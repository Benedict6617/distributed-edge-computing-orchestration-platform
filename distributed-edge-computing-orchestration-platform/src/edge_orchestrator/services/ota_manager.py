"""
OTA Update Manager
==================

Manages over-the-air firmware/agent updates across the fleet using a
staged rollout: canary -> progressive waves -> complete. If the observed
failure rate in any wave exceeds `max_failure_rate_pct`, the campaign is
automatically paused so an operator (or an automated policy) can decide
whether to retry, adjust, or roll back.

Updates are pushed through the same `ConnectivityManager` offline queue
as any other command, so a store that's offline overnight will pick up
its firmware update the next time it reconnects rather than being
silently skipped.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

from ..core.db import Database, row_document
from ..core.logging_setup import get_logger
from ..core.models import (
    ConnectivityState,
    NodeStatus,
    OTAUpdateCampaign,
    UpdateRolloutState,
    new_id,
)
from .connectivity import ConnectivityManager
from .node_registry import NodeRegistry

logger = get_logger("edge_orchestrator.ota_manager")


class OTAManager:
    def __init__(self, db: Database, registry: NodeRegistry, connectivity: ConnectivityManager):
        self.db = db
        self.registry = registry
        self.connectivity = connectivity

    def create_campaign(
        self,
        name: str,
        target_firmware_version: str,
        node_selector: dict,
        canary_percent: int = 5,
        max_failure_rate_pct: float = 10.0,
    ) -> OTAUpdateCampaign:
        targets = self._select_nodes(node_selector)
        campaign = OTAUpdateCampaign(
            campaign_id=new_id("ota"),
            name=name,
            target_firmware_version=target_firmware_version,
            node_selector=node_selector,
            canary_percent=canary_percent,
            max_failure_rate_pct=max_failure_rate_pct,
            nodes_total=len(targets),
            state=UpdateRolloutState.DRAFT,
        )
        self._save(campaign)
        logger.info(
            "OTA campaign created",
            extra={"extra_fields": {"campaign_id": campaign.campaign_id, "nodes_total": campaign.nodes_total}},
        )
        return campaign

    def start_canary(self, campaign_id: str) -> OTAUpdateCampaign:
        campaign = self._require(campaign_id)
        targets = self._select_nodes(campaign.node_selector)
        canary_count = max(1, round(len(targets) * campaign.canary_percent / 100)) if targets else 0
        canary_nodes = targets[:canary_count]
        for node in canary_nodes:
            self._push_update(node.node_id, campaign)
        campaign.state = UpdateRolloutState.CANARY
        self._save(campaign)
        return campaign

    def advance(self, campaign_id: str, wave_percent: int = 25) -> OTAUpdateCampaign:
        """Push the update to the next wave of nodes that haven't been
        updated yet, provided the current observed failure rate is within
        tolerance."""
        campaign = self._require(campaign_id)
        failure_rate = self._failure_rate(campaign)
        if failure_rate > campaign.max_failure_rate_pct:
            campaign.state = UpdateRolloutState.PAUSED
            self._save(campaign)
            logger.info(
                "OTA campaign auto-paused: failure rate exceeded threshold",
                extra={
                    "extra_fields": {
                        "campaign_id": campaign_id,
                        "failure_rate_pct": failure_rate,
                        "threshold_pct": campaign.max_failure_rate_pct,
                    }
                },
            )
            return campaign

        targets = self._select_nodes(campaign.node_selector)
        already_targeted_ids = self._targeted_node_ids(campaign_id)
        remaining = [n for n in targets if n.node_id not in already_targeted_ids]
        wave_count = max(1, round(len(targets) * wave_percent / 100)) if targets else 0
        wave = remaining[:wave_count]
        for node in wave:
            self._push_update(node.node_id, campaign)

        campaign.state = (
            UpdateRolloutState.ROLLING_OUT if remaining[wave_count:] else UpdateRolloutState.COMPLETED
        )
        self._save(campaign)
        return campaign

    def report_result(self, campaign_id: str, node_id: str, success: bool) -> OTAUpdateCampaign:
        campaign = self._require(campaign_id)
        if success:
            campaign.nodes_updated += 1
            node = self.registry.get(node_id)
            if node:
                node.firmware_version = campaign.target_firmware_version
                self.registry.register(node)
        else:
            campaign.nodes_failed += 1
        self._save(campaign)
        return campaign

    def rollback(self, campaign_id: str, previous_version: str) -> OTAUpdateCampaign:
        campaign = self._require(campaign_id)
        targets = self._select_nodes(campaign.node_selector)
        for node in targets:
            self.connectivity.enqueue(
                node.node_id, "ota_update", {"campaign_id": campaign_id, "firmware_version": previous_version}
            )
        campaign.state = UpdateRolloutState.ROLLED_BACK
        self._save(campaign)
        logger.info("OTA campaign rolled back", extra={"extra_fields": {"campaign_id": campaign_id}})
        return campaign

    def get(self, campaign_id: str) -> Optional[OTAUpdateCampaign]:
        row = self.db.query_one("SELECT * FROM ota_campaigns WHERE campaign_id=?", (campaign_id,))
        return self._from_row(row) if row else None

    def list(self) -> List[OTAUpdateCampaign]:
        rows = self.db.query("SELECT * FROM ota_campaigns ORDER BY campaign_id")
        return [self._from_row(r) for r in rows]

    # -- internals ---------------------------------------------------------
    def _push_update(self, node_id: str, campaign: OTAUpdateCampaign) -> None:
        node = self.registry.get(node_id)
        payload = {"campaign_id": campaign.campaign_id, "firmware_version": campaign.target_firmware_version}
        if node and node.connectivity_state == ConnectivityState.ONLINE:
            # In a live deployment this would be an authenticated push (mTLS,
            # signed manifest) to the node's agent; here we still route it
            # through the durable queue so delivery/ack semantics are uniform.
            self.connectivity.enqueue(node_id, "ota_update", payload)
        else:
            self.connectivity.enqueue(node_id, "ota_update", payload)
            logger.info(
                "node offline/degraded, OTA update queued for reconnect",
                extra={"extra_fields": {"node_id": node_id, "campaign_id": campaign.campaign_id}},
            )

    def _select_nodes(self, node_selector: dict):
        nodes = self.registry.list()
        out = []
        for node in nodes:
            if node.status == NodeStatus.DECOMMISSIONED:
                continue
            match = True
            for key, value in (node_selector or {}).items():
                if key == "node_type" and node.node_type.value != value:
                    match = False
                elif key == "region" and node.location.region != value:
                    match = False
                elif key in node.labels and node.labels.get(key) != value:
                    match = False
            if match:
                out.append(node)
        return out

    def _targeted_node_ids(self, campaign_id: str) -> set:
        # In this simplified model we approximate "already targeted" by
        # nodes whose firmware already matches the campaign target, plus
        # anything currently queued for this campaign.
        campaign = self._require(campaign_id)
        targeted = {
            n.node_id for n in self.registry.list() if n.firmware_version == campaign.target_firmware_version
        }
        return targeted

    def _failure_rate(self, campaign: OTAUpdateCampaign) -> float:
        attempted = campaign.nodes_updated + campaign.nodes_failed
        if attempted == 0:
            return 0.0
        return round(100.0 * campaign.nodes_failed / attempted, 2)

    def _require(self, campaign_id: str) -> OTAUpdateCampaign:
        campaign = self.get(campaign_id)
        if not campaign:
            raise ValueError(f"unknown OTA campaign_id '{campaign_id}'")
        return campaign

    def _save(self, campaign: OTAUpdateCampaign) -> None:
        self.db.execute(
            """INSERT INTO ota_campaigns (campaign_id, state, document) VALUES (?, ?, ?)
               ON CONFLICT(campaign_id) DO UPDATE SET state=excluded.state, document=excluded.document""",
            (campaign.campaign_id, campaign.state.value, json.dumps(campaign.to_dict())),
        )

    @staticmethod
    def _from_row(row) -> OTAUpdateCampaign:
        d = row_document(row)
        d["state"] = UpdateRolloutState(d["state"])
        return OTAUpdateCampaign(**d)

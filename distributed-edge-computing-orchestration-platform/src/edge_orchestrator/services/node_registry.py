"""
Node Registry
=============

Tracks every edge node known to the control plane: identity, location,
hardware profile, allocation state, and connectivity. Edge fleets are
defined by *intermittent* connectivity (a retail store's LTE failing over,
a factory floor Wi-Fi AP rebooting, a cell tower losing backhaul), so the
registry derives connectivity state from heartbeat recency rather than
trusting a single "I'm alive" flag -- an agent that stops reporting is
just as significant as one that explicitly reports failure.
"""

from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from ..core.db import Database, row_document
from ..core.logging_setup import get_logger
from ..core.models import ConnectivityState, EdgeNode, NodeStatus

logger = get_logger("edge_orchestrator.node_registry")


class NodeRegistry:
    def __init__(self, db: Database, heartbeat_timeout_s: int = 120, degraded_threshold_s: int = 45):
        self.db = db
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.degraded_threshold_s = degraded_threshold_s

    # -- writes ------------------------------------------------------
    def register(self, node: EdgeNode) -> EdgeNode:
        node.last_heartbeat_ts = time.time()
        self.db.execute(
            """INSERT INTO nodes (node_id, node_type, region, country_code, connectivity_state,
                                   status, last_heartbeat_ts, document)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(node_id) DO UPDATE SET
                 node_type=excluded.node_type, region=excluded.region,
                 country_code=excluded.country_code, connectivity_state=excluded.connectivity_state,
                 status=excluded.status, last_heartbeat_ts=excluded.last_heartbeat_ts,
                 document=excluded.document""",
            (
                node.node_id, node.node_type.value, node.location.region, node.location.country_code,
                node.connectivity_state.value, node.status.value, node.last_heartbeat_ts,
                json.dumps(node.to_dict()),
            ),
        )
        logger.info("node registered", extra={"extra_fields": {"node_id": node.node_id, "node_type": node.node_type.value}})
        return node

    def heartbeat(self, node_id: str, metrics_snapshot: Optional[dict] = None) -> Optional[EdgeNode]:
        node = self.get(node_id)
        if not node:
            return None
        node.last_heartbeat_ts = time.time()
        node.connectivity_state = ConnectivityState.ONLINE
        self._save(node)
        return node

    def set_connectivity(self, node_id: str, state: ConnectivityState) -> Optional[EdgeNode]:
        node = self.get(node_id)
        if not node:
            return None
        node.connectivity_state = state
        self._save(node)
        return node

    def cordon(self, node_id: str) -> Optional[EdgeNode]:
        node = self.get(node_id)
        if not node:
            return None
        node.status = NodeStatus.CORDONED
        self._save(node)
        return node

    def uncordon(self, node_id: str) -> Optional[EdgeNode]:
        node = self.get(node_id)
        if not node:
            return None
        node.status = NodeStatus.READY
        self._save(node)
        return node

    def allocate(self, node_id: str, cpu_millicores: int, memory_mb: int) -> Optional[EdgeNode]:
        node = self.get(node_id)
        if not node:
            return None
        node.allocated_cpu_millicores += cpu_millicores
        node.allocated_memory_mb += memory_mb
        self._save(node)
        return node

    def release(self, node_id: str, cpu_millicores: int, memory_mb: int) -> Optional[EdgeNode]:
        node = self.get(node_id)
        if not node:
            return None
        node.allocated_cpu_millicores = max(0, node.allocated_cpu_millicores - cpu_millicores)
        node.allocated_memory_mb = max(0, node.allocated_memory_mb - memory_mb)
        self._save(node)
        return node

    def _save(self, node: EdgeNode) -> None:
        self.db.execute(
            """UPDATE nodes SET node_type=?, region=?, country_code=?, connectivity_state=?,
                                 status=?, last_heartbeat_ts=?, document=? WHERE node_id=?""",
            (
                node.node_type.value, node.location.region, node.location.country_code,
                node.connectivity_state.value, node.status.value, node.last_heartbeat_ts,
                json.dumps(node.to_dict()), node.node_id,
            ),
        )

    # -- reads ---------------------------------------------------------
    def get(self, node_id: str) -> Optional[EdgeNode]:
        row = self.db.query_one("SELECT * FROM nodes WHERE node_id=?", (node_id,))
        return EdgeNode.from_dict(row_document(row)) if row else None

    def list(
        self,
        region: Optional[str] = None,
        node_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[EdgeNode]:
        clauses, params = [], []
        if region:
            clauses.append("region=?")
            params.append(region)
        if node_type:
            clauses.append("node_type=?")
            params.append(node_type)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(f"SELECT * FROM nodes {where} ORDER BY node_id", params)
        return [EdgeNode.from_dict(row_document(r)) for r in rows]

    def count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) as c FROM nodes")
        return row["c"] if row else 0

    def refresh_connectivity_states(self) -> Dict[str, int]:
        """Reclassify node connectivity purely from heartbeat recency.

        Called periodically (see health_monitor.sweep). Returns a summary
        of transitions for observability.
        """
        now = time.time()
        summary = {"online": 0, "degraded": 0, "offline": 0}
        for node in self.list():
            age = now - node.last_heartbeat_ts
            if age > self.heartbeat_timeout_s:
                new_state = ConnectivityState.OFFLINE
            elif age > self.degraded_threshold_s:
                new_state = ConnectivityState.DEGRADED
            else:
                new_state = ConnectivityState.ONLINE
            if new_state != node.connectivity_state:
                node.connectivity_state = new_state
                self._save(node)
            summary[new_state.value] += 1
        return summary

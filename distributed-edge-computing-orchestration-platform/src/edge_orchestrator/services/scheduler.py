"""
Scheduler
=========

Placement engine responsible for deciding *which* edge node(s) a workload's
replicas should run on. The pipeline for each replica is:

  1. Filter        -- resource fit, connectivity, node-type affinity, compliance
  2. Score          -- latency vs cost (CostOptimizer), spread across existing replicas
  3. Bind           -- allocate resources on the winning node, persist a Deployment
  4. Fallback       -- if no edge node qualifies, recommend a hybrid-cloud burst

This mirrors a Kubernetes-style filter/score/bind scheduling loop, scoped
to the constraints that matter for edge fleets: intermittent connectivity,
physical distance to the data source, and data-sovereignty boundaries.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..core.db import Database, row_document
from ..core.logging_setup import get_logger
from ..core.models import (
    ConnectivityState,
    Deployment,
    DeploymentState,
    EdgeNode,
    NodeStatus,
    Workload,
    new_id,
)
from .compliance_engine import ComplianceEngine
from .cost_optimizer import CostOptimizer
from .node_registry import NodeRegistry

logger = get_logger("edge_orchestrator.scheduler")


@dataclass
class SchedulingDecision:
    workload_id: str
    deployments: List[Deployment]
    unplaced_replicas: int
    cloud_burst_recommendation: Optional[str] = None


class SchedulingError(Exception):
    pass


class Scheduler:
    def __init__(
        self,
        db: Database,
        registry: NodeRegistry,
        compliance: ComplianceEngine,
        cost_optimizer: CostOptimizer,
    ):
        self.db = db
        self.registry = registry
        self.compliance = compliance
        self.cost_optimizer = cost_optimizer

    # -- public API ------------------------------------------------------
    def schedule(self, workload: Workload, origin_lat: float, origin_lon: float) -> SchedulingDecision:
        deployments: List[Deployment] = []
        used_node_ids: List[str] = []

        for replica_index in range(workload.replicas):
            candidates = self._filter_nodes(workload, exclude=used_node_ids)
            if not candidates:
                break
            scored = self._score_nodes(workload, candidates, origin_lat, origin_lon)
            if not scored:
                break
            winner, score = max(scored, key=lambda pair: pair[1].score)
            deployment = self._bind(workload, winner, score)
            deployments.append(deployment)
            used_node_ids.append(winner.node_id)

        unplaced = workload.replicas - len(deployments)
        recommendation = None
        if unplaced > 0:
            all_candidates = self._filter_nodes(workload, exclude=[])
            scored_all = self._score_nodes(workload, all_candidates, origin_lat, origin_lon)
            recommendation = self.cost_optimizer.recommend_cloud_burst(
                [s for _, s in scored_all], workload, edge_saturated=(len(all_candidates) == 0)
            )
            logger.info(
                "workload has unplaced replicas",
                extra={"extra_fields": {"workload_id": workload.workload_id, "unplaced": unplaced}},
            )

        return SchedulingDecision(
            workload_id=workload.workload_id,
            deployments=deployments,
            unplaced_replicas=unplaced,
            cloud_burst_recommendation=recommendation,
        )

    def deployments_for_workload(self, workload_id: str) -> List[Deployment]:
        rows = self.db.query(
            "SELECT * FROM deployments WHERE workload_id=? ORDER BY deployment_id", (workload_id,)
        )
        return [self._deployment_from_row(r) for r in rows]

    def all_deployments(self) -> List[Deployment]:
        rows = self.db.query("SELECT * FROM deployments ORDER BY deployment_id")
        return [self._deployment_from_row(r) for r in rows]

    def stop_deployment(self, deployment_id: str) -> Optional[Deployment]:
        row = self.db.query_one("SELECT * FROM deployments WHERE deployment_id=?", (deployment_id,))
        if not row:
            return None
        deployment = self._deployment_from_row(row)
        deployment.state = DeploymentState.STOPPED
        deployment.updated_ts = time.time()
        self._save_deployment(deployment)

        wl_row = self.db.query_one("SELECT * FROM workloads WHERE workload_id=?", (deployment.workload_id,))
        if wl_row:
            workload = Workload.from_dict(row_document(wl_row))
            self.registry.release(
                deployment.node_id, workload.resources.cpu_millicores, workload.resources.memory_mb
            )
        return deployment

    # -- pipeline stages ---------------------------------------------------
    def _filter_nodes(self, workload: Workload, exclude: List[str]) -> List[EdgeNode]:
        nodes = self.registry.list()
        out = []
        for node in nodes:
            if node.node_id in exclude:
                continue
            if node.status != NodeStatus.READY:
                continue
            if node.connectivity_state == ConnectivityState.OFFLINE:
                continue
            if node.available_cpu_millicores < workload.resources.cpu_millicores:
                continue
            if node.available_memory_mb < workload.resources.memory_mb:
                continue
            if workload.node_type_affinity and node.node_type.value not in workload.node_type_affinity:
                continue
            out.append(node)

        return self.compliance.eligible_nodes(workload, out)

    def _score_nodes(
        self, workload: Workload, candidates: List[EdgeNode], origin_lat: float, origin_lon: float
    ):
        if not candidates:
            return []
        prelim = []
        for node in candidates:
            from .cost_optimizer import estimate_latency_ms, haversine_km

            dist = haversine_km(origin_lat, origin_lon, node.location.latitude, node.location.longitude)
            prelim.append((node, estimate_latency_ms(dist, node.connection_type), node.cost_per_hour_usd))

        max_latency = max((p[1] for p in prelim), default=1.0) or 1.0
        max_cost = max((p[2] for p in prelim), default=1.0) or 1.0

        scored = []
        for node in candidates:
            score = self.cost_optimizer.score_node(
                node, workload, origin_lat, origin_lon, max_latency, max_cost
            )
            scored.append((node, score))
        return scored

    def _bind(self, workload: Workload, node: EdgeNode, score) -> Deployment:
        self.registry.allocate(node.node_id, workload.resources.cpu_millicores, workload.resources.memory_mb)
        deployment = Deployment(
            deployment_id=new_id("dep"),
            workload_id=workload.workload_id,
            node_id=node.node_id,
            state=DeploymentState.SCHEDULED,
            placement_score=score.score,
            placement_reason=(
                f"latency~{score.latency_ms}ms, cost=${score.cost_per_hour_usd}/hr, "
                f"utilization={score.utilization_pct}% (score={score.score})"
            ),
            started_ts=time.time(),
        )
        self._save_deployment(deployment)
        logger.info(
            "workload replica scheduled",
            extra={
                "extra_fields": {
                    "workload_id": workload.workload_id,
                    "node_id": node.node_id,
                    "score": score.score,
                }
            },
        )
        return deployment

    def _save_deployment(self, deployment: Deployment) -> None:
        self.db.execute(
            """INSERT INTO deployments (deployment_id, workload_id, node_id, state, document)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(deployment_id) DO UPDATE SET
                 state=excluded.state, document=excluded.document""",
            (
                deployment.deployment_id, deployment.workload_id, deployment.node_id,
                deployment.state.value, json.dumps(deployment.to_dict()),
            ),
        )

    @staticmethod
    def _deployment_from_row(row) -> Deployment:
        d = row_document(row)
        d["state"] = DeploymentState(d["state"])
        return Deployment(**d)

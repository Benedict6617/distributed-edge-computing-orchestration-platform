"""
Health Monitor
==============

Ingests hardware health telemetry (CPU, memory, disk, temperature, uptime)
reported by edge agents, applies threshold-based anomaly detection, and
raises alerts. Also periodically sweeps the node registry to reclassify
connectivity state from heartbeat recency (see NodeRegistry.refresh_connectivity_states).

Metrics are capped per node (default 500 most recent samples) rather than
retained forever -- this control plane is an operational store, not a
long-term time-series database. Export to Prometheus/Grafana, Datadog, or
a data lake for long-term retention (see docs/DEPLOYMENT.md).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..core.db import Database, row_document
from ..core.logging_setup import get_logger
from ..core.models import HealthMetric
from .node_registry import NodeRegistry

logger = get_logger("edge_orchestrator.health_monitor")

MAX_SAMPLES_PER_NODE = 500

# Conservative default thresholds; override per-fleet in production.
THRESHOLDS = {
    "cpu_usage_pct": 90.0,
    "memory_usage_pct": 90.0,
    "disk_usage_pct": 85.0,
    "temperature_c": 75.0,
}


@dataclass
class Alert:
    node_id: str
    metric: str
    value: float
    threshold: float
    severity: str
    timestamp: float


class HealthMonitor:
    def __init__(self, db: Database, registry: NodeRegistry):
        self.db = db
        self.registry = registry
        self._alerts: List[Alert] = []

    def ingest(self, metric: HealthMetric) -> List[Alert]:
        self.db.execute(
            "INSERT INTO health_metrics (node_id, timestamp, document) VALUES (?, ?, ?)",
            (metric.node_id, metric.timestamp, json.dumps(metric.to_dict())),
        )
        self._trim(metric.node_id)
        self.registry.heartbeat(metric.node_id)
        alerts = self._evaluate(metric)
        self._alerts.extend(alerts)
        self._alerts = self._alerts[-1000:]
        return alerts

    def latest(self, node_id: str) -> Optional[HealthMetric]:
        row = self.db.query_one(
            "SELECT * FROM health_metrics WHERE node_id=? ORDER BY timestamp DESC LIMIT 1", (node_id,)
        )
        return HealthMetric(**row_document(row)) if row else None

    def history(self, node_id: str, limit: int = 100) -> List[HealthMetric]:
        rows = self.db.query(
            "SELECT * FROM health_metrics WHERE node_id=? ORDER BY timestamp DESC LIMIT ?", (node_id, limit)
        )
        return [HealthMetric(**row_document(r)) for r in rows]

    def fleet_snapshot(self) -> Dict[str, dict]:
        snapshot = {}
        for node in self.registry.list():
            latest = self.latest(node.node_id)
            snapshot[node.node_id] = {
                "connectivity_state": node.connectivity_state.value,
                "status": node.status.value,
                "latest_metric": latest.to_dict() if latest else None,
            }
        return snapshot

    def recent_alerts(self, limit: int = 50) -> List[dict]:
        return [a.__dict__ for a in self._alerts[-limit:]]

    def sweep_connectivity(self) -> Dict[str, int]:
        return self.registry.refresh_connectivity_states()

    # -- internals -----------------------------------------------------
    def _evaluate(self, metric: HealthMetric) -> List[Alert]:
        alerts = []
        checks = {
            "cpu_usage_pct": metric.cpu_usage_pct,
            "memory_usage_pct": metric.memory_usage_pct,
            "disk_usage_pct": metric.disk_usage_pct,
            "temperature_c": metric.temperature_c,
        }
        for key, value in checks.items():
            threshold = THRESHOLDS[key]
            if value >= threshold:
                severity = "critical" if value >= threshold * 1.05 else "warning"
                alert = Alert(
                    node_id=metric.node_id,
                    metric=key,
                    value=value,
                    threshold=threshold,
                    severity=severity,
                    timestamp=metric.timestamp,
                )
                alerts.append(alert)
                logger.info(
                    "health alert raised",
                    extra={"extra_fields": {"node_id": metric.node_id, "metric": key, "value": value, "severity": severity}},
                )
        return alerts

    def _trim(self, node_id: str) -> None:
        row = self.db.query_one(
            "SELECT COUNT(*) as c FROM health_metrics WHERE node_id=?", (node_id,)
        )
        count = row["c"] if row else 0
        if count > MAX_SAMPLES_PER_NODE:
            excess = count - MAX_SAMPLES_PER_NODE
            self.db.execute(
                """DELETE FROM health_metrics WHERE id IN (
                     SELECT id FROM health_metrics WHERE node_id=? ORDER BY timestamp ASC LIMIT ?
                   )""",
                (node_id, excess),
            )

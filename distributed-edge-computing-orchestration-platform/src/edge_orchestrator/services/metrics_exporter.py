"""
Metrics Exporter
================

Hand-rolled Prometheus text-exposition-format writer. Avoids a dependency
on `prometheus_client` while still being scrapeable by any Prometheus-
compatible collector (Prometheus itself, Grafana Agent, VictoriaMetrics,
Datadog's OpenMetrics integration, etc).

Exposed series give an at-a-glance view of fleet health -- exactly the
"observability across distributed infrastructure" a small operations team
needs on a single dashboard.
"""

from __future__ import annotations

from typing import List

from ..core.models import ConnectivityState
from .health_monitor import HealthMonitor
from .node_registry import NodeRegistry
from .ota_manager import OTAManager
from .scheduler import Scheduler


def _line(name: str, help_text: str, metric_type: str, samples: List[str]) -> str:
    out = [f"# HELP {name} {help_text}", f"# TYPE {name} {metric_type}"]
    out.extend(samples)
    return "\n".join(out)


def render_prometheus_metrics(
    registry: NodeRegistry, health: HealthMonitor, scheduler: Scheduler, ota: OTAManager
) -> str:
    blocks = []

    nodes = registry.list()
    by_state = {"online": 0, "degraded": 0, "offline": 0}
    by_type: dict = {}
    for n in nodes:
        by_state[n.connectivity_state.value] += 1
        by_type[n.node_type.value] = by_type.get(n.node_type.value, 0) + 1

    samples = [f'edge_orchestrator_nodes_total{{connectivity="{k}"}} {v}' for k, v in by_state.items()]
    blocks.append(_line("edge_orchestrator_nodes_total", "Number of registered edge nodes by connectivity state", "gauge", samples))

    samples = [f'edge_orchestrator_nodes_by_type{{node_type="{k}"}} {v}' for k, v in by_type.items()]
    blocks.append(_line("edge_orchestrator_nodes_by_type", "Number of registered edge nodes by type", "gauge", samples))

    deployments = scheduler.all_deployments()
    dep_states: dict = {}
    for d in deployments:
        dep_states[d.state.value] = dep_states.get(d.state.value, 0) + 1
    samples = [f'edge_orchestrator_deployments_total{{state="{k}"}} {v}' for k, v in dep_states.items()]
    blocks.append(_line("edge_orchestrator_deployments_total", "Number of workload deployments by state", "gauge", samples))

    total_cpu = sum(n.hardware.cpu_cores * 1000 for n in nodes)
    used_cpu = sum(n.allocated_cpu_millicores for n in nodes)
    cpu_pct = round(100.0 * used_cpu / total_cpu, 2) if total_cpu else 0.0
    blocks.append(
        _line(
            "edge_orchestrator_fleet_cpu_utilization_pct",
            "Aggregate fleet CPU allocation percentage",
            "gauge",
            [f"edge_orchestrator_fleet_cpu_utilization_pct {cpu_pct}"],
        )
    )

    campaigns = ota.list()
    samples = [f'edge_orchestrator_ota_campaign_progress{{campaign_id="{c.campaign_id}",state="{c.state.value}"}} {c.nodes_updated}' for c in campaigns]
    if samples:
        blocks.append(_line("edge_orchestrator_ota_campaign_progress", "Nodes successfully updated per OTA campaign", "gauge", samples))

    return "\n\n".join(blocks) + "\n"

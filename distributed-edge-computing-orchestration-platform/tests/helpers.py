"""Shared helpers for building an in-memory Container and sample domain
objects in tests, without touching the filesystem."""

from __future__ import annotations

from edge_orchestrator.container import Container
from edge_orchestrator.core.config import Settings
from edge_orchestrator.core.models import (
    EdgeNode,
    GeoLocation,
    HardwareProfile,
    NodeType,
    ResourceRequest,
    Workload,
)


def make_container(**overrides) -> Container:
    settings = Settings(
        host="127.0.0.1",
        port=0,
        db_path=":memory:",
        log_level="CRITICAL",
        heartbeat_timeout_s=overrides.get("heartbeat_timeout_s", 120),
        degraded_threshold_s=overrides.get("degraded_threshold_s", 45),
        scheduler_latency_weight=overrides.get("scheduler_latency_weight", 0.6),
        max_offline_queue_size=overrides.get("max_offline_queue_size", 500),
        default_ota_max_failure_rate_pct=overrides.get("default_ota_max_failure_rate_pct", 10.0),
    )
    return Container(settings=settings, db_path=":memory:")


def make_node(
    node_id: str,
    node_type: NodeType = NodeType.RETAIL_STORE,
    lat: float = 41.8781,
    lon: float = -87.6298,
    region: str = "us-east",
    country: str = "US",
    cpu_cores: int = 4,
    memory_mb: int = 8192,
    cost_per_hour_usd: float = 0.01,
    connection_type: str = "5g",
) -> EdgeNode:
    return EdgeNode(
        node_id=node_id,
        name=f"Test node {node_id}",
        node_type=node_type,
        location=GeoLocation(latitude=lat, longitude=lon, region=region, country_code=country),
        hardware=HardwareProfile(cpu_cores=cpu_cores, memory_mb=memory_mb, storage_gb=64),
        connection_type=connection_type,
        cost_per_hour_usd=cost_per_hour_usd,
    )


def make_workload(
    workload_id: str = "wl-test",
    cpu_millicores: int = 500,
    memory_mb: int = 512,
    replicas: int = 1,
    latency_sensitivity_ms: int = 500,
    data_residency_region=None,
    node_type_affinity=None,
    cost_priority: str = "balanced",
) -> Workload:
    return Workload(
        workload_id=workload_id,
        name=workload_id,
        container_image="registry.internal/example:1.0.0",
        resources=ResourceRequest(cpu_millicores=cpu_millicores, memory_mb=memory_mb),
        replicas=replicas,
        latency_sensitivity_ms=latency_sensitivity_ms,
        data_residency_region=data_residency_region,
        node_type_affinity=node_type_affinity,
        cost_priority=cost_priority,
    )

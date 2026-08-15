"""
Domain models shared across the orchestrator.

These are intentionally plain `dataclasses` (no ORM, no pydantic) so the
project has zero third-party dependencies. `to_dict` / `from_dict` helpers
keep JSON (de)serialization explicit and predictable, which matters for a
control plane whose wire format is a public contract for edge agents.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def now_ts() -> float:
    return time.time()


class NodeType(str, Enum):
    CELL_TOWER = "cell_tower"
    RETAIL_STORE = "retail_store"
    FACTORY = "factory"
    WAREHOUSE = "warehouse"
    KIOSK = "kiosk"
    VEHICLE = "vehicle"


class ConnectivityState(str, Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class NodeStatus(str, Enum):
    PROVISIONING = "provisioning"
    READY = "ready"
    CORDONED = "cordoned"          # not accepting new workloads
    DRAINING = "draining"
    DECOMMISSIONED = "decommissioned"


class DeploymentState(str, Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPED = "stopped"


class UpdateRolloutState(str, Enum):
    DRAFT = "draft"
    CANARY = "canary"
    ROLLING_OUT = "rolling_out"
    PAUSED = "paused"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"


class SliceProfile(str, Enum):
    """3GPP-aligned 5G network slice archetypes."""
    EMBB = "eMBB"     # Enhanced Mobile Broadband - high throughput
    URLLC = "URLLC"   # Ultra-Reliable Low-Latency Communication
    MMTC = "mMTC"     # Massive Machine-Type Communication - many low-rate devices


@dataclass
class HardwareProfile:
    cpu_cores: int
    memory_mb: int
    storage_gb: int
    gpu: bool = False
    architecture: str = "arm64"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GeoLocation:
    latitude: float
    longitude: float
    region: str          # cloud-provider-style region, e.g. "us-east"
    country_code: str    # ISO-3166 alpha-2, used by the compliance engine

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EdgeNode:
    node_id: str
    name: str
    node_type: NodeType
    location: GeoLocation
    hardware: HardwareProfile
    connectivity_state: ConnectivityState = ConnectivityState.ONLINE
    status: NodeStatus = NodeStatus.READY
    connection_type: str = "5g"          # 5g | 4g-lte | wifi | wired | satellite
    labels: Dict[str, str] = field(default_factory=dict)
    allocated_cpu_millicores: int = 0
    allocated_memory_mb: int = 0
    firmware_version: str = "1.0.0"
    last_heartbeat_ts: float = field(default_factory=now_ts)
    cost_per_hour_usd: float = 0.0
    registered_ts: float = field(default_factory=now_ts)

    @property
    def available_cpu_millicores(self) -> int:
        return self.hardware.cpu_cores * 1000 - self.allocated_cpu_millicores

    @property
    def available_memory_mb(self) -> int:
        return self.hardware.memory_mb - self.allocated_memory_mb

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["node_type"] = self.node_type.value
        d["connectivity_state"] = self.connectivity_state.value
        d["status"] = self.status.value
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "EdgeNode":
        d = dict(d)
        d["node_type"] = NodeType(d["node_type"])
        d["connectivity_state"] = ConnectivityState(d.get("connectivity_state", "online"))
        d["status"] = NodeStatus(d.get("status", "ready"))
        d["location"] = GeoLocation(**d["location"]) if isinstance(d["location"], dict) else d["location"]
        d["hardware"] = HardwareProfile(**d["hardware"]) if isinstance(d["hardware"], dict) else d["hardware"]
        return EdgeNode(**d)


@dataclass
class ResourceRequest:
    cpu_millicores: int
    memory_mb: int
    storage_gb: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Workload:
    workload_id: str
    name: str
    container_image: str
    resources: ResourceRequest
    replicas: int = 1
    latency_sensitivity_ms: int = 500     # max tolerable RTT to data source
    data_residency_region: Optional[str] = None   # e.g. "eu" -> must stay in EU
    node_type_affinity: Optional[List[str]] = None
    slice_profile: Optional[SliceProfile] = None
    cost_priority: str = "balanced"       # "latency" | "cost" | "balanced"
    env: Dict[str, str] = field(default_factory=dict)
    created_ts: float = field(default_factory=now_ts)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.slice_profile:
            d["slice_profile"] = self.slice_profile.value
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Workload":
        d = dict(d)
        d["resources"] = ResourceRequest(**d["resources"]) if isinstance(d["resources"], dict) else d["resources"]
        if d.get("slice_profile"):
            d["slice_profile"] = SliceProfile(d["slice_profile"])
        return Workload(**d)


@dataclass
class Deployment:
    deployment_id: str
    workload_id: str
    node_id: str
    state: DeploymentState = DeploymentState.PENDING
    placement_score: float = 0.0
    placement_reason: str = ""
    started_ts: Optional[float] = None
    updated_ts: float = field(default_factory=now_ts)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


@dataclass
class HealthMetric:
    node_id: str
    cpu_usage_pct: float
    memory_usage_pct: float
    disk_usage_pct: float
    temperature_c: float
    uptime_s: int
    timestamp: float = field(default_factory=now_ts)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OTAUpdateCampaign:
    campaign_id: str
    name: str
    target_firmware_version: str
    node_selector: Dict[str, str]         # label selector, e.g. {"node_type": "retail_store"}
    state: UpdateRolloutState = UpdateRolloutState.DRAFT
    canary_percent: int = 5
    max_failure_rate_pct: float = 10.0
    nodes_total: int = 0
    nodes_updated: int = 0
    nodes_failed: int = 0
    created_ts: float = field(default_factory=now_ts)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d

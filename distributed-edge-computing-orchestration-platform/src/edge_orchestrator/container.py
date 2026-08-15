"""
Composition root. Builds a fully-wired `Container` of services from a
single `Database` instance so the API server, CLI, and tests all
construct the system the same way.
"""

from __future__ import annotations

from pathlib import Path

from .core.config import Settings, SETTINGS
from .core.db import Database
from .services.compliance_engine import ComplianceEngine
from .services.connectivity import ConnectivityManager
from .services.cost_optimizer import CostOptimizer
from .services.health_monitor import HealthMonitor
from .services.hybrid_cloud import HybridCloudManager
from .services.network_slicing import NetworkSliceManager
from .services.node_registry import NodeRegistry
from .services.ota_manager import OTAManager
from .services.scheduler import Scheduler
from .services.service_mesh import ServiceMeshManager
from .services.workload_manager import WorkloadManager

_DEFAULT_COMPLIANCE_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "regions_compliance.json"


class Container:
    def __init__(self, settings: Settings = None, db_path: str = None, compliance_rules_path: str = None):
        self.settings = settings or SETTINGS
        self.db = Database(db_path or self.settings.db_path)

        rules_path = compliance_rules_path or str(_DEFAULT_COMPLIANCE_RULES_PATH)
        try:
            region_rules = ComplianceEngine.load_rules_from_file(rules_path)
        except (FileNotFoundError, OSError):
            region_rules = {}

        self.registry = NodeRegistry(
            self.db,
            heartbeat_timeout_s=self.settings.heartbeat_timeout_s,
            degraded_threshold_s=self.settings.degraded_threshold_s,
        )
        self.connectivity = ConnectivityManager(max_queue_size=self.settings.max_offline_queue_size)
        self.compliance = ComplianceEngine(self.db, region_rules)
        self.cost_optimizer = CostOptimizer(latency_weight=self.settings.scheduler_latency_weight)
        self.scheduler = Scheduler(self.db, self.registry, self.compliance, self.cost_optimizer)
        self.workloads = WorkloadManager(self.db, self.scheduler)
        self.health = HealthMonitor(self.db, self.registry)
        self.ota = OTAManager(self.db, self.registry, self.connectivity)
        self.slices = NetworkSliceManager(self.db)
        self.mesh = ServiceMeshManager(self.db)
        self.hybrid_cloud = HybridCloudManager()

    def close(self) -> None:
        self.db.close()

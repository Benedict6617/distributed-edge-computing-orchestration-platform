"""
Hybrid Cloud Adapters
=======================

Defines a pluggable `CloudProviderAdapter` interface so the orchestrator
can burst workloads into public cloud capacity when edge headroom is
insufficient (see CostOptimizer.recommend_cloud_burst). Ships with mock
AWS / Azure / GCP adapters that model realistic region catalogs and
per-hour pricing so the rest of the system (scheduler, cost optimizer,
compliance engine) can be exercised end-to-end without live cloud
credentials.

Wiring a real provider means implementing this interface against the
provider's actual SDK (boto3, azure-mgmt-*, google-cloud-*) -- intentionally
kept out of this repo's dependency set so the control plane itself stays
zero-dependency; adapters are the one place a production deployment is
expected to add provider SDKs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Protocol


@dataclass
class CloudRegion:
    provider: str
    region_code: str
    country_code: str
    on_demand_cost_per_vcpu_hr: float


@dataclass
class BurstInstance:
    instance_id: str
    provider: str
    region_code: str
    workload_id: str
    launched_ts: float


class CloudProviderAdapter(Protocol):
    name: str

    def list_regions(self) -> List[CloudRegion]: ...
    def launch_instance(self, region_code: str, workload_id: str, cpu_millicores: int, memory_mb: int) -> BurstInstance: ...
    def terminate_instance(self, instance_id: str) -> bool: ...


class _MockCloudAdapter:
    """Shared implementation for the mock provider adapters below. Models
    enough realism (multiple regions, differentiated pricing) to make cost
    comparisons against edge nodes meaningful in demos and tests."""

    name = "mock"
    _regions: List[CloudRegion] = []

    def __init__(self):
        self._instances: Dict[str, BurstInstance] = {}

    def list_regions(self) -> List[CloudRegion]:
        return list(self._regions)

    def launch_instance(self, region_code: str, workload_id: str, cpu_millicores: int, memory_mb: int) -> BurstInstance:
        instance_id = f"{self.name}-{region_code}-{int(time.time() * 1000) % 1_000_000}"
        instance = BurstInstance(
            instance_id=instance_id,
            provider=self.name,
            region_code=region_code,
            workload_id=workload_id,
            launched_ts=time.time(),
        )
        self._instances[instance_id] = instance
        return instance

    def terminate_instance(self, instance_id: str) -> bool:
        return self._instances.pop(instance_id, None) is not None


class AWSAdapter(_MockCloudAdapter):
    name = "aws"
    _regions = [
        CloudRegion("aws", "us-east-1", "US", 0.042),
        CloudRegion("aws", "us-west-2", "US", 0.045),
        CloudRegion("aws", "eu-west-1", "IE", 0.048),
        CloudRegion("aws", "eu-central-1", "DE", 0.049),
        CloudRegion("aws", "ap-southeast-1", "SG", 0.052),
    ]


class AzureAdapter(_MockCloudAdapter):
    name = "azure"
    _regions = [
        CloudRegion("azure", "eastus", "US", 0.041),
        CloudRegion("azure", "westeurope", "NL", 0.050),
        CloudRegion("azure", "germanywestcentral", "DE", 0.051),
        CloudRegion("azure", "southeastasia", "SG", 0.053),
    ]


class GCPAdapter(_MockCloudAdapter):
    name = "gcp"
    _regions = [
        CloudRegion("gcp", "us-central1", "US", 0.040),
        CloudRegion("gcp", "europe-west1", "BE", 0.047),
        CloudRegion("gcp", "asia-southeast1", "SG", 0.051),
    ]


class HybridCloudManager:
    """Aggregates provider adapters and answers "cheapest compliant region"
    style questions used when the scheduler recommends a cloud burst."""

    def __init__(self, adapters: Dict[str, _MockCloudAdapter] = None):
        self.adapters = adapters or {"aws": AWSAdapter(), "azure": AzureAdapter(), "gcp": GCPAdapter()}

    def all_regions(self) -> List[CloudRegion]:
        out = []
        for adapter in self.adapters.values():
            out.extend(adapter.list_regions())
        return out

    def cheapest_region_in_countries(self, allowed_country_codes: List[str]) -> CloudRegion:
        candidates = [r for r in self.all_regions() if r.country_code in allowed_country_codes]
        if not candidates:
            candidates = self.all_regions()
        return min(candidates, key=lambda r: r.on_demand_cost_per_vcpu_hr)

    def burst(self, provider: str, region_code: str, workload_id: str, cpu_millicores: int, memory_mb: int) -> BurstInstance:
        adapter = self.adapters[provider]
        return adapter.launch_instance(region_code, workload_id, cpu_millicores, memory_mb)

"""
5G Network Slicing Manager
===========================

Models 3GPP-aligned network slice profiles (eMBB, URLLC, mMTC) and binds
workloads to a slice so downstream infrastructure (a 5G core's Network
Slice Selection Function in a real deployment) can enforce the associated
QoS envelope end-to-end.

This module does not implement a 3GPP core network -- it implements the
orchestrator-side contract: slice definitions, QoS SLA parameters, and a
validator that checks whether a scheduled deployment's observed/estimated
latency is compatible with the slice it claims to use. That validation is
exactly the signal an SRE needs to know whether a "URLLC" workload is
actually getting URLLC-grade treatment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from ..core.db import Database, row_document
from ..core.logging_setup import get_logger
from ..core.models import SliceProfile, new_id

logger = get_logger("edge_orchestrator.network_slicing")


@dataclass
class SliceQoS:
    max_latency_ms: float
    min_throughput_mbps: float
    min_reliability_pct: float          # e.g. 99.999 for URLLC
    max_connection_density_per_km2: Optional[int] = None


DEFAULT_QOS: Dict[SliceProfile, SliceQoS] = {
    SliceProfile.EMBB: SliceQoS(max_latency_ms=50.0, min_throughput_mbps=100.0, min_reliability_pct=99.0),
    SliceProfile.URLLC: SliceQoS(max_latency_ms=5.0, min_throughput_mbps=10.0, min_reliability_pct=99.999),
    SliceProfile.MMTC: SliceQoS(
        max_latency_ms=500.0, min_throughput_mbps=1.0, min_reliability_pct=98.0, max_connection_density_per_km2=100000
    ),
}


@dataclass
class NetworkSlice:
    slice_id: str
    name: str
    profile: SliceProfile
    qos: SliceQoS
    tenant: str = "default"

    def to_dict(self) -> dict:
        d = {
            "slice_id": self.slice_id,
            "name": self.name,
            "profile": self.profile.value,
            "qos": asdict(self.qos),
            "tenant": self.tenant,
        }
        return d

    @staticmethod
    def from_dict(d: dict) -> "NetworkSlice":
        return NetworkSlice(
            slice_id=d["slice_id"],
            name=d["name"],
            profile=SliceProfile(d["profile"]),
            qos=SliceQoS(**d["qos"]),
            tenant=d.get("tenant", "default"),
        )


class NetworkSliceManager:
    def __init__(self, db: Database):
        self.db = db

    def create_slice(self, name: str, profile: SliceProfile, tenant: str = "default", qos_overrides: Optional[dict] = None) -> NetworkSlice:
        qos = DEFAULT_QOS[profile]
        if qos_overrides:
            qos = SliceQoS(**{**asdict(qos), **qos_overrides})
        ns = NetworkSlice(slice_id=new_id("slice"), name=name, profile=profile, qos=qos, tenant=tenant)
        self._save(ns)
        logger.info("network slice created", extra={"extra_fields": {"slice_id": ns.slice_id, "profile": profile.value}})
        return ns

    def get(self, slice_id: str) -> Optional[NetworkSlice]:
        row = self.db.query_one("SELECT * FROM network_slices WHERE slice_id=?", (slice_id,))
        return NetworkSlice.from_dict(row_document(row)) if row else None

    def list(self) -> List[NetworkSlice]:
        rows = self.db.query("SELECT * FROM network_slices ORDER BY slice_id")
        return [NetworkSlice.from_dict(row_document(r)) for r in rows]

    def validate_sla(self, slice_id: str, observed_latency_ms: float, observed_throughput_mbps: float) -> dict:
        ns = self.get(slice_id)
        if not ns:
            return {"valid": False, "reason": f"unknown slice '{slice_id}'"}
        latency_ok = observed_latency_ms <= ns.qos.max_latency_ms
        throughput_ok = observed_throughput_mbps >= ns.qos.min_throughput_mbps
        return {
            "valid": latency_ok and throughput_ok,
            "slice_id": slice_id,
            "profile": ns.profile.value,
            "latency_ok": latency_ok,
            "throughput_ok": throughput_ok,
            "observed_latency_ms": observed_latency_ms,
            "sla_max_latency_ms": ns.qos.max_latency_ms,
            "observed_throughput_mbps": observed_throughput_mbps,
            "sla_min_throughput_mbps": ns.qos.min_throughput_mbps,
        }

    def _save(self, ns: NetworkSlice) -> None:
        self.db.execute(
            "INSERT INTO network_slices (slice_id, document) VALUES (?, ?) "
            "ON CONFLICT(slice_id) DO UPDATE SET document=excluded.document",
            (ns.slice_id, json.dumps(ns.to_dict())),
        )

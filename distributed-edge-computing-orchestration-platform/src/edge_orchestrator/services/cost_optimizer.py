"""
Cost Optimizer
==============

Provides the scoring primitives the scheduler uses to trade latency for
cost (and vice versa), plus fleet-level recommendations for when a
workload should burst from resource-constrained edge nodes to hybrid
cloud capacity.

The scoring is deliberately simple and explainable (linear weighted sum)
rather than a black-box model -- operators need to be able to answer "why
did workload X land on node Y" in plain language, especially once
compliance and cost are both in play.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from ..core.models import EdgeNode, Workload

# Very rough terrestrial propagation + processing estimate used purely as a
# *relative* signal between candidate nodes, not a claim of physical
# accuracy. Real deployments should replace this with measured RTT from a
# latency probe mesh (see docs/ARCHITECTURE.md).
_SPEED_OF_LIGHT_FIBER_KM_PER_MS = 200.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def estimate_latency_ms(distance_km: float, connection_type: str) -> float:
    base = distance_km / _SPEED_OF_LIGHT_FIBER_KM_PER_MS
    overhead = {
        "wired": 2.0,
        "wifi": 5.0,
        "5g": 4.0,
        "4g-lte": 15.0,
        "satellite": 45.0,
    }.get(connection_type, 10.0)
    return round(base + overhead, 2)


@dataclass
class CandidateScore:
    node_id: str
    latency_ms: float
    cost_per_hour_usd: float
    utilization_pct: float
    score: float                 # higher is better
    breakdown: dict


class CostOptimizer:
    def __init__(self, latency_weight: float = 0.6):
        self.latency_weight = max(0.0, min(1.0, latency_weight))
        self.cost_weight = 1.0 - self.latency_weight

    def score_node(
        self,
        node: EdgeNode,
        workload: Workload,
        origin_lat: float,
        origin_lon: float,
        max_latency_in_pool_ms: float,
        max_cost_in_pool_usd: float,
    ) -> CandidateScore:
        distance_km = haversine_km(origin_lat, origin_lon, node.location.latitude, node.location.longitude)
        latency_ms = estimate_latency_ms(distance_km, node.connection_type)

        utilization_pct = 0.0
        total_millicores = node.hardware.cpu_cores * 1000
        if total_millicores > 0:
            utilization_pct = 100.0 * node.allocated_cpu_millicores / total_millicores

        # Normalize against the candidate pool so weights behave consistently
        # regardless of absolute latency/cost magnitudes.
        norm_latency = latency_ms / max_latency_in_pool_ms if max_latency_in_pool_ms else 0.0
        norm_cost = (
            node.cost_per_hour_usd / max_cost_in_pool_usd if max_cost_in_pool_usd else 0.0
        )

        # Weight adjustment: a workload can override the fleet-default
        # latency/cost balance via `cost_priority`.
        latency_weight, cost_weight = self.latency_weight, self.cost_weight
        if workload.cost_priority == "latency":
            latency_weight, cost_weight = 0.85, 0.15
        elif workload.cost_priority == "cost":
            latency_weight, cost_weight = 0.25, 0.75

        # Lower normalized latency/cost is better -> invert into a 0..1 "goodness".
        latency_term = (1.0 - min(norm_latency, 1.0)) * latency_weight
        cost_term = (1.0 - min(norm_cost, 1.0)) * cost_weight
        headroom_term = (1.0 - utilization_pct / 100.0) * 0.15  # small nudge away from hot nodes

        score = round(latency_term + cost_term + headroom_term, 4)

        return CandidateScore(
            node_id=node.node_id,
            latency_ms=latency_ms,
            cost_per_hour_usd=node.cost_per_hour_usd,
            utilization_pct=round(utilization_pct, 1),
            score=score,
            breakdown={
                "latency_term": round(latency_term, 4),
                "cost_term": round(cost_term, 4),
                "headroom_term": round(headroom_term, 4),
                "distance_km": round(distance_km, 1),
                "latency_weight_used": latency_weight,
                "cost_weight_used": cost_weight,
            },
        )

    def recommend_cloud_burst(
        self, edge_candidates: List[CandidateScore], workload: Workload, edge_saturated: bool
    ) -> Optional[str]:
        """Return a human-readable recommendation string if the workload
        should burst to hybrid cloud capacity instead of (or in addition
        to) edge placement."""
        if edge_saturated or not edge_candidates:
            return (
                "No edge capacity satisfies placement constraints; recommend bursting "
                f"workload '{workload.name}' to hybrid cloud capacity in the nearest "
                "compliant cloud region until edge headroom frees up."
            )
        best = max(edge_candidates, key=lambda c: c.score)
        if best.latency_ms > workload.latency_sensitivity_ms:
            return (
                f"Best available edge node ({best.node_id}) has an estimated "
                f"{best.latency_ms}ms RTT, exceeding the workload's "
                f"{workload.latency_sensitivity_ms}ms latency budget. Consider hybrid "
                "cloud placement in a closer cloud region, or relaxing the latency SLA."
            )
        return None

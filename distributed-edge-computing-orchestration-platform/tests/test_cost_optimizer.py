import unittest

from edge_orchestrator.services.cost_optimizer import CostOptimizer, estimate_latency_ms, haversine_km
from tests.helpers import make_node, make_workload


class CostOptimizerTests(unittest.TestCase):
    def test_haversine_zero_distance(self):
        self.assertAlmostEqual(0.0, haversine_km(41.87, -87.62, 41.87, -87.62), places=3)

    def test_haversine_known_distance_chicago_to_new_york(self):
        # Great-circle distance is roughly 1145km; allow reasonable tolerance.
        dist = haversine_km(41.8781, -87.6298, 40.7128, -74.0060)
        self.assertGreater(dist, 1100)
        self.assertLess(dist, 1200)

    def test_latency_increases_with_distance(self):
        near = estimate_latency_ms(10, "5g")
        far = estimate_latency_ms(2000, "5g")
        self.assertLess(near, far)

    def test_satellite_has_higher_overhead_than_wired(self):
        wired = estimate_latency_ms(100, "wired")
        satellite = estimate_latency_ms(100, "satellite")
        self.assertLess(wired, satellite)

    def test_latency_priority_favors_closer_node_even_if_pricier(self):
        optimizer = CostOptimizer(latency_weight=0.6)
        cheap_far = make_node("far", lat=34.05, lon=-118.24, cost_per_hour_usd=0.001)
        pricier_near = make_node("near", lat=41.88, lon=-87.63, cost_per_hour_usd=0.05)
        workload = make_workload(cost_priority="latency")

        candidates = [cheap_far, pricier_near]
        max_lat = max(
            estimate_latency_ms(haversine_km(41.8781, -87.6298, n.location.latitude, n.location.longitude), n.connection_type)
            for n in candidates
        )
        max_cost = max(n.cost_per_hour_usd for n in candidates)

        scores = {
            n.node_id: optimizer.score_node(n, workload, 41.8781, -87.6298, max_lat, max_cost) for n in candidates
        }
        self.assertGreater(scores["near"].score, scores["far"].score)

    def test_cost_priority_favors_cheaper_node_even_if_farther(self):
        optimizer = CostOptimizer(latency_weight=0.6)
        cheap_far = make_node("far", lat=34.05, lon=-118.24, cost_per_hour_usd=0.001)
        pricier_near = make_node("near", lat=41.88, lon=-87.63, cost_per_hour_usd=0.09)
        workload = make_workload(cost_priority="cost")

        candidates = [cheap_far, pricier_near]
        max_lat = max(
            estimate_latency_ms(haversine_km(41.8781, -87.6298, n.location.latitude, n.location.longitude), n.connection_type)
            for n in candidates
        )
        max_cost = max(n.cost_per_hour_usd for n in candidates)

        scores = {
            n.node_id: optimizer.score_node(n, workload, 41.8781, -87.6298, max_lat, max_cost) for n in candidates
        }
        self.assertGreater(scores["far"].score, scores["near"].score)

    def test_recommend_cloud_burst_when_no_candidates(self):
        optimizer = CostOptimizer()
        workload = make_workload()
        rec = optimizer.recommend_cloud_burst([], workload, edge_saturated=True)
        self.assertIsNotNone(rec)
        self.assertIn("hybrid cloud", rec)


if __name__ == "__main__":
    unittest.main()

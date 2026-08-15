import unittest

from edge_orchestrator.core.models import SliceProfile
from tests.helpers import make_container


class NetworkSlicingTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()

    def tearDown(self):
        self.container.close()

    def test_create_slice_applies_default_qos(self):
        ns = self.container.slices.create_slice("urllc-line-1", SliceProfile.URLLC)
        self.assertEqual(5.0, ns.qos.max_latency_ms)
        self.assertEqual(99.999, ns.qos.min_reliability_pct)

    def test_validate_sla_passes_within_budget(self):
        ns = self.container.slices.create_slice("urllc-line-1", SliceProfile.URLLC)
        result = self.container.slices.validate_sla(ns.slice_id, observed_latency_ms=3.0, observed_throughput_mbps=20.0)
        self.assertTrue(result["valid"])

    def test_validate_sla_fails_when_latency_exceeded(self):
        ns = self.container.slices.create_slice("urllc-line-1", SliceProfile.URLLC)
        result = self.container.slices.validate_sla(ns.slice_id, observed_latency_ms=50.0, observed_throughput_mbps=20.0)
        self.assertFalse(result["valid"])
        self.assertFalse(result["latency_ok"])

    def test_qos_overrides_are_applied(self):
        ns = self.container.slices.create_slice(
            "custom-embb", SliceProfile.EMBB, qos_overrides={"max_latency_ms": 20.0}
        )
        self.assertEqual(20.0, ns.qos.max_latency_ms)

    def test_unknown_slice_validation_is_invalid(self):
        result = self.container.slices.validate_sla("slice-does-not-exist", 1.0, 1.0)
        self.assertFalse(result["valid"])


class ServiceMeshTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()

    def tearDown(self):
        self.container.close()

    def test_policy_allows_matching_traffic(self):
        self.container.mesh.add_policy("checkout", "inventory-sync", allowed_ports=[443])
        self.assertTrue(self.container.mesh.is_allowed("checkout", "inventory-sync", 443))

    def test_policy_denies_unlisted_port(self):
        self.container.mesh.add_policy("checkout", "inventory-sync", allowed_ports=[443])
        self.assertFalse(self.container.mesh.is_allowed("checkout", "inventory-sync", 8080))

    def test_no_policy_denies_by_default(self):
        self.assertFalse(self.container.mesh.is_allowed("a", "b", 443))

    def test_sidecar_config_contains_spiffe_identity(self):
        config = self.container.mesh.generate_sidecar_config("checkout", ["inventory-sync"])
        self.assertIn("spiffe://edge.internal/ns/default/workload/checkout", config)
        self.assertIn("inventory-sync", config)


if __name__ == "__main__":
    unittest.main()

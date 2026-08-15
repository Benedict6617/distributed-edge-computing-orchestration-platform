import unittest

from edge_orchestrator.services.compliance_engine import ComplianceEngine, ComplianceRegionRule
from tests.helpers import make_container, make_node, make_workload


class ComplianceEngineTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()
        rules = {
            "eu": ComplianceRegionRule(
                region_code="eu",
                countries=["DE", "FR", "NL"],
                allowed_destination_regions=["eu"],
                regulation="GDPR",
            ),
            "us": ComplianceRegionRule(
                region_code="us",
                countries=["US"],
                allowed_destination_regions=["us"],
                regulation="US privacy law",
            ),
        }
        self.container.compliance = ComplianceEngine(self.container.db, rules)
        # scheduler holds a reference captured at construction time; rebuild
        # it so it uses the freshly-configured compliance engine.
        from edge_orchestrator.services.scheduler import Scheduler

        self.container.scheduler = Scheduler(
            self.container.db, self.container.registry, self.container.compliance, self.container.cost_optimizer
        )
        self.container.workloads.scheduler = self.container.scheduler

    def tearDown(self):
        self.container.close()

    def test_eu_workload_allowed_on_eu_node(self):
        node = make_node("node-de", country="DE", region="eu-central")
        workload = make_workload(data_residency_region="eu")
        result = self.container.compliance.evaluate_placement(workload, node)
        self.assertTrue(result.allowed)
        self.assertEqual("GDPR", result.applicable_rule)

    def test_eu_workload_blocked_on_us_node(self):
        node = make_node("node-us", country="US", region="us-east")
        workload = make_workload(data_residency_region="eu")
        result = self.container.compliance.evaluate_placement(workload, node)
        self.assertFalse(result.allowed)

    def test_workload_without_residency_constraint_always_allowed(self):
        node = make_node("node-us", country="US", region="us-east")
        workload = make_workload(data_residency_region=None)
        result = self.container.compliance.evaluate_placement(workload, node)
        self.assertTrue(result.allowed)

    def test_unknown_region_is_rejected(self):
        node = make_node("node-us", country="US", region="us-east")
        workload = make_workload(data_residency_region="mars")
        result = self.container.compliance.evaluate_placement(workload, node)
        self.assertFalse(result.allowed)

    def test_scheduler_excludes_noncompliant_nodes(self):
        eu_node = make_node("node-eu", country="DE", region="eu-central", lat=52.5, lon=13.4)
        us_node = make_node("node-us", country="US", region="us-east", lat=41.87, lon=-87.6)
        self.container.registry.register(eu_node)
        self.container.registry.register(us_node)

        workload = make_workload(data_residency_region="eu")
        self.container.workloads.create(workload)

        decision = self.container.workloads.deploy(workload.workload_id, 52.5, 13.4)
        self.assertEqual(1, len(decision.deployments))
        self.assertEqual("node-eu", decision.deployments[0].node_id)

    def test_audit_log_records_evaluations(self):
        node = make_node("node-de", country="DE", region="eu-central")
        workload = make_workload(data_residency_region="eu")
        self.container.compliance.evaluate_placement(workload, node)
        log = self.container.compliance.audit_log()
        self.assertGreaterEqual(len(log), 1)
        self.assertEqual("node-de", log[0]["node_id"])


if __name__ == "__main__":
    unittest.main()

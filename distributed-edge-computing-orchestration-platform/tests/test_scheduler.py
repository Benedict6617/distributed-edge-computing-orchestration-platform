import unittest

from edge_orchestrator.core.models import NodeType
from tests.helpers import make_container, make_node, make_workload


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()

    def tearDown(self):
        self.container.close()

    def test_places_workload_on_closest_cheapest_node(self):
        near = make_node("node-near", lat=41.8781, lon=-87.6298, cost_per_hour_usd=0.01)   # Chicago
        far = make_node("node-far", lat=34.0522, lon=-118.2437, cost_per_hour_usd=0.01)    # LA
        self.container.registry.register(near)
        self.container.registry.register(far)

        workload = make_workload(cost_priority="latency")
        self.container.workloads.create(workload)

        decision = self.container.workloads.deploy(workload.workload_id, 41.8781, -87.6298)

        self.assertEqual(1, len(decision.deployments))
        self.assertEqual("node-near", decision.deployments[0].node_id)
        self.assertEqual(0, decision.unplaced_replicas)

    def test_respects_resource_limits(self):
        small = make_node("node-small", cpu_cores=1, memory_mb=512)
        self.container.registry.register(small)

        workload = make_workload(cpu_millicores=2000, memory_mb=4096)
        self.container.workloads.create(workload)

        decision = self.container.workloads.deploy(workload.workload_id, 41.8781, -87.6298)

        self.assertEqual(0, len(decision.deployments))
        self.assertEqual(1, decision.unplaced_replicas)
        self.assertIsNotNone(decision.cloud_burst_recommendation)

    def test_node_type_affinity_filters_candidates(self):
        factory_node = make_node("node-factory", node_type=NodeType.FACTORY)
        retail_node = make_node("node-retail", node_type=NodeType.RETAIL_STORE)
        self.container.registry.register(factory_node)
        self.container.registry.register(retail_node)

        workload = make_workload(node_type_affinity=["factory"])
        self.container.workloads.create(workload)

        decision = self.container.workloads.deploy(workload.workload_id, 41.8781, -87.6298)

        self.assertEqual(1, len(decision.deployments))
        self.assertEqual("node-factory", decision.deployments[0].node_id)

    def test_replicas_spread_across_distinct_nodes(self):
        for i in range(3):
            self.container.registry.register(make_node(f"node-{i}", lat=41.87 + i * 0.01, lon=-87.62))

        workload = make_workload(replicas=3)
        self.container.workloads.create(workload)
        decision = self.container.workloads.deploy(workload.workload_id, 41.8781, -87.6298)

        node_ids = {d.node_id for d in decision.deployments}
        self.assertEqual(3, len(node_ids))

    def test_offline_node_is_excluded(self):
        from edge_orchestrator.core.models import ConnectivityState

        offline_node = make_node("node-offline")
        self.container.registry.register(offline_node)
        self.container.registry.set_connectivity("node-offline", ConnectivityState.OFFLINE)

        online_node = make_node("node-online", lat=30.0, lon=-90.0)
        self.container.registry.register(online_node)

        workload = make_workload()
        self.container.workloads.create(workload)
        decision = self.container.workloads.deploy(workload.workload_id, 41.8781, -87.6298)

        self.assertEqual(1, len(decision.deployments))
        self.assertEqual("node-online", decision.deployments[0].node_id)

    def test_stop_deployment_releases_resources(self):
        node = make_node("node-1", cpu_cores=2, memory_mb=2048)
        self.container.registry.register(node)
        workload = make_workload(cpu_millicores=1000, memory_mb=1024)
        self.container.workloads.create(workload)

        decision = self.container.workloads.deploy(workload.workload_id, 41.8781, -87.6298)
        self.assertEqual(1, len(decision.deployments))

        refreshed = self.container.registry.get("node-1")
        self.assertEqual(1000, refreshed.allocated_cpu_millicores)

        self.container.scheduler.stop_deployment(decision.deployments[0].deployment_id)
        refreshed_after_stop = self.container.registry.get("node-1")
        self.assertEqual(0, refreshed_after_stop.allocated_cpu_millicores)


if __name__ == "__main__":
    unittest.main()

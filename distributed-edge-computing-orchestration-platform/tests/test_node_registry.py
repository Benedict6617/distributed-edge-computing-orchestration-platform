import unittest

from edge_orchestrator.core.models import NodeStatus
from tests.helpers import make_container, make_node


class NodeRegistryTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()

    def tearDown(self):
        self.container.close()

    def test_register_and_get(self):
        self.container.registry.register(make_node("node-1"))
        node = self.container.registry.get("node-1")
        self.assertIsNotNone(node)
        self.assertEqual("node-1", node.node_id)

    def test_cordon_prevents_new_status_ready(self):
        self.container.registry.register(make_node("node-1"))
        self.container.registry.cordon("node-1")
        node = self.container.registry.get("node-1")
        self.assertEqual(NodeStatus.CORDONED, node.status)

    def test_uncordon_restores_ready(self):
        self.container.registry.register(make_node("node-1"))
        self.container.registry.cordon("node-1")
        self.container.registry.uncordon("node-1")
        node = self.container.registry.get("node-1")
        self.assertEqual(NodeStatus.READY, node.status)

    def test_allocate_and_release_resources(self):
        self.container.registry.register(make_node("node-1", cpu_cores=4, memory_mb=8192))
        self.container.registry.allocate("node-1", 1000, 1024)
        node = self.container.registry.get("node-1")
        self.assertEqual(3000, node.available_cpu_millicores)

        self.container.registry.release("node-1", 1000, 1024)
        node = self.container.registry.get("node-1")
        self.assertEqual(4000, node.available_cpu_millicores)

    def test_release_does_not_go_negative(self):
        self.container.registry.register(make_node("node-1"))
        self.container.registry.release("node-1", 9999, 9999)
        node = self.container.registry.get("node-1")
        self.assertEqual(0, node.allocated_cpu_millicores)

    def test_list_filters_by_region(self):
        self.container.registry.register(make_node("node-us", region="us-east"))
        self.container.registry.register(make_node("node-eu", region="eu-west"))
        us_nodes = self.container.registry.list(region="us-east")
        self.assertEqual(1, len(us_nodes))
        self.assertEqual("node-us", us_nodes[0].node_id)

    def test_count(self):
        self.container.registry.register(make_node("node-1"))
        self.container.registry.register(make_node("node-2"))
        self.assertEqual(2, self.container.registry.count())


if __name__ == "__main__":
    unittest.main()

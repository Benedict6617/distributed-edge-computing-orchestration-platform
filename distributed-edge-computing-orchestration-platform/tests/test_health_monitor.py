import unittest

from edge_orchestrator.core.models import ConnectivityState, HealthMetric
from tests.helpers import make_container, make_node


class HealthMonitorTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()
        self.container.registry.register(make_node("node-1"))

    def tearDown(self):
        self.container.close()

    def test_ingest_updates_heartbeat_and_connectivity(self):
        self.container.registry.set_connectivity("node-1", ConnectivityState.OFFLINE)
        metric = HealthMetric(
            node_id="node-1", cpu_usage_pct=40, memory_usage_pct=50, disk_usage_pct=30, temperature_c=45, uptime_s=1000
        )
        self.container.health.ingest(metric)
        node = self.container.registry.get("node-1")
        self.assertEqual(ConnectivityState.ONLINE, node.connectivity_state)

    def test_high_cpu_raises_alert(self):
        metric = HealthMetric(
            node_id="node-1", cpu_usage_pct=95, memory_usage_pct=50, disk_usage_pct=30, temperature_c=45, uptime_s=1000
        )
        alerts = self.container.health.ingest(metric)
        self.assertTrue(any(a.metric == "cpu_usage_pct" for a in alerts))

    def test_normal_metrics_raise_no_alerts(self):
        metric = HealthMetric(
            node_id="node-1", cpu_usage_pct=40, memory_usage_pct=50, disk_usage_pct=30, temperature_c=45, uptime_s=1000
        )
        alerts = self.container.health.ingest(metric)
        self.assertEqual(0, len(alerts))

    def test_history_returns_most_recent_first(self):
        for cpu in (10, 20, 30):
            self.container.health.ingest(
                HealthMetric(node_id="node-1", cpu_usage_pct=cpu, memory_usage_pct=1, disk_usage_pct=1, temperature_c=1, uptime_s=1)
            )
        history = self.container.health.history("node-1", limit=10)
        self.assertEqual(30, history[0].cpu_usage_pct)

    def test_connectivity_sweep_marks_stale_node_offline(self):
        container = make_container(heartbeat_timeout_s=0, degraded_threshold_s=0)
        try:
            container.registry.register(make_node("node-stale"))
            import time

            time.sleep(0.01)
            summary = container.health.sweep_connectivity()
            self.assertGreaterEqual(summary["offline"], 1)
        finally:
            container.close()


if __name__ == "__main__":
    unittest.main()

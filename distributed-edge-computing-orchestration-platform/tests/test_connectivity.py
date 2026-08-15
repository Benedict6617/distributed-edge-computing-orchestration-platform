import unittest

from edge_orchestrator.services.connectivity import ConnectivityManager


class ConnectivityManagerTests(unittest.TestCase):
    def test_enqueue_and_drain(self):
        mgr = ConnectivityManager(max_queue_size=10)
        mgr.enqueue("node-1", "deploy", {"workload_id": "wl-1"})
        mgr.enqueue("node-1", "deploy", {"workload_id": "wl-2"})
        self.assertEqual(2, mgr.queue_depth("node-1"))

        batch = mgr.drain_queue("node-1")
        self.assertEqual(2, len(batch))
        self.assertEqual(1, batch[0].attempts)

    def test_acknowledge_removes_command(self):
        mgr = ConnectivityManager()
        cmd = mgr.enqueue("node-1", "deploy", {})
        self.assertEqual(1, mgr.queue_depth("node-1"))
        ok = mgr.acknowledge("node-1", cmd.command_id)
        self.assertTrue(ok)
        self.assertEqual(0, mgr.queue_depth("node-1"))

    def test_acknowledge_unknown_command_returns_false(self):
        mgr = ConnectivityManager()
        self.assertFalse(mgr.acknowledge("node-1", "cmd-does-not-exist"))

    def test_bounded_queue_drops_oldest(self):
        mgr = ConnectivityManager(max_queue_size=3)
        for i in range(5):
            mgr.enqueue("node-1", "deploy", {"i": i})
        self.assertEqual(3, mgr.queue_depth("node-1"))
        remaining = mgr.pending("node-1")
        # the two oldest (i=0, i=1) should have been dropped
        self.assertEqual([2, 3, 4], [c.payload["i"] for c in remaining])

    def test_fleet_summary_only_includes_nonempty_queues(self):
        mgr = ConnectivityManager()
        mgr.enqueue("node-1", "deploy", {})
        mgr.enqueue("node-2", "deploy", {})
        mgr.acknowledge("node-2", mgr.pending("node-2")[0].command_id)
        summary = mgr.fleet_summary()
        self.assertIn("node-1", summary)
        self.assertNotIn("node-2", summary)


if __name__ == "__main__":
    unittest.main()

import unittest

from edge_orchestrator.core.models import NodeType, UpdateRolloutState
from tests.helpers import make_container, make_node


class OTAManagerTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()
        for i in range(10):
            self.container.registry.register(make_node(f"node-{i}", node_type=NodeType.RETAIL_STORE))

    def tearDown(self):
        self.container.close()

    def test_create_campaign_counts_targets(self):
        campaign = self.container.ota.create_campaign(
            name="rollout-1", target_firmware_version="2.0.0", node_selector={"node_type": "retail_store"}
        )
        self.assertEqual(10, campaign.nodes_total)
        self.assertEqual(UpdateRolloutState.DRAFT, campaign.state)

    def test_canary_wave_queues_commands_for_subset(self):
        campaign = self.container.ota.create_campaign(
            name="rollout-1", target_firmware_version="2.0.0",
            node_selector={"node_type": "retail_store"}, canary_percent=20,
        )
        campaign = self.container.ota.start_canary(campaign.campaign_id)
        self.assertEqual(UpdateRolloutState.CANARY, campaign.state)

        queued_nodes = [n for n in range(10) if self.container.connectivity.queue_depth(f"node-{n}") > 0]
        self.assertEqual(2, len(queued_nodes))  # 20% of 10

    def test_auto_pause_on_high_failure_rate(self):
        campaign = self.container.ota.create_campaign(
            name="rollout-1", target_firmware_version="2.0.0",
            node_selector={"node_type": "retail_store"}, max_failure_rate_pct=10.0,
        )
        # Simulate 3 failures out of 4 reported results -> 75% failure rate.
        for i in range(3):
            campaign = self.container.ota.report_result(campaign.campaign_id, f"node-{i}", success=False)
        campaign = self.container.ota.report_result(campaign.campaign_id, "node-3", success=True)

        advanced = self.container.ota.advance(campaign.campaign_id)
        self.assertEqual(UpdateRolloutState.PAUSED, advanced.state)

    def test_successful_update_bumps_node_firmware_version(self):
        campaign = self.container.ota.create_campaign(
            name="rollout-1", target_firmware_version="9.9.9", node_selector={"node_type": "retail_store"}
        )
        self.container.ota.report_result(campaign.campaign_id, "node-0", success=True)
        node = self.container.registry.get("node-0")
        self.assertEqual("9.9.9", node.firmware_version)

    def test_rollback_sets_state_and_queues_previous_version(self):
        campaign = self.container.ota.create_campaign(
            name="rollout-1", target_firmware_version="2.0.0", node_selector={"node_type": "retail_store"}
        )
        campaign = self.container.ota.rollback(campaign.campaign_id, "1.9.0")
        self.assertEqual(UpdateRolloutState.ROLLED_BACK, campaign.state)
        self.assertGreater(self.container.connectivity.queue_depth("node-0"), 0)


if __name__ == "__main__":
    unittest.main()

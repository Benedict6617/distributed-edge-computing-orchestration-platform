import unittest

from edge_orchestrator.services.hybrid_cloud import HybridCloudManager


class HybridCloudTests(unittest.TestCase):
    def setUp(self):
        self.manager = HybridCloudManager()

    def test_all_regions_nonempty(self):
        regions = self.manager.all_regions()
        self.assertGreater(len(regions), 0)
        providers = {r.provider for r in regions}
        self.assertEqual({"aws", "azure", "gcp"}, providers)

    def test_cheapest_region_in_countries(self):
        region = self.manager.cheapest_region_in_countries(["DE", "NL"])
        self.assertIn(region.country_code, ("DE", "NL"))

    def test_cheapest_region_falls_back_when_no_match(self):
        region = self.manager.cheapest_region_in_countries(["ZZ"])
        self.assertIsNotNone(region)

    def test_burst_creates_instance(self):
        instance = self.manager.burst("aws", "us-east-1", "wl-test", 1000, 1024)
        self.assertEqual("aws", instance.provider)
        self.assertEqual("us-east-1", instance.region_code)
        self.assertTrue(instance.instance_id.startswith("aws-"))


if __name__ == "__main__":
    unittest.main()

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from edge_orchestrator.api.server import make_handler
from edge_orchestrator.container import Container
from edge_orchestrator.core.config import Settings


def _request(base_url, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{base_url}{path}", data=data, method=method,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class APIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = Settings(
            host="127.0.0.1", port=0, db_path=":memory:", log_level="CRITICAL",
            heartbeat_timeout_s=120, degraded_threshold_s=45,
            scheduler_latency_weight=0.6, max_offline_queue_size=500,
            default_ota_max_failure_rate_pct=10.0,
        )
        cls.container = Container(settings=settings, db_path=":memory:")
        handler_cls = make_handler(cls.container)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        cls.port = cls.httpd.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.container.close()

    def test_health_check(self):
        status, body = _request(self.base_url, "GET", "/health")
        self.assertEqual(200, status)
        self.assertEqual("ok", body["status"])

    def test_register_and_fetch_node(self):
        payload = {
            "name": "API Test Node",
            "node_type": "retail_store",
            "location": {"latitude": 1.0, "longitude": 2.0, "region": "us-east", "country_code": "US"},
            "hardware": {"cpu_cores": 4, "memory_mb": 8192, "storage_gb": 64},
        }
        status, body = _request(self.base_url, "POST", "/api/v1/nodes", payload)
        self.assertEqual(201, status)
        node_id = body["node_id"]

        status, body = _request(self.base_url, "GET", f"/api/v1/nodes/{node_id}")
        self.assertEqual(200, status)
        self.assertEqual("API Test Node", body["name"])

    def test_get_missing_node_returns_404(self):
        status, body = _request(self.base_url, "GET", "/api/v1/nodes/does-not-exist")
        self.assertEqual(404, status)

    def test_create_workload_and_deploy(self):
        node_payload = {
            "name": "Deploy Target",
            "node_type": "retail_store",
            "location": {"latitude": 41.88, "longitude": -87.63, "region": "us-east", "country_code": "US"},
            "hardware": {"cpu_cores": 4, "memory_mb": 8192, "storage_gb": 64},
        }
        _request(self.base_url, "POST", "/api/v1/nodes", node_payload)

        workload_payload = {
            "name": "api-test-workload",
            "container_image": "registry.internal/example:1.0.0",
            "resources": {"cpu_millicores": 500, "memory_mb": 512},
            "replicas": 1,
        }
        status, body = _request(self.base_url, "POST", "/api/v1/workloads", workload_payload)
        self.assertEqual(201, status)
        workload_id = body["workload_id"]

        status, body = _request(
            self.base_url, "POST", f"/api/v1/workloads/{workload_id}/deploy",
            {"origin_lat": 41.88, "origin_lon": -87.63},
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(body["deployments"]))

    def test_invalid_json_body_returns_400(self):
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/nodes", data=b"{not json", method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(400, e.code)

    def test_metrics_endpoint_returns_prometheus_text(self):
        req = urllib.request.Request(f"{self.base_url}/metrics", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            text = resp.read().decode()
        self.assertIn("edge_orchestrator_nodes_total", text)


if __name__ == "__main__":
    unittest.main()

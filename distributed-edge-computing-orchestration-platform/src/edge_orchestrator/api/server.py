"""
HTTP API server built entirely on Python's standard library
(`http.server` + `socketserver`). No Flask, no FastAPI, no uvicorn --
this keeps the control plane deployable with nothing but a base Python
container image, which matters for management infrastructure that may
itself run in resource-constrained or air-gapped environments.

Run directly:
    python3 -m edge_orchestrator.api.server

Or via the CLI:
    edge-orchestrator serve
"""

from __future__ import annotations

import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import handlers as h
from .router import Router
from ..container import Container
from ..core.logging_setup import configure_logging, get_logger

logger = get_logger("edge_orchestrator.api")


def build_router() -> Router:
    router = Router()
    router.add("GET", "/health", h.health_check)
    router.add("GET", "/metrics", h.metrics)

    router.add("GET", "/api/v1/nodes", h.list_nodes)
    router.add("POST", "/api/v1/nodes", h.register_node)
    router.add("GET", "/api/v1/nodes/{node_id}", h.get_node)
    router.add("POST", "/api/v1/nodes/{node_id}/heartbeat", h.heartbeat_node)
    router.add("POST", "/api/v1/nodes/{node_id}/cordon", h.cordon_node)
    router.add("POST", "/api/v1/nodes/{node_id}/uncordon", h.uncordon_node)

    router.add("GET", "/api/v1/workloads", h.list_workloads)
    router.add("POST", "/api/v1/workloads", h.create_workload)
    router.add("GET", "/api/v1/workloads/{workload_id}", h.get_workload)
    router.add("POST", "/api/v1/workloads/{workload_id}/deploy", h.deploy_workload)

    router.add("GET", "/api/v1/deployments", h.list_deployments)
    router.add("POST", "/api/v1/deployments/{deployment_id}/stop", h.stop_deployment)

    router.add("POST", "/api/v1/health/ingest", h.ingest_health)
    router.add("GET", "/api/v1/health/fleet", h.fleet_health)
    router.add("GET", "/api/v1/health/alerts", h.recent_alerts)
    router.add("POST", "/api/v1/health/sweep", h.sweep_connectivity)

    router.add("POST", "/api/v1/updates", h.create_campaign)
    router.add("GET", "/api/v1/updates", h.list_campaigns)
    router.add("GET", "/api/v1/updates/{campaign_id}", h.get_campaign)
    router.add("POST", "/api/v1/updates/{campaign_id}/canary", h.start_canary)
    router.add("POST", "/api/v1/updates/{campaign_id}/advance", h.advance_campaign)
    router.add("POST", "/api/v1/updates/{campaign_id}/report", h.report_campaign_result)
    router.add("POST", "/api/v1/updates/{campaign_id}/rollback", h.rollback_campaign)

    router.add("GET", "/api/v1/slices", h.list_slices)
    router.add("POST", "/api/v1/slices", h.create_slice)
    router.add("POST", "/api/v1/slices/{slice_id}/validate", h.validate_slice_sla)

    router.add("GET", "/api/v1/mesh/policies", h.list_mesh_policies)
    router.add("POST", "/api/v1/mesh/policies", h.add_mesh_policy)
    router.add("GET", "/api/v1/mesh/sidecar/{workload_name}", h.sidecar_config)

    router.add("GET", "/api/v1/compliance/regions", h.list_compliance_regions)
    router.add("GET", "/api/v1/compliance/audit-log", h.compliance_audit_log)
    router.add("POST", "/api/v1/compliance/validate", h.validate_compliance)

    router.add("GET", "/api/v1/cloud/regions", h.list_cloud_regions)
    router.add("POST", "/api/v1/cloud/burst", h.burst_to_cloud)
    return router


ROUTER = build_router()


def make_handler(container: Container):
    class OrchestratorRequestHandler(BaseHTTPRequestHandler):
        server_version = "EdgeOrchestrator/1.0"

        def log_message(self, fmt, *args):  # route access logs through our JSON logger
            logger.info("http request", extra={"extra_fields": {"client": self.client_address[0], "message": fmt % args}})

        def _dispatch(self, method: str):
            parsed = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            handler, params = ROUTER.resolve(method, parsed.path)
            if not handler:
                self._write(404, {"error": f"no route for {method} {parsed.path}"})
                return

            body = {}
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    self._write(400, {"error": "invalid JSON body"})
                    return

            try:
                status, response = handler(container, params, query, body)
            except Exception as exc:  # control-plane must never crash on a bad request
                logger.info("handler error", extra={"extra_fields": {"error": str(exc), "trace": traceback.format_exc()}})
                self._write(500, {"error": "internal server error", "detail": str(exc)})
                return
            self._write(status, response)

        def _write(self, status: int, payload: dict) -> None:
            self.send_response(status)
            if "__raw_text__" in payload:
                body = payload["__raw_text__"].encode()
                self.send_header("Content-Type", "text/plain; version=0.0.4")
            else:
                body = json.dumps(payload, indent=2).encode()
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    return OrchestratorRequestHandler


def run(host: str = None, port: int = None, db_path: str = None):
    container = Container(db_path=db_path)
    configure_logging(container.settings.log_level)
    host = host or container.settings.host
    port = port or container.settings.port
    handler_cls = make_handler(container)
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    logger.info("edge orchestrator API listening", extra={"extra_fields": {"host": host, "port": port}})
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        httpd.server_close()
        container.close()


if __name__ == "__main__":
    run()

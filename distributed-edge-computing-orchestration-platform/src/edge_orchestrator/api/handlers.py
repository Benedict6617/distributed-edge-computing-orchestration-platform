"""
Request handlers for the orchestrator's HTTP API.

Every handler has the signature `(container, params, query, body) -> (status_code, response_dict)`
so `server.py` can dispatch uniformly and serialize the result as JSON. Handlers
are intentionally thin: they translate HTTP <-> domain objects and delegate
all real logic to the `services/` layer, which is what's exercised directly
by the unit tests.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from ..core.models import (
    ConnectivityState,
    EdgeNode,
    GeoLocation,
    HardwareProfile,
    HealthMetric,
    NodeType,
    ResourceRequest,
    SliceProfile,
    Workload,
    new_id,
)

Response = Tuple[int, Dict[str, Any]]


def _err(status: int, message: str) -> Response:
    return status, {"error": message}


# -- system -----------------------------------------------------------------
def health_check(container, params, query, body) -> Response:
    return 200, {"status": "ok", "nodes_registered": container.registry.count()}


def metrics(container, params, query, body) -> Response:
    from ..services.metrics_exporter import render_prometheus_metrics

    text = render_prometheus_metrics(container.registry, container.health, container.scheduler, container.ota)
    return 200, {"__raw_text__": text}


# -- nodes --------------------------------------------------------------
def list_nodes(container, params, query, body) -> Response:
    nodes = container.registry.list(
        region=query.get("region"), node_type=query.get("node_type"), status=query.get("status")
    )
    return 200, {"nodes": [n.to_dict() for n in nodes], "count": len(nodes)}


def get_node(container, params, query, body) -> Response:
    node = container.registry.get(params["node_id"])
    if not node:
        return _err(404, f"node '{params['node_id']}' not found")
    return 200, node.to_dict()


def register_node(container, params, query, body) -> Response:
    try:
        node = EdgeNode(
            node_id=body.get("node_id") or new_id("node"),
            name=body["name"],
            node_type=NodeType(body["node_type"]),
            location=GeoLocation(**body["location"]),
            hardware=HardwareProfile(**body["hardware"]),
            connection_type=body.get("connection_type", "5g"),
            labels=body.get("labels", {}),
            cost_per_hour_usd=float(body.get("cost_per_hour_usd", 0.0)),
            firmware_version=body.get("firmware_version", "1.0.0"),
        )
    except (KeyError, TypeError, ValueError) as e:
        return _err(400, f"invalid node payload: {e}")
    container.registry.register(node)
    return 201, node.to_dict()


def heartbeat_node(container, params, query, body) -> Response:
    node = container.registry.heartbeat(params["node_id"])
    if not node:
        return _err(404, f"node '{params['node_id']}' not found")
    return 200, node.to_dict()


def cordon_node(container, params, query, body) -> Response:
    node = container.registry.cordon(params["node_id"])
    if not node:
        return _err(404, f"node '{params['node_id']}' not found")
    return 200, node.to_dict()


def uncordon_node(container, params, query, body) -> Response:
    node = container.registry.uncordon(params["node_id"])
    if not node:
        return _err(404, f"node '{params['node_id']}' not found")
    return 200, node.to_dict()


# -- workloads ------------------------------------------------------------
def list_workloads(container, params, query, body) -> Response:
    workloads = container.workloads.list()
    return 200, {"workloads": [w.to_dict() for w in workloads], "count": len(workloads)}


def get_workload(container, params, query, body) -> Response:
    workload = container.workloads.get(params["workload_id"])
    if not workload:
        return _err(404, f"workload '{params['workload_id']}' not found")
    return 200, workload.to_dict()


def create_workload(container, params, query, body) -> Response:
    try:
        workload = Workload(
            workload_id=body.get("workload_id") or new_id("wl"),
            name=body["name"],
            container_image=body["container_image"],
            resources=ResourceRequest(**body["resources"]),
            replicas=int(body.get("replicas", 1)),
            latency_sensitivity_ms=int(body.get("latency_sensitivity_ms", 500)),
            data_residency_region=body.get("data_residency_region"),
            node_type_affinity=body.get("node_type_affinity"),
            slice_profile=SliceProfile(body["slice_profile"]) if body.get("slice_profile") else None,
            cost_priority=body.get("cost_priority", "balanced"),
            env=body.get("env", {}),
        )
    except (KeyError, TypeError, ValueError) as e:
        return _err(400, f"invalid workload payload: {e}")
    container.workloads.create(workload)
    return 201, workload.to_dict()


def deploy_workload(container, params, query, body) -> Response:
    body = body or {}
    origin_lat = body.get("origin_lat")
    origin_lon = body.get("origin_lon")
    if origin_lat is None or origin_lon is None:
        return _err(400, "origin_lat and origin_lon are required to score placement distance")
    try:
        decision = container.workloads.deploy(params["workload_id"], float(origin_lat), float(origin_lon))
    except ValueError as e:
        return _err(404, str(e))
    return 200, {
        "workload_id": decision.workload_id,
        "deployments": [d.to_dict() for d in decision.deployments],
        "unplaced_replicas": decision.unplaced_replicas,
        "cloud_burst_recommendation": decision.cloud_burst_recommendation,
    }


# -- deployments ------------------------------------------------------------
def list_deployments(container, params, query, body) -> Response:
    deployments = container.scheduler.all_deployments()
    return 200, {"deployments": [d.to_dict() for d in deployments], "count": len(deployments)}


def stop_deployment(container, params, query, body) -> Response:
    deployment = container.scheduler.stop_deployment(params["deployment_id"])
    if not deployment:
        return _err(404, f"deployment '{params['deployment_id']}' not found")
    return 200, deployment.to_dict()


# -- health / telemetry -------------------------------------------------
def ingest_health(container, params, query, body) -> Response:
    try:
        metric = HealthMetric(
            node_id=body["node_id"],
            cpu_usage_pct=float(body["cpu_usage_pct"]),
            memory_usage_pct=float(body["memory_usage_pct"]),
            disk_usage_pct=float(body["disk_usage_pct"]),
            temperature_c=float(body["temperature_c"]),
            uptime_s=int(body.get("uptime_s", 0)),
        )
    except (KeyError, TypeError, ValueError) as e:
        return _err(400, f"invalid health metric payload: {e}")
    alerts = container.health.ingest(metric)
    return 201, {"accepted": True, "alerts_raised": [a.__dict__ for a in alerts]}


def fleet_health(container, params, query, body) -> Response:
    return 200, container.health.fleet_snapshot()


def recent_alerts(container, params, query, body) -> Response:
    return 200, {"alerts": container.health.recent_alerts()}


def sweep_connectivity(container, params, query, body) -> Response:
    return 200, container.health.sweep_connectivity()


# -- OTA updates -----------------------------------------------------------
def create_campaign(container, params, query, body) -> Response:
    try:
        campaign = container.ota.create_campaign(
            name=body["name"],
            target_firmware_version=body["target_firmware_version"],
            node_selector=body.get("node_selector", {}),
            canary_percent=int(body.get("canary_percent", 5)),
            max_failure_rate_pct=float(body.get("max_failure_rate_pct", 10.0)),
        )
    except (KeyError, TypeError, ValueError) as e:
        return _err(400, f"invalid campaign payload: {e}")
    return 201, campaign.to_dict()


def list_campaigns(container, params, query, body) -> Response:
    return 200, {"campaigns": [c.to_dict() for c in container.ota.list()]}


def get_campaign(container, params, query, body) -> Response:
    campaign = container.ota.get(params["campaign_id"])
    if not campaign:
        return _err(404, f"campaign '{params['campaign_id']}' not found")
    return 200, campaign.to_dict()


def start_canary(container, params, query, body) -> Response:
    try:
        campaign = container.ota.start_canary(params["campaign_id"])
    except ValueError as e:
        return _err(404, str(e))
    return 200, campaign.to_dict()


def advance_campaign(container, params, query, body) -> Response:
    wave_percent = int((body or {}).get("wave_percent", 25))
    try:
        campaign = container.ota.advance(params["campaign_id"], wave_percent)
    except ValueError as e:
        return _err(404, str(e))
    return 200, campaign.to_dict()


def report_campaign_result(container, params, query, body) -> Response:
    try:
        campaign = container.ota.report_result(
            params["campaign_id"], body["node_id"], bool(body["success"])
        )
    except (KeyError, ValueError) as e:
        return _err(400, str(e))
    return 200, campaign.to_dict()


def rollback_campaign(container, params, query, body) -> Response:
    previous_version = (body or {}).get("previous_version", "0.0.0")
    try:
        campaign = container.ota.rollback(params["campaign_id"], previous_version)
    except ValueError as e:
        return _err(404, str(e))
    return 200, campaign.to_dict()


# -- 5G network slicing --------------------------------------------------
def list_slices(container, params, query, body) -> Response:
    return 200, {"slices": [s.to_dict() for s in container.slices.list()]}


def create_slice(container, params, query, body) -> Response:
    try:
        ns = container.slices.create_slice(
            name=body["name"],
            profile=SliceProfile(body["profile"]),
            tenant=body.get("tenant", "default"),
            qos_overrides=body.get("qos_overrides"),
        )
    except (KeyError, ValueError) as e:
        return _err(400, str(e))
    return 201, ns.to_dict()


def validate_slice_sla(container, params, query, body) -> Response:
    try:
        result = container.slices.validate_sla(
            params["slice_id"], float(body["observed_latency_ms"]), float(body["observed_throughput_mbps"])
        )
    except (KeyError, TypeError, ValueError) as e:
        return _err(400, str(e))
    return 200, result


# -- service mesh ------------------------------------------------------
def list_mesh_policies(container, params, query, body) -> Response:
    return 200, {"policies": [p.to_dict() for p in container.mesh.list_policies()]}


def add_mesh_policy(container, params, query, body) -> Response:
    try:
        policy = container.mesh.add_policy(
            source_workload=body["source_workload"],
            destination_workload=body["destination_workload"],
            allowed_ports=body.get("allowed_ports"),
            mtls_required=bool(body.get("mtls_required", True)),
            description=body.get("description", ""),
        )
    except KeyError as e:
        return _err(400, f"missing field {e}")
    return 201, policy.to_dict()


def sidecar_config(container, params, query, body) -> Response:
    upstreams = query.get("upstreams", "")
    upstream_list = [u for u in upstreams.split(",") if u] if upstreams else []
    config_text = container.mesh.generate_sidecar_config(params["workload_name"], upstream_list)
    return 200, {"__raw_text__": config_text}


# -- compliance ------------------------------------------------------
def list_compliance_regions(container, params, query, body) -> Response:
    return 200, {"regions": container.compliance.list_regions()}


def compliance_audit_log(container, params, query, body) -> Response:
    return 200, {"entries": container.compliance.audit_log()}


def validate_compliance(container, params, query, body) -> Response:
    workload = container.workloads.get(body.get("workload_id", ""))
    node = container.registry.get(body.get("node_id", ""))
    if not workload or not node:
        return _err(404, "workload or node not found")
    result = container.compliance.evaluate_placement(workload, node)
    return 200, {"allowed": result.allowed, "reason": result.reason, "regulation": result.applicable_rule}


# -- hybrid cloud ------------------------------------------------------
def list_cloud_regions(container, params, query, body) -> Response:
    regions = container.hybrid_cloud.all_regions()
    return 200, {
        "regions": [
            {
                "provider": r.provider,
                "region_code": r.region_code,
                "country_code": r.country_code,
                "on_demand_cost_per_vcpu_hr": r.on_demand_cost_per_vcpu_hr,
            }
            for r in regions
        ]
    }


def burst_to_cloud(container, params, query, body) -> Response:
    try:
        instance = container.hybrid_cloud.burst(
            provider=body["provider"],
            region_code=body["region_code"],
            workload_id=body["workload_id"],
            cpu_millicores=int(body.get("cpu_millicores", 1000)),
            memory_mb=int(body.get("memory_mb", 1024)),
        )
    except (KeyError, ValueError) as e:
        return _err(400, str(e))
    return 201, {
        "instance_id": instance.instance_id,
        "provider": instance.provider,
        "region_code": instance.region_code,
        "workload_id": instance.workload_id,
    }

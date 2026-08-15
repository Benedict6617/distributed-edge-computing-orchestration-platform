"""
`edge-orchestrator` command-line interface.

Built on argparse (stdlib) rather than click/typer so operators can run it
with a bare `python3 -m edge_orchestrator.cli.main` on any machine with
Python installed -- no virtualenv, no pip install, no lockfile drift
between what's documented and what's actually runnable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..container import Container
from ..core.logging_setup import configure_logging
from ..core.models import (
    EdgeNode,
    GeoLocation,
    HardwareProfile,
    NodeType,
    ResourceRequest,
    SliceProfile,
    Workload,
    new_id,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"


def _print_table(rows, columns):
    if not rows:
        print("(none)")
        return
    widths = [max(len(str(r.get(c, ""))) for r in rows + [dict(zip(columns, columns))]) for c in columns]
    header = "  ".join(c.ljust(w) for c, w in zip(columns, widths))
    print(header)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(columns, widths)))


def cmd_seed(args, container: Container):
    data_dir = Path(args.data_dir)
    nodes_path = data_dir / "sample_nodes.json"
    workloads_path = data_dir / "sample_workloads.json"

    nodes_created = 0
    if nodes_path.exists():
        for raw in json.loads(nodes_path.read_text()):
            node = EdgeNode(
                node_id=raw["node_id"],
                name=raw["name"],
                node_type=NodeType(raw["node_type"]),
                location=GeoLocation(**raw["location"]),
                hardware=HardwareProfile(**raw["hardware"]),
                connection_type=raw.get("connection_type", "5g"),
                labels=raw.get("labels", {}),
                cost_per_hour_usd=raw.get("cost_per_hour_usd", 0.0),
                firmware_version=raw.get("firmware_version", "1.0.0"),
            )
            container.registry.register(node)
            nodes_created += 1

    workloads_created = 0
    if workloads_path.exists():
        for raw in json.loads(workloads_path.read_text()):
            workload = Workload(
                workload_id=raw["workload_id"],
                name=raw["name"],
                container_image=raw["container_image"],
                resources=ResourceRequest(**raw["resources"]),
                replicas=raw.get("replicas", 1),
                latency_sensitivity_ms=raw.get("latency_sensitivity_ms", 500),
                data_residency_region=raw.get("data_residency_region"),
                node_type_affinity=raw.get("node_type_affinity"),
                slice_profile=SliceProfile(raw["slice_profile"]) if raw.get("slice_profile") else None,
                cost_priority=raw.get("cost_priority", "balanced"),
                env=raw.get("env", {}),
            )
            container.workloads.create(workload)
            workloads_created += 1

    print(f"Seeded {nodes_created} nodes and {workloads_created} workloads from {data_dir}")


def cmd_serve(args, container: Container):
    from ..api.server import run

    # `container` here was only built to validate settings/db path up front;
    # `run()` constructs its own Container bound to the request-handling
    # server, so we must forward the *actual* --db value the operator
    # passed in, not container.settings.db_path (which reflects env/config
    # defaults and may differ from an explicit --db override).
    print(f"Starting edge orchestrator API on {args.host}:{args.port} (db={args.db})")
    container.close()
    run(host=args.host, port=args.port, db_path=args.db)


def cmd_nodes_list(args, container: Container):
    nodes = container.registry.list(region=args.region, node_type=args.type, status=args.status)
    rows = [
        {
            "node_id": n.node_id,
            "name": n.name,
            "type": n.node_type.value,
            "region": n.location.region,
            "connectivity": n.connectivity_state.value,
            "status": n.status.value,
            "cpu_free_m": n.available_cpu_millicores,
            "mem_free_mb": n.available_memory_mb,
        }
        for n in nodes
    ]
    _print_table(rows, ["node_id", "name", "type", "region", "connectivity", "status", "cpu_free_m", "mem_free_mb"])
    print(f"\n{len(nodes)} node(s)")


def cmd_workloads_list(args, container: Container):
    workloads = container.workloads.list()
    rows = [
        {
            "workload_id": w.workload_id,
            "name": w.name,
            "image": w.container_image,
            "replicas": w.replicas,
            "latency_budget_ms": w.latency_sensitivity_ms,
            "residency": w.data_residency_region or "-",
        }
        for w in workloads
    ]
    _print_table(rows, ["workload_id", "name", "image", "replicas", "latency_budget_ms", "residency"])


def cmd_workloads_deploy(args, container: Container):
    decision = container.workloads.deploy(args.workload_id, args.lat, args.lon)
    print(f"Workload '{args.workload_id}': {len(decision.deployments)} replica(s) placed, "
          f"{decision.unplaced_replicas} unplaced.")
    for d in decision.deployments:
        print(f"  -> {d.node_id}  [{d.state.value}]  {d.placement_reason}")
    if decision.cloud_burst_recommendation:
        print(f"  Recommendation: {decision.cloud_burst_recommendation}")


def cmd_updates_rollout(args, container: Container):
    selector = {}
    if args.node_type:
        selector["node_type"] = args.node_type
    if args.region:
        selector["region"] = args.region
    campaign = container.ota.create_campaign(
        name=args.name,
        target_firmware_version=args.version,
        node_selector=selector,
        canary_percent=args.canary_percent,
    )
    campaign = container.ota.start_canary(campaign.campaign_id)
    print(f"Campaign {campaign.campaign_id} created targeting {campaign.nodes_total} node(s); "
          f"canary wave pushed to {campaign.canary_percent}%.")


def cmd_updates_status(args, container: Container):
    campaign = container.ota.get(args.campaign_id)
    if not campaign:
        print(f"No such campaign '{args.campaign_id}'", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(campaign.to_dict(), indent=2))


def cmd_compliance_check(args, container: Container):
    workload = container.workloads.get(args.workload_id)
    node = container.registry.get(args.node_id)
    if not workload or not node:
        print("workload or node not found", file=sys.stderr)
        sys.exit(1)
    result = container.compliance.evaluate_placement(workload, node)
    print(f"Allowed: {result.allowed}\nReason: {result.reason}")


def cmd_demo(args, container: Container):
    """Run a short end-to-end scenario against seeded data: deploy a
    latency-sensitive workload, run an OTA canary, and check compliance."""
    from .. import cli as _  # noqa: F401  (keeps import graph explicit)

    cmd_seed(argparse.Namespace(data_dir=str(DEFAULT_DATA_DIR)), container)

    print("\n== Deploying 'pos-fraud-detector' near a Chicago retail cluster ==")
    workloads = container.workloads.list()
    target = next((w for w in workloads if "fraud" in w.workload_id or "fraud" in w.name.lower()), workloads[0])
    decision = container.workloads.deploy(target.workload_id, 41.8781, -87.6298)
    for d in decision.deployments:
        print(f"  -> {d.node_id}: {d.placement_reason}")
    if decision.cloud_burst_recommendation:
        print(f"  Recommendation: {decision.cloud_burst_recommendation}")

    print("\n== Starting an OTA canary rollout to retail_store nodes ==")
    campaign = container.ota.create_campaign(
        name="agent-2.4.0-rollout", target_firmware_version="2.4.0",
        node_selector={"node_type": "retail_store"}, canary_percent=10,
    )
    campaign = container.ota.start_canary(campaign.campaign_id)
    print(f"  Campaign {campaign.campaign_id}: {campaign.nodes_total} target nodes, canary wave dispatched.")

    print("\n== Compliance check: EU workload against a US node ==")
    eu_workload = next((w for w in workloads if w.data_residency_region == "eu"), None)
    us_node = next((n for n in container.registry.list() if n.location.country_code == "US"), None)
    if eu_workload and us_node:
        result = container.compliance.evaluate_placement(eu_workload, us_node)
        print(f"  {eu_workload.workload_id} on {us_node.node_id}: allowed={result.allowed} -- {result.reason}")

    print("\nDemo complete. Try `edge-orchestrator serve` and explore the HTTP API next.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edge-orchestrator", description="Distributed edge computing orchestration platform CLI")
    parser.add_argument("--db", default="edge_orchestrator.db", help="Path to the SQLite database file")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed", help="Load sample nodes/workloads into the database")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("serve", help="Start the HTTP API server")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)

    nodes = sub.add_parser("nodes", help="Inspect edge nodes")
    nodes_sub = nodes.add_subparsers(dest="nodes_command", required=True)
    p = nodes_sub.add_parser("list")
    p.add_argument("--region")
    p.add_argument("--type", dest="type")
    p.add_argument("--status")
    p.set_defaults(func=cmd_nodes_list)

    workloads = sub.add_parser("workloads", help="Inspect and deploy workloads")
    workloads_sub = workloads.add_subparsers(dest="workloads_command", required=True)
    p = workloads_sub.add_parser("list")
    p.set_defaults(func=cmd_workloads_list)
    p = workloads_sub.add_parser("deploy")
    p.add_argument("workload_id")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.set_defaults(func=cmd_workloads_deploy)

    updates = sub.add_parser("updates", help="Manage OTA update campaigns")
    updates_sub = updates.add_subparsers(dest="updates_command", required=True)
    p = updates_sub.add_parser("rollout")
    p.add_argument("--name", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--node-type")
    p.add_argument("--region")
    p.add_argument("--canary-percent", type=int, default=5)
    p.set_defaults(func=cmd_updates_rollout)
    p = updates_sub.add_parser("status")
    p.add_argument("campaign_id")
    p.set_defaults(func=cmd_updates_status)

    compliance = sub.add_parser("compliance", help="Evaluate data-residency compliance")
    compliance_sub = compliance.add_subparsers(dest="compliance_command", required=True)
    p = compliance_sub.add_parser("check")
    p.add_argument("workload_id")
    p.add_argument("node_id")
    p.set_defaults(func=cmd_compliance_check)

    p = sub.add_parser("demo", help="Run an end-to-end demo scenario against seeded sample data")
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    container = Container(db_path=args.db)
    configure_logging(container.settings.log_level)
    try:
        args.func(args, container)
    finally:
        container.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

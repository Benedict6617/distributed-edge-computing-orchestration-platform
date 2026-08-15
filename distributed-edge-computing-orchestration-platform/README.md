# Edge Orchestrator

**A dependency-free control plane for orchestrating containerized workloads across distributed edge fleets** — retail stores, factories, warehouses, kiosks, and leased cell-tower compute — with intermittent connectivity, hybrid cloud burst, 5G network slicing, service-mesh security, and data-sovereignty compliance built in.

Think "Kubernetes control-plane concepts," resized for a business running compute at hundreds or thousands of physical sites instead of inside one data center.

> Built for small and mid-sized multi-location businesses — retail chains, franchises, light manufacturing, logistics — that need to run software close to where their data is generated, without hiring a platform team to babysit it.

---

## Why this exists

Running software at "the edge" is a different problem from running it in a cloud region:

- **Connectivity is intermittent, not absent.** A store's LTE fails over, a factory Wi-Fi AP reboots, a tower's backhaul drops in weather. The control plane has to keep working *through* that, not just detect it.
- **Distance and cost trade off against each other.** The nearest node isn't always the cheapest; the cheapest isn't always fast enough. Placement has to be able to explain itself.
- **Firmware/software updates roll out to unattended hardware.** A bad canary at 2am at an unstaffed kiosk needs to stop itself, not page someone.
- **Data sovereignty is not optional.** EU customer data cannot casually run on a US node because that node happened to have free CPU.

This project models each of those problems directly rather than bolting them onto a generic scheduler.

## What's included

| Capability | Where |
|---|---|
| Node registry + heartbeat-based connectivity classification | `services/node_registry.py` |
| Latency-vs-cost aware scheduler (filter → score → bind) | `services/scheduler.py`, `services/cost_optimizer.py` |
| Offline/durable command queue for disconnected nodes | `services/connectivity.py` |
| Staged OTA update rollout with auto-pause on failure rate | `services/ota_manager.py` |
| Hardware health ingestion, threshold alerting | `services/health_monitor.py` |
| Prometheus-compatible `/metrics` endpoint | `services/metrics_exporter.py` |
| Data-sovereignty / residency policy engine + audit log | `services/compliance_engine.py` |
| 5G network slicing (eMBB / URLLC / mMTC) + SLA validation | `services/network_slicing.py` |
| Service mesh: workload identity, zero-trust policy, sidecar config generation | `services/service_mesh.py` |
| Hybrid cloud burst adapters (AWS / Azure / GCP mocks) | `services/hybrid_cloud.py` |
| REST API (32 endpoints) | `api/` |
| CLI (`edge-orchestrator ...`) | `cli/main.py` |
| Edge agent simulator (for demos / load-testing) | `agent/simulator.py` |
| Realistic sample fleet: 56 nodes, 10 workloads, historical metrics | `data/` |

## Design principles

1. **Zero third-party runtime dependencies.** Everything runs on the Python 3.9+ standard library: `sqlite3` for storage, `http.server` for the API, `argparse` for the CLI. Control planes for edge infrastructure are frequently deployed onto constrained management servers (or occasionally onto the edge hardware itself); a minimal dependency footprint means fewer supply-chain surfaces, faster cold starts, and no risk of the control plane's own dependencies drifting out from under it. `pytest` is an optional dev-only dependency for a nicer test runner — `python3 -m unittest` works with zero installs.
2. **Explainable placement.** Every scheduling decision records *why* a workload landed where it did (latency estimate, cost, utilization) — not just that it did.
3. **Connectivity loss is the default case to design for, not an edge case (pun intended).** Commands to disconnected nodes queue durably and replay on reconnect instead of silently failing.
4. **Compliance is enforced at the scheduler, not bolted on after.** A workload with a data-residency constraint simply cannot be scored onto a non-compliant node; every evaluation is audited.
5. **The project is honest about what's simulated.** OTA delivery, cloud bursting, and PKI issuance in this repo model the *control-plane contract* realistically; wiring them to real device fleets, cloud SDKs, or a certificate authority is called out explicitly in `docs/DEPLOYMENT.md` rather than pretended away.

## Quickstart

```bash
# 1. Install (editable, no external packages required)
pip install -e .

# 2. Try the guided demo — seeds sample data, deploys a workload,
#    starts an OTA canary, and runs a compliance check.
edge-orchestrator demo

# 3. Or start the API and explore it yourself
edge-orchestrator serve --port 8080 &
edge-orchestrator --db edge_orchestrator.db seed
curl http://localhost:8080/api/v1/nodes | head -40
```

No `pip install -e .`? It also runs directly:

```bash
PYTHONPATH=src python3 -m edge_orchestrator.cli.main demo
```

### CLI reference

```text
edge-orchestrator seed                                  # load data/sample_*.json into the DB
edge-orchestrator serve --host 0.0.0.0 --port 8080       # start the HTTP API
edge-orchestrator nodes list [--region] [--type]         # inspect the fleet
edge-orchestrator workloads list
edge-orchestrator workloads deploy <id> --lat X --lon Y  # schedule a workload
edge-orchestrator updates rollout --name N --version V --node-type retail_store
edge-orchestrator updates status <campaign_id>
edge-orchestrator compliance check <workload_id> <node_id>
edge-orchestrator demo                                   # end-to-end scripted walkthrough
```

Run `edge-orchestrator <command> --help` for full flag lists.

### API reference

See [`docs/API.md`](docs/API.md) for the full endpoint list with `curl` examples. Highlights:

```bash
# Register a node
curl -X POST localhost:8080/api/v1/nodes -H "Content-Type: application/json" -d @examples/node.json

# Deploy a workload near a given coordinate
curl -X POST localhost:8080/api/v1/workloads/wl-pos-fraud-detector/deploy \
     -d '{"origin_lat": 41.8781, "origin_lon": -87.6298}'

# Prometheus-format fleet metrics
curl localhost:8080/metrics
```

## Sample data

`data/` ships a realistic multi-site business fleet (regenerate any time with `python3 scripts/generate_sample_data.py`):

- **56 edge nodes** — 22 retail stores, 14 kiosks, 8 warehouses, 6 factory floors, 6 leased cell-tower compute sites — spread across US, EU, APAC, and Canada, each with realistic hardware specs, connection types, and hourly cost.
- **10 workloads** — POS fraud detection, in-store video analytics, factory predictive maintenance, IoT telemetry aggregation, inventory sync, an EU-only checkout service, warehouse robotics control, digital signage, an APAC loyalty service, and a 5G slice QoS probe — covering the full range of latency sensitivity and data-residency constraints.
- **1,120 historical health-metric rows** in `data/sample_metrics.csv` for dashboard/reporting prototyping.

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for component diagrams and the scheduling pipeline. See [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) for how data-sovereignty rules are modeled, and [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for running this in Docker, behind systemd, or (with the noted adapter work) against real cloud/PKI/device fleets.

## Testing

```bash
python3 -m unittest discover -s tests -v
# or, if you installed the [dev] extra:
pip install -e ".[dev]" && pytest
```

60 tests cover the scheduler, compliance engine, OTA rollout/rollback, connectivity queueing, cost optimizer, health monitor, network slicing, service mesh, hybrid cloud adapters, and the HTTP API end-to-end.

## Project layout

```
edge-orchestrator/
├── src/edge_orchestrator/
│   ├── core/            # models, config, db, logging
│   ├── services/         # scheduler, compliance, OTA, health, slicing, mesh, hybrid cloud...
│   ├── api/              # stdlib HTTP server + route handlers
│   ├── cli/               # argparse CLI
│   └── agent/            # edge node agent simulator
├── data/                  # sample fleet + compliance rules
├── scripts/               # sample data generator, demo script
├── tests/                 # unittest suite (60 tests)
├── docs/                  # architecture, API reference, deployment, compliance
├── deploy/                # Dockerfile, docker-compose, systemd unit
└── .github/workflows/     # CI (test on 3.9–3.12)
```

## Roadmap / not included

This is a control-plane reference implementation, not a finished commercial product. Deliberately out of scope, and called out rather than faked:

- A real edge-agent binary for physical hardware (the simulator in `agent/` stands in for load-testing/demo purposes)
- Live cloud SDK integration (adapters in `hybrid_cloud.py` are structured to be swapped for boto3/azure-mgmt/google-cloud-* without touching the scheduler)
- A production certificate authority for the service mesh (SPIFFE/SPIRE or Vault PKI, see `docs/DEPLOYMENT.md`)
- A real 5G core network-slice selection function integration (this models the orchestrator-side QoS contract, not a 3GPP core)

## License

MIT — see [`LICENSE`](LICENSE).

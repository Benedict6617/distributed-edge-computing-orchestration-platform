# Deployment Guide

## Running locally

```bash
pip install -e .
edge-orchestrator --db edge_orchestrator.db seed
edge-orchestrator serve --host 0.0.0.0 --port 8080
```

Or with zero install:

```bash
PYTHONPATH=src python3 -m edge_orchestrator.api.server
```

Configuration is layered (env vars > `config.json` > defaults) — see
`core/config.py`. Relevant environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `EDGE_ORCH_HOST` | `0.0.0.0` | API bind address |
| `EDGE_ORCH_PORT` | `8080` | API port |
| `EDGE_ORCH_DB_PATH` | `edge_orchestrator.db` | SQLite file path |
| `EDGE_ORCH_LOG_LEVEL` | `INFO` | Python logging level |
| `EDGE_ORCH_HEARTBEAT_TIMEOUT_S` | `120` | Seconds without a heartbeat before a node is OFFLINE |
| `EDGE_ORCH_DEGRADED_THRESHOLD_S` | `45` | Seconds without a heartbeat before a node is DEGRADED |
| `EDGE_ORCH_SCHEDULER_LATENCY_WEIGHT` | `0.6` | Fleet-default latency vs. cost weighting (0..1) |
| `EDGE_ORCH_MAX_OFFLINE_QUEUE` | `500` | Max queued commands per disconnected node |
| `EDGE_ORCH_OTA_MAX_FAILURE_RATE` | `10.0` | Default OTA auto-pause threshold (%) |

## Docker

```bash
docker build -t edge-orchestrator -f deploy/Dockerfile .
docker run -p 8080:8080 -v edge-orch-data:/data \
  -e EDGE_ORCH_DB_PATH=/data/edge_orchestrator.db \
  edge-orchestrator
```

The image is built `FROM python:3.12-slim` with **no `pip install` step**
— the control plane's zero-dependency design means the final image only
needs the standard library. This keeps the image small and avoids
rebuilding it whenever an upstream package cuts a release.

### docker-compose

`deploy/docker-compose.yml` brings up the control plane plus three
simulated edge-agent containers (see `agent/simulator.py`) reporting
telemetry against it, useful for demoing fleet-health dashboards without
real hardware:

```bash
docker compose -f deploy/docker-compose.yml up
```

## systemd (bare-metal management server)

```ini
# deploy/edge-orchestrator.service
[Unit]
Description=Edge Orchestrator control plane
After=network.target

[Service]
Type=simple
Environment=EDGE_ORCH_DB_PATH=/var/lib/edge-orchestrator/edge_orchestrator.db
Environment=EDGE_ORCH_LOG_LEVEL=INFO
ExecStart=/usr/bin/edge-orchestrator serve --host 0.0.0.0 --port 8080
Restart=on-failure
User=edge-orchestrator

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp deploy/edge-orchestrator.service /etc/systemd/system/
sudo systemctl enable --now edge-orchestrator
```

## Production hardening checklist

This repo is a reference control plane. Before running it against a real
fleet, add the following — each is a scoped, additive change rather than
a rewrite:

- **TLS + auth in front of the API.** `api/server.py` is a plain HTTP
  server by design (keeps the demo dependency-free); put it behind nginx,
  Caddy, or a cloud load balancer doing TLS termination and API-key /
  mTLS client auth. `service_mesh.py`'s identity issuance is the model
  for authenticating *workload-to-workload* traffic once deployed — it is
  not, by itself, a substitute for authenticating callers of the control
  plane's own API.
- **A real certificate authority for the mesh.** `service_mesh.py`
  simulates certificate fingerprints with `hashlib`/`secrets` to stay
  dependency-free. Swap in SPIFFE/SPIRE or a Vault PKI backend by
  replacing `issue_identity()`'s body — the rest of the mesh manager
  (policy storage, sidecar config generation) is unaffected.
- **A durable, replicated queue for `ConnectivityManager`.** The current
  implementation is in-process memory, which is fine for a single control
  plane instance but won't survive a restart or scale horizontally.
  Swap the internal dict-of-lists for a durable queue (an embedded WAL,
  Redis, or a message broker) behind the same `enqueue`/`drain_queue`
  interface.
- **Real cloud SDKs behind `hybrid_cloud.py`'s adapters.** `AWSAdapter`,
  `AzureAdapter`, and `GCPAdapter` currently return mock instances so the
  scheduler and cost optimizer can be exercised end-to-end without cloud
  credentials. Each adapter is a small, isolated class — replace
  `launch_instance`/`terminate_instance` with real `boto3` /
  `azure-mgmt-compute` / `google-cloud-compute` calls without touching
  the scheduler.
- **A real edge-agent binary.** `agent/simulator.py` is for demos and
  load-testing against this control plane. Production edge hardware needs
  an actual agent (a small Go/Rust/Python daemon) that pulls its OTA
  queue, applies container updates via the local container runtime, and
  reports real hardware telemetry.
- **Backups.** `EDGE_ORCH_DB_PATH` is a single SQLite file. Back it up
  like you would any other stateful service (snapshot, `sqlite3 .backup`,
  or point it at a mounted volume with your existing backup tooling).

## Scaling notes

SQLite's WAL mode (enabled automatically for file-backed databases —
see `core/db.py`) supports many concurrent readers and one writer, which
comfortably handles a control plane for a fleet of a few thousand nodes
on a single instance. Beyond that, the natural next step is moving the
document-store tables in `core/db.py` to Postgres (the schema is already
table-per-entity with a JSON document column, so this is a driver swap,
not a redesign) and running multiple stateless API instances behind a
load balancer, with `ConnectivityManager`'s queue moved to the durable
backend noted above.

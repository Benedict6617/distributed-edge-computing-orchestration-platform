# API Reference

Base URL: `http://<host>:<port>` (default `http://localhost:8080`)

All request/response bodies are JSON. There is no authentication layer in
this reference implementation — see `docs/DEPLOYMENT.md` for how to put
it behind a reverse proxy with mTLS or an API gateway in production.

## System

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness check; returns registered node count |
| GET | `/metrics` | Prometheus text-exposition-format fleet metrics |

```bash
curl localhost:8080/health
```

## Nodes

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/nodes` | List nodes. Query params: `region`, `node_type`, `status` |
| POST | `/api/v1/nodes` | Register a node |
| GET | `/api/v1/nodes/{node_id}` | Fetch one node |
| POST | `/api/v1/nodes/{node_id}/heartbeat` | Record a heartbeat (marks node ONLINE) |
| POST | `/api/v1/nodes/{node_id}/cordon` | Stop scheduling new workloads onto this node |
| POST | `/api/v1/nodes/{node_id}/uncordon` | Resume scheduling |

```bash
curl -X POST localhost:8080/api/v1/nodes -H "Content-Type: application/json" -d '{
  "name": "Downtown Store #42",
  "node_type": "retail_store",
  "location": {"latitude": 41.8781, "longitude": -87.6298, "region": "us-east", "country_code": "US"},
  "hardware": {"cpu_cores": 4, "memory_mb": 8192, "storage_gb": 64},
  "connection_type": "5g",
  "cost_per_hour_usd": 0.012
}'
```

## Workloads & deployments

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/workloads` | List workload definitions |
| POST | `/api/v1/workloads` | Define a workload |
| GET | `/api/v1/workloads/{workload_id}` | Fetch one workload |
| POST | `/api/v1/workloads/{workload_id}/deploy` | Schedule its replicas (`origin_lat`, `origin_lon` required) |
| GET | `/api/v1/deployments` | List all deployments |
| POST | `/api/v1/deployments/{deployment_id}/stop` | Stop a deployment and release its resources |

```bash
curl -X POST localhost:8080/api/v1/workloads -H "Content-Type: application/json" -d '{
  "name": "pos-fraud-detector",
  "container_image": "registry.internal/retail/fraud-detector:1.8.2",
  "resources": {"cpu_millicores": 1500, "memory_mb": 2048},
  "replicas": 3,
  "latency_sensitivity_ms": 80,
  "node_type_affinity": ["retail_store"],
  "slice_profile": "URLLC",
  "cost_priority": "latency"
}'

curl -X POST localhost:8080/api/v1/workloads/wl-pos-fraud-detector/deploy \
  -H "Content-Type: application/json" \
  -d '{"origin_lat": 41.8781, "origin_lon": -87.6298}'
```

## Health & telemetry

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/health/ingest` | Report a hardware telemetry sample |
| GET | `/api/v1/health/fleet` | Snapshot of every node's latest metric + connectivity |
| GET | `/api/v1/health/alerts` | Recent threshold-based alerts |
| POST | `/api/v1/health/sweep` | Force a connectivity-state reclassification sweep |

```bash
curl -X POST localhost:8080/api/v1/health/ingest -H "Content-Type: application/json" -d '{
  "node_id": "node-retail-store-001",
  "cpu_usage_pct": 42.5, "memory_usage_pct": 55.0,
  "disk_usage_pct": 30.0, "temperature_c": 48.5, "uptime_s": 86400
}'
```

## OTA updates

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/updates` | Create a campaign (`node_selector`, `canary_percent`, `max_failure_rate_pct`) |
| GET | `/api/v1/updates` | List campaigns |
| GET | `/api/v1/updates/{campaign_id}` | Fetch a campaign |
| POST | `/api/v1/updates/{campaign_id}/canary` | Push to the initial canary wave |
| POST | `/api/v1/updates/{campaign_id}/advance` | Push the next wave (`wave_percent`) if failure rate is within tolerance |
| POST | `/api/v1/updates/{campaign_id}/report` | Report a per-node update result (`node_id`, `success`) |
| POST | `/api/v1/updates/{campaign_id}/rollback` | Roll every targeted node back to `previous_version` |

```bash
curl -X POST localhost:8080/api/v1/updates -H "Content-Type: application/json" -d '{
  "name": "agent-2.4.0-rollout", "target_firmware_version": "2.4.0",
  "node_selector": {"node_type": "retail_store"}, "canary_percent": 10
}'
```

## 5G network slicing

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/slices` | List network slices |
| POST | `/api/v1/slices` | Create a slice (`profile`: `eMBB`\|`URLLC`\|`mMTC`, optional `qos_overrides`) |
| POST | `/api/v1/slices/{slice_id}/validate` | Validate observed latency/throughput against the slice's QoS SLA |

```bash
curl -X POST localhost:8080/api/v1/slices -H "Content-Type: application/json" \
  -d '{"name": "factory-line-1-urllc", "profile": "URLLC"}'
```

## Service mesh

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/mesh/policies` | List zero-trust traffic policies |
| POST | `/api/v1/mesh/policies` | Add a policy (`source_workload`, `destination_workload`, `allowed_ports`) |
| GET | `/api/v1/mesh/sidecar/{workload_name}?upstreams=a,b` | Generate an Envoy-compatible sidecar config |

## Compliance / data sovereignty

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/compliance/regions` | List configured data-residency rules |
| GET | `/api/v1/compliance/audit-log` | Recent placement compliance evaluations |
| POST | `/api/v1/compliance/validate` | Check `workload_id` against `node_id` |

## Hybrid cloud

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/cloud/regions` | List mock AWS/Azure/GCP regions and pricing |
| POST | `/api/v1/cloud/burst` | Launch a burst instance (`provider`, `region_code`, `workload_id`) |

## Error format

Non-2xx responses return `{"error": "..."}`. Handlers never raise
uncaught exceptions to the client — an internal error returns `500` with
`{"error": "internal server error", "detail": "..."}` and is logged as
structured JSON on the server.

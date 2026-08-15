# Architecture

## System overview

```mermaid
flowchart TB
    subgraph ControlPlane["Control Plane (this repo)"]
        API["HTTP API (stdlib http.server)"]
        CLI["CLI (argparse)"]
        Scheduler["Scheduler\n(filter → score → bind)"]
        Registry["Node Registry"]
        Connectivity["Connectivity Manager\n(offline command queue)"]
        Compliance["Compliance Engine"]
        Cost["Cost Optimizer"]
        OTA["OTA Manager"]
        Health["Health Monitor"]
        Slicing["Network Slice Manager"]
        Mesh["Service Mesh Manager"]
        Hybrid["Hybrid Cloud Manager"]
        DB[("SQLite\n(document store)")]
    end

    subgraph Fleet["Edge Fleet"]
        Store["Retail Store Node"]
        Factory["Factory Floor Node"]
        Tower["Cell Tower MEC Node"]
        Kiosk["Kiosk Node"]
    end

    subgraph Cloud["Hybrid Cloud"]
        AWS[(AWS burst capacity)]
        Azure[(Azure burst capacity)]
        GCP[(GCP burst capacity)]
    end

    CLI --> Scheduler
    API --> Scheduler
    API --> Registry
    API --> OTA
    API --> Health
    API --> Slicing
    API --> Mesh
    API --> Compliance
    API --> Hybrid

    Scheduler --> Registry
    Scheduler --> Compliance
    Scheduler --> Cost
    Scheduler -. "no compliant/available edge capacity" .-> Hybrid

    Registry <--> DB
    Compliance <--> DB
    OTA <--> DB
    Slicing <--> DB
    Mesh <--> DB

    OTA --> Connectivity
    Connectivity -. "queued while offline, replayed on reconnect" .-> Store
    Connectivity -. "queued while offline, replayed on reconnect" .-> Factory
    Connectivity -. "queued while offline, replayed on reconnect" .-> Tower
    Connectivity -. "queued while offline, replayed on reconnect" .-> Kiosk

    Store -- "heartbeat + telemetry" --> Health
    Factory -- "heartbeat + telemetry" --> Health
    Tower -- "heartbeat + telemetry" --> Health
    Kiosk -- "heartbeat + telemetry" --> Health
    Health --> Registry

    Hybrid --> AWS
    Hybrid --> Azure
    Hybrid --> GCP
```

## The scheduling pipeline

Each workload replica moves through four stages, mirroring the filter/score/bind
pattern used by Kubernetes' `kube-scheduler`, scoped to what matters for a
distributed edge fleet:

```mermaid
sequenceDiagram
    participant Client as CLI / API caller
    participant WM as Workload Manager
    participant Sched as Scheduler
    participant Comp as Compliance Engine
    participant Cost as Cost Optimizer
    participant Reg as Node Registry

    Client->>WM: deploy(workload_id, origin_lat, origin_lon)
    WM->>Sched: schedule(workload)
    loop for each replica
        Sched->>Reg: list candidate nodes
        Reg-->>Sched: nodes with headroom, READY, not OFFLINE
        Sched->>Comp: eligible_nodes(workload, candidates)
        Comp-->>Sched: compliant subset (audited)
        Sched->>Cost: score_node(node, workload) for each candidate
        Cost-->>Sched: latency/cost/headroom-weighted score
        Sched->>Reg: allocate(winning_node, cpu, mem)
        Sched-->>WM: Deployment(node_id, score, reason)
    end
    alt unplaced replicas remain
        Sched->>Cost: recommend_cloud_burst(...)
        Cost-->>Sched: human-readable burst recommendation
    end
    Sched-->>Client: SchedulingDecision
```

**Filter** eliminates nodes that cannot legally or physically run the
workload: insufficient CPU/memory, `OFFLINE` connectivity, node-type
affinity mismatch, or a data-residency violation.

**Score** ranks the surviving candidates using a linear weighted sum of
normalized latency, normalized cost, and a small headroom nudge away from
already-busy nodes. Workloads can shift the latency/cost balance via
`cost_priority: "latency" | "cost" | "balanced"`. The formula is
deliberately simple and inspectable — see `services/cost_optimizer.py` —
because "why did this land here" needs a plain-language answer for
compliance and cost review, not a black box.

**Bind** allocates resources on the winning node and persists a
`Deployment` record.

**Fallback**: if not all replicas can be placed, the scheduler asks the
cost optimizer whether a hybrid cloud burst (see `hybrid_cloud.py`) is a
better answer than leaving the workload under-replicated.

## Connectivity model

Edge fleets are defined by nodes that come and go. Rather than treating
connectivity as binary, the registry classifies each node from heartbeat
recency into three states:

```mermaid
stateDiagram-v2
    [*] --> ONLINE: heartbeat received
    ONLINE --> DEGRADED: no heartbeat for degraded_threshold_s
    DEGRADED --> OFFLINE: no heartbeat for heartbeat_timeout_s
    DEGRADED --> ONLINE: heartbeat received
    OFFLINE --> ONLINE: heartbeat received
```

While a node is `DEGRADED` or `OFFLINE`, control-plane intents (deploy,
stop, OTA update, policy change) are not dropped — they're placed on a
per-node durable queue (`services/connectivity.py`) and replayed once the
node's agent reconnects and calls `drain_queue`. This is the difference
between "the control plane thinks the store is fine because nothing
failed" and "the control plane correctly withheld new work from a store
it can't currently reach, and will catch it up the moment it's back."

## OTA rollout state machine

```mermaid
stateDiagram-v2
    [*] --> DRAFT: create_campaign
    DRAFT --> CANARY: start_canary (push to canary_percent% of targets)
    CANARY --> ROLLING_OUT: advance (failure rate within tolerance)
    ROLLING_OUT --> ROLLING_OUT: advance (more waves remain)
    ROLLING_OUT --> COMPLETED: advance (no targets remain)
    CANARY --> PAUSED: advance (failure rate exceeds threshold)
    ROLLING_OUT --> PAUSED: advance (failure rate exceeds threshold)
    PAUSED --> ROLLED_BACK: rollback
    CANARY --> ROLLED_BACK: rollback
    ROLLING_OUT --> ROLLED_BACK: rollback
```

## Latency estimation

`cost_optimizer.estimate_latency_ms` combines great-circle distance
(haversine) with a per-connection-type overhead constant (wired < 5G <
Wi-Fi < 4G-LTE < satellite) to produce a *relative* signal between
candidate nodes. It is explicitly not a claim of physical accuracy —
production deployments should replace it with measured round-trip time
from an active latency-probe mesh (each node periodically pinging a
shared set of anchors), which is a drop-in swap: the scheduler only needs
something that implements `estimate_latency_ms(node, origin) -> float`.

## Why SQLite-as-document-store instead of an ORM

Each service owns one table, storing entities as JSON with a handful of
indexed columns pulled out for the filters that service actually needs
(region, connectivity state, campaign state, etc). This keeps the project
dependency-free while still getting SQLite's atomic writes and WAL-mode
concurrent reads. It is intentionally *not* a time-series store — health
metrics are capped per node (`MAX_SAMPLES_PER_NODE` in
`health_monitor.py`) and old samples are trimmed, because a control
plane's job is current operational state, not long-term analytics. Wire a
real metrics backend (Prometheus + long-term storage, or a data
warehouse) for that — `/metrics` is already exposed in a
Prometheus-compatible format for exactly this purpose.

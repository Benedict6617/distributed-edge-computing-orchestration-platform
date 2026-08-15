#!/usr/bin/env python3
"""
Regenerates data/sample_nodes.json, data/sample_workloads.json, and
data/sample_metrics.csv with realistic, deterministically-seeded sample
data representing a mid-sized multi-site business fleet: retail stores,
factory floors, warehouses, kiosks, and a handful of telecom cell-tower
edge sites the business leases compute on.

Re-run any time you want a fresh (but reproducible -- fixed random seed)
sample fleet:

    python3 scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

random.seed(42)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

# (city, lat, lon, region, country_code)
SITES = [
    ("Chicago, IL", 41.8781, -87.6298, "us-east", "US"),
    ("New York, NY", 40.7128, -74.0060, "us-east", "US"),
    ("Atlanta, GA", 33.7490, -84.3880, "us-east", "US"),
    ("Dallas, TX", 32.7767, -96.7970, "us-east", "US"),
    ("Denver, CO", 39.7392, -104.9903, "us-west", "US"),
    ("Seattle, WA", 47.6062, -122.3321, "us-west", "US"),
    ("Phoenix, AZ", 33.4484, -112.0740, "us-west", "US"),
    ("Los Angeles, CA", 34.0522, -118.2437, "us-west", "US"),
    ("Berlin, Germany", 52.5200, 13.4050, "eu-central", "DE"),
    ("Munich, Germany", 48.1351, 11.5820, "eu-central", "DE"),
    ("Amsterdam, Netherlands", 52.3676, 4.9041, "eu-west", "NL"),
    ("Dublin, Ireland", 53.3498, -6.2603, "eu-west", "IE"),
    ("Paris, France", 48.8566, 2.3522, "eu-west", "FR"),
    ("Warsaw, Poland", 52.2297, 21.0122, "eu-central", "PL"),
    ("Singapore", 1.3521, 103.8198, "apac-sea", "SG"),
    ("Tokyo, Japan", 35.6762, 139.6503, "apac-ne", "JP"),
    ("Sydney, Australia", -33.8688, 151.2093, "apac-se", "AU"),
    ("Toronto, Canada", 43.6532, -79.3832, "ca-central", "CA"),
]

NODE_TYPE_SPECS = {
    # node_type: (hardware profile pool, connection types, cost/hr pool, name template)
    "retail_store": {
        "hw": [(4, 8192, 64), (8, 16384, 128)],
        "conn": ["5g", "wired", "wifi"],
        "cost": (0.008, 0.02),
        "names": ["POS Gateway", "Store Edge Node", "In-Store Compute"],
    },
    "factory": {
        "hw": [(8, 16384, 256), (16, 32768, 512)],
        "conn": ["wired", "5g"],
        "cost": (0.02, 0.05),
        "names": ["Line Controller Edge", "Plant Floor Gateway", "MES Edge Node"],
    },
    "warehouse": {
        "hw": [(8, 16384, 256), (4, 8192, 128)],
        "conn": ["wired", "wifi", "5g"],
        "cost": (0.015, 0.035),
        "names": ["Fulfillment Edge Node", "WMS Gateway", "Sortation Edge"],
    },
    "kiosk": {
        "hw": [(2, 4096, 32)],
        "conn": ["5g", "4g-lte", "wifi"],
        "cost": (0.004, 0.01),
        "names": ["Self-Checkout Kiosk", "Interactive Kiosk Node"],
    },
    "cell_tower": {
        "hw": [(16, 32768, 512), (32, 65536, 1024)],
        "conn": ["5g", "wired"],
        "cost": (0.03, 0.07),
        "names": ["MEC Compute Node", "Tower Edge Site"],
    },
}


def build_nodes():
    nodes = []
    counter = {}
    # Distribute a realistic small/mid-business footprint: many retail
    # stores and kiosks, a handful of factories/warehouses, a few leased
    # cell-tower MEC (multi-access edge compute) slots for the busiest metros.
    plan = (
        [("retail_store", 22)]
        + [("kiosk", 14)]
        + [("factory", 6)]
        + [("warehouse", 8)]
        + [("cell_tower", 6)]
    )
    for node_type, count in plan:
        spec = NODE_TYPE_SPECS[node_type]
        for _ in range(count):
            city, lat, lon, region, country = random.choice(SITES)
            jitter_lat = lat + random.uniform(-0.15, 0.15)
            jitter_lon = lon + random.uniform(-0.15, 0.15)
            cpu, mem, disk = random.choice(spec["hw"])
            conn = random.choice(spec["conn"])
            cost = round(random.uniform(*spec["cost"]), 4)
            counter[node_type] = counter.get(node_type, 0) + 1
            idx = counter[node_type]
            node_id = f"node-{node_type.replace('_','-')}-{idx:03d}"
            connectivity_state = random.choices(
                ["online", "degraded", "offline"], weights=[85, 10, 5]
            )[0]
            nodes.append(
                {
                    "node_id": node_id,
                    "name": f"{random.choice(spec['names'])} - {city}",
                    "node_type": node_type,
                    "location": {
                        "latitude": round(jitter_lat, 4),
                        "longitude": round(jitter_lon, 4),
                        "region": region,
                        "country_code": country,
                    },
                    "hardware": {
                        "cpu_cores": cpu,
                        "memory_mb": mem,
                        "storage_gb": disk,
                        "gpu": node_type == "factory" and random.random() < 0.3,
                        "architecture": random.choice(["arm64", "amd64"]),
                    },
                    "connectivity_state": connectivity_state,
                    "connection_type": conn,
                    "labels": {"site_city": city.split(",")[0].strip(), "tier": "edge"},
                    "cost_per_hour_usd": cost,
                    "firmware_version": random.choice(["2.3.1", "2.3.2", "2.4.0"]),
                }
            )
    return nodes


WORKLOADS = [
    {
        "workload_id": "wl-pos-fraud-detector",
        "name": "pos-fraud-detector",
        "container_image": "registry.internal/retail/fraud-detector:1.8.2",
        "resources": {"cpu_millicores": 1500, "memory_mb": 2048, "storage_gb": 5},
        "replicas": 3,
        "latency_sensitivity_ms": 80,
        "data_residency_region": None,
        "node_type_affinity": ["retail_store"],
        "slice_profile": "URLLC",
        "cost_priority": "latency",
        "env": {"MODEL_VERSION": "v4", "ALERT_WEBHOOK": "https://ops.internal/alerts/fraud"},
    },
    {
        "workload_id": "wl-video-analytics",
        "name": "store-video-analytics",
        "container_image": "registry.internal/retail/video-analytics:3.1.0",
        "resources": {"cpu_millicores": 3000, "memory_mb": 4096, "storage_gb": 20},
        "replicas": 4,
        "latency_sensitivity_ms": 150,
        "data_residency_region": None,
        "node_type_affinity": ["retail_store", "kiosk"],
        "slice_profile": "eMBB",
        "cost_priority": "balanced",
        "env": {"STREAM_FPS": "15", "RETENTION_HOURS": "48"},
    },
    {
        "workload_id": "wl-predictive-maintenance",
        "name": "factory-predictive-maintenance",
        "container_image": "registry.internal/manufacturing/pred-maint:2.0.4",
        "resources": {"cpu_millicores": 4000, "memory_mb": 8192, "storage_gb": 40},
        "replicas": 2,
        "latency_sensitivity_ms": 20,
        "data_residency_region": None,
        "node_type_affinity": ["factory"],
        "slice_profile": "URLLC",
        "cost_priority": "latency",
        "env": {"SENSOR_SAMPLE_HZ": "1000"},
    },
    {
        "workload_id": "wl-iot-telemetry-aggregator",
        "name": "iot-telemetry-aggregator",
        "container_image": "registry.internal/platform/telemetry-agg:1.5.0",
        "resources": {"cpu_millicores": 500, "memory_mb": 1024, "storage_gb": 10},
        "replicas": 6,
        "latency_sensitivity_ms": 400,
        "data_residency_region": None,
        "node_type_affinity": None,
        "slice_profile": "mMTC",
        "cost_priority": "cost",
        "env": {"BATCH_INTERVAL_S": "30"},
    },
    {
        "workload_id": "wl-inventory-sync",
        "name": "inventory-sync-agent",
        "container_image": "registry.internal/retail/inventory-sync:4.2.1",
        "resources": {"cpu_millicores": 500, "memory_mb": 512, "storage_gb": 2},
        "replicas": 10,
        "latency_sensitivity_ms": 1000,
        "data_residency_region": None,
        "node_type_affinity": ["retail_store", "warehouse"],
        "slice_profile": None,
        "cost_priority": "cost",
        "env": {"ERP_ENDPOINT": "https://erp.internal/api"},
    },
    {
        "workload_id": "wl-eu-customer-checkout",
        "name": "eu-customer-checkout-service",
        "container_image": "registry.internal/retail/checkout:5.0.1",
        "resources": {"cpu_millicores": 2000, "memory_mb": 2048, "storage_gb": 5},
        "replicas": 3,
        "latency_sensitivity_ms": 60,
        "data_residency_region": "eu",
        "node_type_affinity": ["retail_store"],
        "slice_profile": "URLLC",
        "cost_priority": "latency",
        "env": {"CURRENCY": "EUR", "GDPR_MODE": "strict"},
    },
    {
        "workload_id": "wl-warehouse-robotics-controller",
        "name": "warehouse-robotics-controller",
        "container_image": "registry.internal/logistics/robotics-ctrl:1.3.0",
        "resources": {"cpu_millicores": 3500, "memory_mb": 4096, "storage_gb": 15},
        "replicas": 2,
        "latency_sensitivity_ms": 10,
        "data_residency_region": None,
        "node_type_affinity": ["warehouse"],
        "slice_profile": "URLLC",
        "cost_priority": "latency",
        "env": {"SAFETY_INTERLOCK": "enabled"},
    },
    {
        "workload_id": "wl-digital-signage",
        "name": "kiosk-digital-signage",
        "container_image": "registry.internal/retail/signage:2.2.0",
        "resources": {"cpu_millicores": 300, "memory_mb": 512, "storage_gb": 8},
        "replicas": 8,
        "latency_sensitivity_ms": 2000,
        "data_residency_region": None,
        "node_type_affinity": ["kiosk"],
        "slice_profile": "eMBB",
        "cost_priority": "cost",
        "env": {"PLAYLIST_ID": "seasonal-2026"},
    },
    {
        "workload_id": "wl-apac-loyalty-service",
        "name": "apac-loyalty-service",
        "container_image": "registry.internal/retail/loyalty:3.4.0",
        "resources": {"cpu_millicores": 1000, "memory_mb": 1024, "storage_gb": 5},
        "replicas": 2,
        "latency_sensitivity_ms": 200,
        "data_residency_region": "apac",
        "node_type_affinity": ["retail_store", "kiosk"],
        "slice_profile": "eMBB",
        "cost_priority": "balanced",
        "env": {"REGION": "apac"},
    },
    {
        "workload_id": "wl-network-slicing-probe",
        "name": "5g-slice-qos-probe",
        "container_image": "registry.internal/platform/slice-probe:1.0.3",
        "resources": {"cpu_millicores": 200, "memory_mb": 256, "storage_gb": 1},
        "replicas": 5,
        "latency_sensitivity_ms": 5,
        "data_residency_region": None,
        "node_type_affinity": ["cell_tower"],
        "slice_profile": "URLLC",
        "cost_priority": "latency",
        "env": {"PROBE_INTERVAL_MS": "500"},
    },
]


def build_metrics_csv(nodes, rows_per_node=20):
    rows = []
    for node in nodes:
        base_cpu = random.uniform(15, 45)
        base_mem = random.uniform(25, 55)
        for i in range(rows_per_node):
            ts_offset_min = (rows_per_node - i) * 15
            rows.append(
                {
                    "node_id": node["node_id"],
                    "minutes_ago": ts_offset_min,
                    "cpu_usage_pct": round(min(99, max(2, base_cpu + random.uniform(-8, 20))), 1),
                    "memory_usage_pct": round(min(97, max(5, base_mem + random.uniform(-10, 15))), 1),
                    "disk_usage_pct": round(random.uniform(15, 75), 1),
                    "temperature_c": round(random.uniform(32, 68), 1),
                }
            )
    return rows


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    nodes = build_nodes()
    (DATA_DIR / "sample_nodes.json").write_text(json.dumps(nodes, indent=2) + "\n")
    (DATA_DIR / "sample_workloads.json").write_text(json.dumps(WORKLOADS, indent=2) + "\n")

    metrics_rows = build_metrics_csv(nodes)
    with open(DATA_DIR / "sample_metrics.csv", "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["node_id", "minutes_ago", "cpu_usage_pct", "memory_usage_pct", "disk_usage_pct", "temperature_c"]
        )
        writer.writeheader()
        writer.writerows(metrics_rows)

    print(f"Generated {len(nodes)} nodes, {len(WORKLOADS)} workloads, {len(metrics_rows)} metric rows.")


if __name__ == "__main__":
    main()

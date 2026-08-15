"""
Edge Agent Simulator
=====================

A stand-in for the lightweight agent that would run on real edge hardware
(a Raspberry Pi at a kiosk, an industrial PC on a factory floor, a compute
blade at a cell tower). Real agents are out of scope for a control-plane
repo, but fleets can't be demonstrated or load-tested without something on
the other end of the wire -- this simulator plays that role over the
plain HTTP API using only `urllib` from the standard library (no
`requests` dependency).

Usage:
    python3 -m edge_orchestrator.agent.simulator --api http://localhost:8080 \\
        --node-id node-us-chi-001 --duration 60
"""

from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request


def _post(api_base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{api_base}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        return {"error": str(e)}


def simulate_heartbeats(api_base: str, node_id: str, duration_s: int, interval_s: float, flap_probability: float):
    """Continuously reports synthetic hardware telemetry, occasionally
    simulating a connectivity gap by pausing reports (rather than sending
    an explicit 'offline' signal -- exactly how real connectivity loss
    looks to the control plane)."""
    end_at = time.time() + duration_s
    cpu_baseline = random.uniform(20, 45)
    while time.time() < end_at:
        if random.random() < flap_probability:
            gap = random.uniform(5, 20)
            print(f"[{node_id}] simulating connectivity gap for {gap:.1f}s")
            time.sleep(gap)
            continue

        metric = {
            "node_id": node_id,
            "cpu_usage_pct": round(min(99, max(1, cpu_baseline + random.uniform(-10, 25))), 1),
            "memory_usage_pct": round(random.uniform(30, 80), 1),
            "disk_usage_pct": round(random.uniform(20, 70), 1),
            "temperature_c": round(random.uniform(35, 65), 1),
            "uptime_s": int(time.time()),
        }
        result = _post(api_base, "/api/v1/health/ingest", metric)
        alerts = result.get("alerts_raised", [])
        status = f"cpu={metric['cpu_usage_pct']}% mem={metric['memory_usage_pct']}% temp={metric['temperature_c']}C"
        if alerts:
            status += f"  ALERTS={[a['metric'] for a in alerts]}"
        print(f"[{node_id}] {status}")
        time.sleep(interval_s)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Simulate an edge node agent reporting telemetry to the control plane")
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--duration", type=int, default=60, help="seconds to run")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between telemetry reports")
    parser.add_argument("--flap-probability", type=float, default=0.05, help="chance of a simulated connectivity gap per tick")
    args = parser.parse_args(argv)
    simulate_heartbeats(args.api, args.node_id, args.duration, args.interval, args.flap_probability)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

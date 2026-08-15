"""
Central configuration for the orchestrator control plane.

Configuration precedence (highest wins):
  1. Environment variables (EDGE_ORCH_*)
  2. config.json in the working directory (optional, git-ignored)
  3. Defaults defined below

This keeps local development, CI, and production container deployments
consistent without introducing a config-management dependency.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val is not None else default


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    return float(val) if val is not None else default


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_path: str
    log_level: str
    heartbeat_timeout_s: int          # node considered "offline" after this
    degraded_threshold_s: int         # node considered "degraded" after this
    scheduler_latency_weight: float   # 0..1, remainder goes to cost weight
    max_offline_queue_size: int
    default_ota_max_failure_rate_pct: float


def load_settings() -> Settings:
    config_file = Path(os.environ.get("EDGE_ORCH_CONFIG_FILE", "config.json"))
    file_overrides = {}
    if config_file.exists():
        try:
            file_overrides = json.loads(config_file.read_text())
        except (json.JSONDecodeError, OSError):
            file_overrides = {}

    def pick(env_name: str, json_key: str, caster, default):
        if env_name in os.environ:
            return caster(os.environ[env_name])
        if json_key in file_overrides:
            return caster(file_overrides[json_key])
        return default

    return Settings(
        host=pick("EDGE_ORCH_HOST", "host", str, "0.0.0.0"),
        port=pick("EDGE_ORCH_PORT", "port", int, 8080),
        db_path=pick("EDGE_ORCH_DB_PATH", "db_path", str, "edge_orchestrator.db"),
        log_level=pick("EDGE_ORCH_LOG_LEVEL", "log_level", str, "INFO"),
        heartbeat_timeout_s=pick("EDGE_ORCH_HEARTBEAT_TIMEOUT_S", "heartbeat_timeout_s", int, 120),
        degraded_threshold_s=pick("EDGE_ORCH_DEGRADED_THRESHOLD_S", "degraded_threshold_s", int, 45),
        scheduler_latency_weight=pick(
            "EDGE_ORCH_SCHEDULER_LATENCY_WEIGHT", "scheduler_latency_weight", float, 0.6
        ),
        max_offline_queue_size=pick("EDGE_ORCH_MAX_OFFLINE_QUEUE", "max_offline_queue_size", int, 500),
        default_ota_max_failure_rate_pct=pick(
            "EDGE_ORCH_OTA_MAX_FAILURE_RATE", "default_ota_max_failure_rate_pct", float, 10.0
        ),
    )


SETTINGS = load_settings()

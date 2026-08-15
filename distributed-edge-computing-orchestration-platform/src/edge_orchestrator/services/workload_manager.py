"""
Workload Manager
================

Public-facing service for defining workloads and deploying them onto the
fleet. Wraps the Scheduler with persistence and lifecycle bookkeeping so
API/CLI callers have a single entrypoint: `create`, `deploy`, `list`.
"""

from __future__ import annotations

import json
from typing import List, Optional

from ..core.db import Database, row_document
from ..core.logging_setup import get_logger
from ..core.models import Workload, new_id
from .scheduler import Scheduler, SchedulingDecision

logger = get_logger("edge_orchestrator.workload_manager")


class WorkloadManager:
    def __init__(self, db: Database, scheduler: Scheduler):
        self.db = db
        self.scheduler = scheduler

    def create(self, workload: Workload) -> Workload:
        if not workload.workload_id:
            workload.workload_id = new_id("wl")
        self.db.execute(
            "INSERT INTO workloads (workload_id, name, document) VALUES (?, ?, ?) "
            "ON CONFLICT(workload_id) DO UPDATE SET name=excluded.name, document=excluded.document",
            (workload.workload_id, workload.name, json.dumps(workload.to_dict())),
        )
        logger.info("workload created", extra={"extra_fields": {"workload_id": workload.workload_id}})
        return workload

    def get(self, workload_id: str) -> Optional[Workload]:
        row = self.db.query_one("SELECT * FROM workloads WHERE workload_id=?", (workload_id,))
        return Workload.from_dict(row_document(row)) if row else None

    def list(self) -> List[Workload]:
        rows = self.db.query("SELECT * FROM workloads ORDER BY workload_id")
        return [Workload.from_dict(row_document(r)) for r in rows]

    def deploy(self, workload_id: str, origin_lat: float, origin_lon: float) -> SchedulingDecision:
        workload = self.get(workload_id)
        if not workload:
            raise ValueError(f"unknown workload_id '{workload_id}'")
        return self.scheduler.schedule(workload, origin_lat, origin_lon)

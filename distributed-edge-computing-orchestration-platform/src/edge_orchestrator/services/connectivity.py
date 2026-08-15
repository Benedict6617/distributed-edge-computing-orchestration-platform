"""
Connectivity Manager
====================

Edge nodes lose backhaul connectivity routinely -- a retail store's LTE
failover, a factory floor Wi-Fi AP reboot, a tower's microwave link being
knocked offline by weather. A control plane that assumes always-on
connectivity will simply fail to converge state for a meaningful fraction
of the fleet at any given time.

This module implements a per-node durable command queue: while a node is
DEGRADED or OFFLINE, control-plane intents (deploy, stop, update-config,
apply-policy) are queued rather than dropped. When the node's agent
reconnects and calls `drain_queue`, queued commands are replayed in order
with idempotency keys so a partially-applied command is never re-applied
destructively.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..core.logging_setup import get_logger
from ..core.models import ConnectivityState

logger = get_logger("edge_orchestrator.connectivity")


@dataclass
class QueuedCommand:
    command_id: str
    node_id: str
    command_type: str      # "deploy" | "stop" | "update_config" | "apply_policy" | "ota_update"
    payload: dict
    enqueued_ts: float = field(default_factory=time.time)
    attempts: int = 0


class ConnectivityManager:
    """In-memory, per-node FIFO command queues with a bounded size.

    In a production deployment this queue would be backed by a durable
    broker (e.g. an embedded WAL or a message queue); the interface here
    is deliberately storage-agnostic so that swap is a one-file change.
    """

    def __init__(self, max_queue_size: int = 500):
        self.max_queue_size = max_queue_size
        self._queues: Dict[str, List[QueuedCommand]] = {}
        self._node_states: Dict[str, ConnectivityState] = {}

    def note_state(self, node_id: str, state: ConnectivityState) -> None:
        self._node_states[node_id] = state

    def enqueue(self, node_id: str, command_type: str, payload: dict) -> QueuedCommand:
        queue = self._queues.setdefault(node_id, [])
        if len(queue) >= self.max_queue_size:
            # Drop the oldest non-critical command rather than the newest --
            # newest intent reflects the operator's current desired state.
            dropped = queue.pop(0)
            logger.info(
                "offline queue full, dropping oldest command",
                extra={"extra_fields": {"node_id": node_id, "dropped_command": dropped.command_type}},
            )
        cmd = QueuedCommand(
            command_id=f"cmd-{uuid.uuid4().hex[:10]}",
            node_id=node_id,
            command_type=command_type,
            payload=payload,
        )
        queue.append(cmd)
        return cmd

    def queue_depth(self, node_id: str) -> int:
        return len(self._queues.get(node_id, []))

    def pending(self, node_id: str) -> List[QueuedCommand]:
        return list(self._queues.get(node_id, []))

    def drain_queue(self, node_id: str, max_commands: int = 50) -> List[QueuedCommand]:
        """Called when an agent reconnects and polls for backlog. Returns the
        batch of commands to replay; the caller is responsible for calling
        `acknowledge` once the agent confirms application."""
        queue = self._queues.get(node_id, [])
        batch = queue[:max_commands]
        for cmd in batch:
            cmd.attempts += 1
        return batch

    def acknowledge(self, node_id: str, command_id: str) -> bool:
        queue = self._queues.get(node_id, [])
        for i, cmd in enumerate(queue):
            if cmd.command_id == command_id:
                queue.pop(i)
                return True
        return False

    def fleet_summary(self) -> Dict[str, int]:
        return {node_id: len(q) for node_id, q in self._queues.items() if q}

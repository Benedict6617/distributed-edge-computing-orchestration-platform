"""
Persistence layer built on Python's built-in `sqlite3` module.

Design notes
------------
Each domain entity is stored as a JSON document in its own table, with a
small number of indexed columns pulled out for efficient filtering. This
"document store on top of SQLite" pattern avoids an ORM dependency while
still giving us WAL durability, atomic writes, and simple SQL for the
handful of queries the control plane actually needs.

The schema is intentionally small -- this is a control plane for metadata
and orchestration decisions, not a time-series database. High-volume
telemetry (see `health_metrics`) is capped and rolled off by the health
monitor service rather than kept forever.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    region TEXT NOT NULL,
    country_code TEXT NOT NULL,
    connectivity_state TEXT NOT NULL,
    status TEXT NOT NULL,
    last_heartbeat_ts REAL NOT NULL,
    document TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workloads (
    workload_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    document TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deployments (
    deployment_id TEXT PRIMARY KEY,
    workload_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    state TEXT NOT NULL,
    document TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_metrics_node ON health_metrics(node_id, timestamp);

CREATE TABLE IF NOT EXISTS ota_campaigns (
    campaign_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    document TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS network_slices (
    slice_id TEXT PRIMARY KEY,
    document TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mesh_policies (
    policy_id TEXT PRIMARY KEY,
    document TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compliance_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    document TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    document TEXT NOT NULL
);
"""


class Database:
    """Thread-safe wrapper around a single SQLite connection."""

    def __init__(self, path: str = ":memory:"):
        self._path = path
        self._is_memory = path == ":memory:"
        self._local = threading.local()
        self._lock = threading.RLock()
        # An in-memory SQLite database is scoped to the *connection* that
        # created it -- a thread-local connection pool would silently give
        # each request-handling thread its own empty database. Since the
        # threaded HTTP server (ThreadingHTTPServer) hands each request a
        # new thread, ":memory:" mode instead keeps a single shared
        # connection guarded by `_lock`. File-backed databases use WAL and
        # a connection-per-thread, which scales far better under concurrent
        # request load.
        if self._is_memory:
            self._shared_conn = self._connect()
            self._shared_conn.executescript(SCHEMA)
        else:
            with self._connect() as conn:
                conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._is_memory:
            return self._shared_conn
        if not hasattr(self._local, "conn"):
            self._local.conn = self._connect()
        return self._local.conn

    @contextmanager
    def transaction(self):
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        if self._is_memory:
            self._shared_conn.close()
        elif hasattr(self._local, "conn"):
            self._local.conn.close()


def row_document(row: sqlite3.Row) -> Dict[str, Any]:
    return json.loads(row["document"])

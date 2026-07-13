"""SQLite persistence for executors (condor.db at the repo root).

One row per executor: config + per-type state as JSON, coarse lifecycle
status, heartbeat. Written on every loop iteration and state transition
so a killed process can be reconciled on restart.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from condor.executors.base import ExecutorBase

_SCHEMA = """
CREATE TABLE IF NOT EXISTS executors (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    status       TEXT NOT NULL,
    agent_slug   TEXT NOT NULL DEFAULT '',
    agent_id     TEXT NOT NULL DEFAULT '',
    strategy     TEXT NOT NULL DEFAULT '',
    config       TEXT NOT NULL,
    state        TEXT NOT NULL,
    close_reason TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    heartbeat_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_executors_status ON executors(status);
"""

# Applied after column migrations (references columns older tables lack)
_POST_MIGRATION_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_executors_agent ON executors(agent_id);
CREATE INDEX IF NOT EXISTS idx_executors_agent_slug ON executors(agent_slug);
CREATE INDEX IF NOT EXISTS idx_executors_strategy ON executors(strategy);
"""


@dataclass
class ExecutorRecord:
    id: str
    type: str
    status: str
    agent_slug: str
    agent_id: str
    strategy: str
    config: dict
    state: dict
    close_reason: Optional[str]
    created_at: float
    updated_at: float
    heartbeat_at: float


_COLUMNS = ("id, type, status, agent_slug, agent_id, strategy, config, state,"
            " close_reason, created_at, updated_at, heartbeat_at")


def _slug_from_run_id(run_id: str) -> str:
    """Derive the agent slug from a run id: strips a trailing session
    ``_N``/``_eN`` or delegation ``-dN`` suffix. Slugs may contain
    underscores, so only the rightmost suffix is stripped."""
    import re

    m = re.match(r"^(.+?)(?:_e?\d+|-d\d+)$", run_id)
    return m.group(1) if m else run_id


class ExecutorStore:
    def __init__(self, db_path: str | Path = "condor.db"):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        # M0 -> M1 migration: table may predate the agent_id column
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(executors)")}
        if "agent_id" not in cols:
            self._conn.execute(
                "ALTER TABLE executors ADD COLUMN agent_id TEXT NOT NULL DEFAULT ''"
            )
        # M2 -> M3 migration: agent_slug + strategy columns; backfill slug
        # from existing run ids (strategy stays '' — unknowable after the fact)
        if "agent_slug" not in cols:
            self._conn.execute(
                "ALTER TABLE executors ADD COLUMN agent_slug TEXT NOT NULL DEFAULT ''"
            )
            self._conn.execute(
                "ALTER TABLE executors ADD COLUMN strategy TEXT NOT NULL DEFAULT ''"
            )
            for (eid, run_id) in self._conn.execute(
                "SELECT id, agent_id FROM executors WHERE agent_id != ''"
            ).fetchall():
                self._conn.execute(
                    "UPDATE executors SET agent_slug = ? WHERE id = ?",
                    (_slug_from_run_id(run_id), eid),
                )
        self._conn.executescript(_POST_MIGRATION_SCHEMA)
        self._conn.commit()

    def save(self, executor: "ExecutorBase") -> None:
        now = time.time()
        state_json = executor.state_model().model_dump_json()
        config_json = executor.config.model_dump_json()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO executors (id, type, status, agent_slug, agent_id, strategy,
                                       config, state, close_reason,
                                       created_at, updated_at, heartbeat_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    state = excluded.state,
                    close_reason = excluded.close_reason,
                    updated_at = excluded.updated_at,
                    heartbeat_at = excluded.heartbeat_at
                """,
                (
                    executor.id,
                    executor.config.type,
                    executor.status.value,
                    executor.config.agent_slug
                    or _slug_from_run_id(executor.config.agent_id),
                    executor.config.agent_id,
                    executor.config.strategy,
                    config_json,
                    state_json,
                    executor.close_reason,
                    now,
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def mark(self, executor_id: str, status: str, close_reason: Optional[str] = None) -> None:
        """Update status/reason without an executor instance (reconcile/watchdog)."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE executors SET status = ?, close_reason = COALESCE(?, close_reason),"
                " updated_at = ? WHERE id = ?",
                (status, close_reason, now, executor_id),
            )
            self._conn.commit()

    def load(self, executor_id: str) -> Optional[ExecutorRecord]:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM executors WHERE id = ?", (executor_id,)
        ).fetchone()
        return self._to_record(row) if row else None

    def load_non_terminal(self) -> list[ExecutorRecord]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM executors"
            " WHERE status IN ('PENDING', 'ACTIVE', 'CLOSING') ORDER BY created_at"
        ).fetchall()
        return [self._to_record(r) for r in rows]

    def load_by_agent(self, agent_id: str, limit: int = 100) -> list[ExecutorRecord]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM executors WHERE agent_id = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        return [self._to_record(r) for r in rows]

    def load_by_slug(self, agent_slug: str, limit: int = 500) -> list[ExecutorRecord]:
        """Everything an agent ever did, across all its sessions and delegations."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM executors WHERE agent_slug = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (agent_slug, limit),
        ).fetchall()
        return [self._to_record(r) for r in rows]

    def list_all(self, limit: int = 50) -> list[ExecutorRecord]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM executors ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_record(r) for r in rows]

    @staticmethod
    def _to_record(row) -> ExecutorRecord:
        return ExecutorRecord(
            id=row[0],
            type=row[1],
            status=row[2],
            agent_slug=row[3],
            agent_id=row[4],
            strategy=row[5],
            config=json.loads(row[6]),
            state=json.loads(row[7]),
            close_reason=row[8],
            created_at=row[9],
            updated_at=row[10],
            heartbeat_at=row[11],
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

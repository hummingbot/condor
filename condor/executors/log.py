"""Per-agent append-only transaction log for executors.

The durable half of the executor state model (see
``docs/executor-store-migration-plan.md``): live state lives in the daemon's
RAM (barriers enforced at machine speed); the chain is the truth; this log is
the append-only record of executor *lifecycle events* that lets a restarted
daemon rebuild the open set and reconcile it against the chain, and lets
performance fold settled trades. It replaces the single-file SQLite
``ExecutorStore``.

Layout — one file per agent slug, one JSON object per line:

    agents/{slug}/executors.jsonl

Each line is one event: ``opened`` (carries the full barrier ``config`` + the
open ``state`` snapshot), ``closed``/``failed`` (terminal, carries the final
state + reason), or a plain non-terminal ``update``. A record is reconstructed
by folding its events in file order (config from the opener, state last-wins,
status from the last event). Append-only + a single writer (the daemon) means no
locking beyond an in-process lock and no atomic full-file rewrite.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

from condor.executors.records import ExecutorRecord

if TYPE_CHECKING:
    from condor.executors.base import ExecutorBase

# Slug used for executors created without an agent (chat / CLI / manual).
_MANUAL_SLUG = "_manual"
_TERMINAL = {"CLOSED", "FAILED"}
_LOG_NAME = "executors.jsonl"

log = logging.getLogger(__name__)


class ExecutorLog:
    """Append-only executor lifecycle log, one file per agent slug.

    Same read surface as ``ExecutorStore`` (``save``/``mark``/``load``/
    ``load_non_terminal``/``load_by_agent``/``load_by_slug``/``list_all``) so the
    runtime can swap stores without changing call sites.
    """

    def __init__(self, agents_root: str | Path = "agents"):
        self.agents_root = Path(agents_root)
        self._lock = threading.Lock()

    # -- paths ---------------------------------------------------------------

    def _dir_for_slug(self, slug: str) -> Path:
        return self.agents_root / (slug or _MANUAL_SLUG)

    def _path_for_slug(self, slug: str) -> Path:
        return self._dir_for_slug(slug) / _LOG_NAME

    def _all_paths(self) -> list[Path]:
        if not self.agents_root.is_dir():
            return []
        return sorted(self.agents_root.glob(f"*/{_LOG_NAME}"))

    # -- write (append-only) -------------------------------------------------

    def _append(self, slug: str, obj: dict) -> None:
        """Serialized durable append (§3 durability contract).

        ``ExecutorBase.persist()`` dedups to transitions, so every append IS a
        recovery-relevant transition — each one is fsynced. Directories are
        0700 and files 0600 (same posture as the sealed store and control
        socket). Before appending, a torn tail left by a crash mid-write is
        truncated so the file always ends on a complete line.
        """
        path = self._path_for_slug(slug)
        line = json.dumps(obj)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path.parent, 0o700)
            except OSError:
                pass
            self._truncate_torn_tail(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    @staticmethod
    def _truncate_torn_tail(path: Path) -> None:
        """Drop a partial final line (crash mid-append) before the next write.

        The fold already ignores it on read; truncating here keeps the file
        itself always well-formed once a new writer takes over.
        """
        if not path.exists():
            return
        size = path.stat().st_size
        if size == 0:
            return
        with path.open("rb+") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) == b"\n":
                return
            # Scan back to the last complete line boundary.
            fh.seek(0)
            data = fh.read()
            cut = data.rfind(b"\n")
            keep = cut + 1 if cut >= 0 else 0
            fh.truncate(keep)
        log.warning(
            "executor log %s: truncated torn tail (%d -> %d bytes)",
            path, size, keep,
        )

    def save(self, executor: "ExecutorBase") -> None:
        """Append a lifecycle event snapshotting this executor's current state.

        Event kind is derived from status: terminal -> ``closed``/``failed``,
        else ``opened`` (the config-bearing opener) — under the transaction-log
        model this is called on transitions, not every tick.
        """
        status = executor.status.value
        # The slug routes the record to its agent's log file. Run ids are
        # opaque ULIDs (§7.1) — nothing derives a slug from one, so an
        # attributed executor must carry its agent_slug explicitly.
        slug = executor.config.agent_slug
        if executor.config.agent_id and not slug:
            raise ValueError(
                f"executor {executor.id} carries agent_id "
                f"{executor.config.agent_id!r} without agent_slug — run ids "
                "are opaque; attribution requires the explicit slug"
            )
        obj = {
            "ts": time.time(),
            "event": self._event_for_status(status),
            "id": executor.id,
            "type": executor.config.type,
            "agent_slug": slug,
            "agent_id": executor.config.agent_id,
            "strategy": executor.config.strategy,
            "status": status,
            "close_reason": executor.close_reason,
            # opaque per-type JSON, exactly as the SQLite store serialized it
            "config": json.loads(executor.config.model_dump_json()),
            "state": json.loads(executor.state_model().model_dump_json()),
        }
        self._append(slug, obj)

    def mark(
        self, executor_id: str, status: str, close_reason: Optional[str] = None
    ) -> None:
        """Append a status-only terminal event (reconcile/watchdog path).

        No executor instance in hand, so the prior state is kept by the fold;
        the slug is resolved from the id's existing log entries.
        """
        slug = self._slug_of(executor_id)
        if slug is None:
            raise KeyError(f"executor {executor_id} not found in any agent log")
        self._append(
            slug,
            {
                "ts": time.time(),
                "event": self._event_for_status(status),
                "id": executor_id,
                "status": status,
                "close_reason": close_reason,
            },
        )

    @staticmethod
    def _event_for_status(status: str) -> str:
        return {"CLOSED": "closed", "FAILED": "failed"}.get(status, "opened")

    # -- read (fold events into records) -------------------------------------

    def load(self, executor_id: str) -> Optional[ExecutorRecord]:
        for path in self._all_paths():
            rec = self._fold(self._read(path)).get(executor_id)
            if rec is not None:
                return rec
        return None

    def load_non_terminal(self) -> list[ExecutorRecord]:
        out = [
            rec
            for path in self._all_paths()
            for rec in self._fold(self._read(path)).values()
            if rec.status not in _TERMINAL
        ]
        out.sort(key=lambda r: r.created_at)
        return out

    def load_by_agent(self, agent_id: str, limit: int = 100) -> list[ExecutorRecord]:
        """Records attributed to one run id. Run ids are opaque (no slug is
        derivable), so this scans every agent's log — the per-slug fan-out is
        small and the fold is already file-local."""
        recs = [
            r
            for path in self._all_paths()
            for r in self._fold(self._read(path)).values()
            if r.agent_id == agent_id
        ]
        recs.sort(key=lambda r: r.created_at, reverse=True)
        return recs[:limit]

    def load_by_slug(self, agent_slug: str, limit: int = 500) -> list[ExecutorRecord]:
        recs = list(self._fold(self._read(self._path_for_slug(agent_slug))).values())
        recs.sort(key=lambda r: r.created_at, reverse=True)
        return recs[:limit]

    def list_all(self, limit: int = 50) -> list[ExecutorRecord]:
        recs = [
            rec
            for path in self._all_paths()
            for rec in self._fold(self._read(path)).values()
        ]
        recs.sort(key=lambda r: r.created_at, reverse=True)
        return recs[:limit]

    def close(self) -> None:  # parity with ExecutorStore.close(); nothing to flush
        pass

    # -- internals -----------------------------------------------------------

    def _slug_of(self, executor_id: str) -> Optional[str]:
        for path in self._all_paths():
            for obj in self._read(path):
                if obj.get("id") == executor_id:
                    # slug is the directory name (…/{slug}/executors.jsonl)
                    return path.parent.name
        return None

    @staticmethod
    def _read(path: Path) -> list[dict]:
        """Read + parse events, tolerating a torn FINAL line (§3).

        A partial final line (crash mid-append) is ignored; a corrupt line
        anywhere else is a real store corruption and still raises.
        """
        if not path.exists():
            return []
        events: list[dict] = []
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(json.loads(stripped))
            except json.JSONDecodeError:
                if i == len(lines) - 1:
                    log.warning(
                        "executor log %s: ignoring torn final line", path
                    )
                    continue
                raise
        return events

    @staticmethod
    def _fold(events: Iterable[dict]) -> dict[str, ExecutorRecord]:
        """Fold a slug's events into one record per executor id.

        config: from the first event that carries it (immutable intent).
        state / status / close_reason: last-wins.
        created_at: first event ts; updated_at/heartbeat_at: last event ts.
        """
        recs: dict[str, ExecutorRecord] = {}
        for e in events:
            eid = e["id"]
            ts = e.get("ts", 0.0)
            rec = recs.get(eid)
            if rec is None:
                rec = ExecutorRecord(
                    id=eid,
                    type=e.get("type", ""),
                    status=e.get("status", ""),
                    agent_slug=e.get("agent_slug", ""),
                    agent_id=e.get("agent_id", ""),
                    strategy=e.get("strategy", ""),
                    config=e.get("config") or {},
                    state=e.get("state") or {},
                    close_reason=e.get("close_reason"),
                    created_at=ts,
                    updated_at=ts,
                    heartbeat_at=ts,
                )
                recs[eid] = rec
                continue
            # fold subsequent events onto the existing record
            if e.get("type"):
                rec.type = e["type"]
            if e.get("agent_slug"):
                rec.agent_slug = e["agent_slug"]
            if e.get("agent_id"):
                rec.agent_id = e["agent_id"]
            if e.get("strategy"):
                rec.strategy = e["strategy"]
            if e.get("config"):
                rec.config = e["config"]
            if e.get("state"):
                rec.state = e["state"]
            if e.get("status"):
                rec.status = e["status"]
            if e.get("close_reason") is not None:
                rec.close_reason = e["close_reason"]
            rec.updated_at = ts
            rec.heartbeat_at = ts
        return recs

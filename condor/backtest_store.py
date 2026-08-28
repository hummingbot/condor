"""Two-tier persistence for backtest results: a summary index and a payload file.

A saved backtest is really two objects with different lifetimes, sizes and
access patterns, and treating them as one is what made this store expensive
(FEAT-075):

* The **summary** — ~1.6 KB of provenance, window and the 21 engine metrics. It
  is read on every dashboard poll, it is what ranking and comparison actually
  consume, and it must outlive everything. It lives in ``_index.json``.
* The **payload** — the whole task envelope, dominated by ``processed_data``
  (29 columns × ~130k rows, serialized as a dict of dicts) and
  ``pnl_timeseries``. Individual files run to 137 MB. It is read only when a
  human opens a chart, and it is worthless once the window it covers stops
  being interesting. It lives in ``<task_id>.json.gz``.

Splitting them is what makes listing cheap (index only, never a payload open),
makes the archive server-agnostic (scoping by server becomes a filter rather
than a precondition), and makes retention possible at all — a payload can be
pruned while its metrics survive forever.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from condor import paths
from condor.fsutil import atomic_write_bytes, atomic_write_json

logger = logging.getLogger(__name__)

#: Index schema version. ``1`` (or a missing key) is the flat
#: ``{task_id: {server, config}}`` map this store used to write.
INDEX_VERSION = 2

#: How long a payload is kept. Summaries are never pruned. ``0`` disables
#: pruning entirely.
DEFAULT_RETENTION_DAYS = 30

#: How often ``save_result`` bothers to check for prunable payloads. The check
#: is a scan of the index, not of the disk, but there is no reason to pay it on
#: every write.
_PRUNE_INTERVAL = 86400

_GZIP_LEVEL = 6


def retention_days() -> int:
    """Payload retention in days, honouring ``CONDOR_BACKTEST_RETENTION_DAYS``.

    Read per call rather than at import so the override stays observable to a
    test and to an operator who edits ``.env`` and restarts nothing but the
    routine that reads it.
    """
    raw = os.getenv("CONDOR_BACKTEST_RETENTION_DAYS")
    if raw is None or raw.strip() == "":
        return DEFAULT_RETENTION_DAYS
    try:
        return max(0, int(float(raw)))
    except ValueError:
        logger.warning("Ignoring unreadable CONDOR_BACKTEST_RETENTION_DAYS=%r", raw)
        return DEFAULT_RETENTION_DAYS


class BacktestStore:
    """Persist backtest results as a summary index plus a gzipped payload."""

    def __init__(self, data_dir: Path | None = None) -> None:
        # Resolved here rather than as a default argument: a default binds the
        # directory at import, which is exactly the cwd-relative constant this
        # replaced, and $CONDOR_DATA_DIR has to stay observable after import.
        data_dir = paths.backtests_dir() if data_dir is None else Path(data_dir)
        self._dir = data_dir
        self._index_path = data_dir / "_index.json"
        # task_id -> summary (see _summarize). Guarded by _lock because the
        # v2 migration runs in a thread executor while requests read the index.
        self._index: dict[str, dict[str, Any]] = {}
        self._meta: dict[str, Any] = {"v": INDEX_VERSION, "migrated": True}
        self._lock = threading.RLock()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load_index()
        self._migrate_legacy()

    # -- public API --

    @property
    def migrated(self) -> bool:
        """False while v1 files still hold summaries nobody has derived yet.

        Exposed on the archive response so the dashboard can say "indexing…"
        instead of quietly listing half a store with no metrics.
        """
        return bool(self._meta.get("migrated", True))

    def save_result(self, server: str, task_id: str, result: dict[str, Any]) -> None:
        """Persist one task envelope and its summary. Signature unchanged."""
        task = {"server": server, **result}
        self._write_payload(task_id, task)
        with self._lock:
            self._index[task_id] = _summarize(server, task_id, task)
            self._persist_index()
        self._maybe_prune()

    def get_result(self, task_id: str) -> dict[str, Any] | None:
        """The full envelope, or None when there is no payload to read.

        None means "no payload", which is *not* the same as "unknown task":
        a pruned run still has a summary. Callers that need to tell the two
        apart ask :meth:`get_summary`.
        """
        if task_id not in self._index:
            return None
        return self._read_payload(task_id)

    def list_summaries(self, server: str | None = None) -> list[dict[str, Any]]:
        """Every summary, newest first. Never opens a payload file.

        ``server=None`` spans every server: a backtest is a computation over
        candles, and the server is only the box that ran it.
        """
        with self._lock:
            summaries = [dict(s) for s in self._index.values()]
        if server:
            summaries = [s for s in summaries if s.get("server") == server]
        summaries.sort(key=_ordering_key, reverse=True)
        return summaries

    def get_summary(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            summary = self._index.get(task_id)
            return dict(summary) if summary else None

    def resolve_task_id(self, prefix: str) -> str | list[str] | None:
        """Resolve an exact id or a unique prefix.

        Returns the id, the list of candidates when the prefix is ambiguous, or
        None when nothing matches. The one copy of a lookup that used to be
        hand-rolled over ``store._index`` in three places.
        """
        with self._lock:
            known = list(self._index.keys())
        if prefix in known:
            return prefix
        matches = sorted(t for t in known if t.startswith(prefix))
        if not matches:
            return None
        if len(matches) > 1:
            return matches
        return matches[0]

    def known_task_ids(self) -> list[str]:
        """Every task id the index knows, newest first."""
        return [s["task_id"] for s in self.list_summaries()]

    def has_payload(self, task_id: str) -> bool:
        """Whether a chart could still be rendered for this task."""
        summary = self.get_summary(task_id)
        if summary is None:
            return False
        if not summary.get("has_payload", True):
            return False
        return (
            self._payload_path(task_id).exists() or self._legacy_path(task_id).exists()
        )

    def prune_payloads(self, max_age_days: int | None = None) -> int:
        """Delete payloads older than the cutoff; keep every summary.

        Returns how many payloads were deleted. ``max_age_days=0`` is a no-op,
        which is how the retention override disables pruning.
        """
        max_age = retention_days() if max_age_days is None else max_age_days
        with self._lock:
            self._meta["last_pruned"] = time.time()
            self._persist_index()
        if max_age <= 0:
            return 0

        cutoff = time.time() - max_age * 86400
        deleted = 0
        with self._lock:
            candidates = [
                (tid, dict(s))
                for tid, s in self._index.items()
                if s.get("has_payload", True)
            ]
        for task_id, summary in candidates:
            if self._payload_age_key(task_id, summary) >= cutoff:
                continue
            self._unlink_payload(task_id)
            with self._lock:
                stored = self._index.get(task_id)
                if stored is not None:
                    stored["has_payload"] = False
            deleted += 1
        if deleted:
            with self._lock:
                self._persist_index()
            logger.info(
                "Pruned %d backtest payload(s) older than %dd", deleted, max_age
            )
        return deleted

    def delete_result(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._index:
                return False
            del self._index[task_id]
            self._persist_index()
        self._unlink_payload(task_id)
        return True

    def migrate(self) -> int:
        """Bring a v1 store to v2: derive every summary, gzip every payload.

        Per file and idempotent, so a crash resumes where it stopped. Each file
        is parsed once — that parse is what produces the summary, and the
        summary is the thing that has to survive — but a payload already past
        the retention cutoff is then *deleted* rather than compressed, which is
        where the expensive half of the work is skipped.

        Runs off the boot path (see ``migrate_backtest_archive``): 39 files cost
        roughly 0.7 s to parse and 2.5 s to compress each.
        """
        legacy = sorted(
            p
            for p in self._dir.glob("*.json")
            if p.name != self._index_path.name and not p.name.startswith(".")
        )
        if not legacy and self.migrated:
            return 0

        cutoff = time.time() - retention_days() * 86400 if retention_days() else None
        converted = 0
        for path in legacy:
            task_id = path.stem
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Skipping unreadable backtest file %s", path)
                continue
            if not isinstance(task, dict):
                logger.warning("Skipping non-object backtest file %s", path)
                continue

            summary = _summarize(task.get("server", ""), task_id, task)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = time.time()
            if cutoff is not None and _ordering_key(summary, mtime) < cutoff:
                path.unlink(missing_ok=True)
                summary["has_payload"] = False
            else:
                self._write_payload(task_id, task)
                path.unlink(missing_ok=True)
            with self._lock:
                self._index[task_id] = summary
                self._persist_index()
            converted += 1

        # A v1 entry with no file on disk can never be described -- the parse
        # that would have produced its summary has nothing to read -- so it
        # would list as a permanent "unknown" row with no metrics. Dropping it
        # is the honest end state; the run it named is already gone.
        with self._lock:
            orphans = [
                tid
                for tid, summary in self._index.items()
                if summary.get("status") == "unknown"
                and not self._payload_path(tid).exists()
                and not self._legacy_path(tid).exists()
            ]
            for tid in orphans:
                del self._index[tid]
            self._meta["v"] = INDEX_VERSION
            self._meta["migrated"] = True
            self._persist_index()
        if orphans:
            logger.info("Dropped %d backtest index entries with no file", len(orphans))
        if converted:
            logger.info("Migrated %d backtest file(s) to the v2 archive", converted)
        self.prune_payloads()
        return converted

    # -- internals --

    def _payload_path(self, task_id: str) -> Path:
        return self._dir / f"{_safe_name(task_id)}.json.gz"

    def _legacy_path(self, task_id: str) -> Path:
        """The uncompressed file a not-yet-migrated task still lives in."""
        return self._dir / f"{_safe_name(task_id)}.json"

    def _read_payload(self, task_id: str) -> dict[str, Any] | None:
        path = self._payload_path(task_id)
        try:
            if path.exists():
                return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
            legacy = self._legacy_path(task_id)
            if legacy.exists():
                return json.loads(legacy.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to read backtest payload %s", path)
        return None

    def _write_payload(self, task_id: str, task: dict[str, Any]) -> None:
        raw = json.dumps(task, separators=(",", ":"), default=str).encode("utf-8")
        atomic_write_bytes(self._payload_path(task_id), gzip.compress(raw, _GZIP_LEVEL))

    def _unlink_payload(self, task_id: str) -> None:
        self._payload_path(task_id).unlink(missing_ok=True)
        self._legacy_path(task_id).unlink(missing_ok=True)

    def _payload_age_key(self, task_id: str, summary: dict[str, Any]) -> float:
        """When this run finished, for retention purposes.

        ``completed_at`` is authoritative. A summary derived from a v1 envelope
        that carried neither timestamp falls back to the payload's mtime, which
        is what ordering used before this store had a real clock.
        """
        try:
            mtime = self._payload_path(task_id).stat().st_mtime
        except OSError:
            try:
                mtime = self._legacy_path(task_id).stat().st_mtime
            except OSError:
                mtime = 0.0
        return _ordering_key(summary, mtime)

    def _maybe_prune(self) -> None:
        last = float(self._meta.get("last_pruned") or 0)
        if time.time() - last < _PRUNE_INTERVAL:
            return
        try:
            self.prune_payloads()
        except Exception:
            logger.warning("Backtest payload prune failed", exc_info=True)

    def _persist_index(self) -> None:
        atomic_write_json(self._index_path, {**self._meta, "tasks": self._index})

    def _load_index(self) -> None:
        if not self._index_path.exists():
            return
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load backtest index, rebuilding")
            self._rebuild_index()
            return
        if not isinstance(raw, dict):
            self._rebuild_index()
            return

        if raw.get("v") == INDEX_VERSION:
            tasks = raw.get("tasks")
            self._index = tasks if isinstance(tasks, dict) else {}
            self._meta = {k: v for k, v in raw.items() if k != "tasks"}
            self._meta.setdefault("migrated", True)
            return

        # v1: a flat {task_id: {server, config}} map. Each entry becomes a
        # degraded summary -- enough to list and to resolve an id, honestly
        # marked as carrying no metrics -- until migrate() derives the real one.
        self._index = {
            tid: _degraded_summary(tid, meta)
            for tid, meta in raw.items()
            if isinstance(meta, dict)
        }
        self._meta = {"v": INDEX_VERSION, "migrated": False}

    def _rebuild_index(self) -> None:
        """Rebuild the index from whatever files are on disk."""
        self._index = {}
        self._meta = {"v": INDEX_VERSION, "migrated": True}
        for path in sorted(self._dir.glob("*.json.gz")):
            task_id = path.name[: -len(".json.gz")]
            try:
                task = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
            except Exception:
                logger.warning("Skipping corrupt backtest file %s", path)
                continue
            if isinstance(task, dict):
                self._index[task_id] = _summarize(task.get("server", ""), task_id, task)
        if any(
            p.name != self._index_path.name and not p.name.startswith(".")
            for p in self._dir.glob("*.json")
        ):
            # Uncompressed files are still out there; migrate() owns them.
            self._meta["migrated"] = False
        self._persist_index()

    def _migrate_legacy(self) -> None:
        """Fold the single-file ``backtests.json`` store into per-task files."""
        legacy_file = paths.legacy_backtests_file()
        if not legacy_file.exists():
            return
        try:
            legacy_data = json.loads(legacy_file.read_text(encoding="utf-8"))
            if not isinstance(legacy_data, dict) or not legacy_data:
                legacy_file.unlink()
                return
            logger.info(
                "Migrating %d backtest results from legacy store", len(legacy_data)
            )
            for task_id, entry in legacy_data.items():
                if not isinstance(entry, dict):
                    continue
                server = entry.pop("server", "")
                self.save_result(server, task_id, entry)
            legacy_file.unlink()
            logger.info("Legacy backtest store migrated and removed")
        except Exception:
            logger.warning("Failed to migrate legacy backtest store", exc_info=True)


# -- summaries --


def _safe_name(task_id: str) -> str:
    return task_id.replace("/", "_").replace("..", "_")


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(value: Any) -> float | None:
    """A task timestamp as epoch seconds, whatever shape it arrived in.

    The API server writes ``created_at``/``completed_at`` as ISO-8601 strings
    (``2026-08-24T18:11:04.300751+00:00``); routines and the legacy store wrote
    floats. Both have to reduce to one clock, because this is the key the
    archive orders and expires by -- a timestamp that silently parsed to
    nothing would order every run equally and prune by file mtime forever.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # A naive stamp is UTC here, not the reader's local zone: writers are
        # servers, and .timestamp() would otherwise shift by the offset.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _summarize(server: str, task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    """Derive the listing/ranking tier from a full task envelope.

    The one place the summary shape is defined. Tolerates every key being
    absent: a failed task has no ``result`` and still deserves a summary
    carrying its status and error, and a config shape that drifts must produce
    an empty field rather than an exception that loses the run.
    """
    config = task.get("config")
    config = config if isinstance(config, dict) else {}
    controller = config.get("config")
    controller = controller if isinstance(controller, dict) else {}
    result = task.get("result")
    result = result if isinstance(result, dict) else {}
    metrics = result.get("results")
    metrics = metrics if isinstance(metrics, dict) else {}

    created_at = _epoch(task.get("created_at"))
    completed_at = _epoch(task.get("completed_at"))
    if completed_at is None and task.get("status") == "completed":
        completed_at = created_at

    return {
        "task_id": task_id,
        "server": server or task.get("server") or "",
        "status": str(task.get("status") or ""),
        "config_id": str(controller.get("id") or config.get("config_id") or ""),
        "controller": str(controller.get("controller_name") or ""),
        "trading_pair": str(controller.get("trading_pair") or ""),
        "connector": str(controller.get("connector_name") or ""),
        "start_time": _num(config.get("start_time")) or 0,
        "end_time": _num(config.get("end_time")) or 0,
        "resolution": str(config.get("backtesting_resolution") or ""),
        "trade_cost": _num(config.get("trade_cost")),
        "created_at": created_at,
        "completed_at": completed_at,
        "metrics": metrics,
        "error": str(task.get("error") or "") or None,
        "has_payload": True,
    }


def _degraded_summary(task_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    """A v1 index entry, widened to the summary shape without opening a file.

    ``status: "unknown"`` and empty metrics are the honest answer until
    :meth:`BacktestStore.migrate` parses the payload — the alternative, claiming
    ``completed`` with no numbers, is what makes a half-indexed archive lie.
    """
    return {
        "task_id": task_id,
        "server": str(meta.get("server") or ""),
        "status": "unknown",
        "config_id": str(meta.get("config") or ""),
        "controller": "",
        "trading_pair": "",
        "connector": "",
        "start_time": 0,
        "end_time": 0,
        "resolution": "",
        "trade_cost": None,
        "created_at": None,
        "completed_at": None,
        "metrics": {},
        "error": None,
        "has_payload": True,
    }


def _ordering_key(summary: dict[str, Any], fallback: float = 0.0) -> float:
    """When a run finished: ``completed_at``, then ``created_at``, then fallback.

    Ordering used to be the payload file's mtime, which stops existing the
    moment a payload can be pruned — hence a real clock in the summary.
    """
    for key in ("completed_at", "created_at"):
        value = _epoch(summary.get(key))
        if value:
            return value
    return fallback


# Singleton
_store: BacktestStore | None = None


def get_backtest_store() -> BacktestStore:
    global _store
    if _store is None:
        _store = BacktestStore()
    return _store


def migrate_backtest_archive() -> int:
    """Entry point for the boot-time background migration (``main.py``)."""
    try:
        return get_backtest_store().migrate()
    except Exception:
        logger.warning("Backtest archive migration failed", exc_info=True)
        return 0

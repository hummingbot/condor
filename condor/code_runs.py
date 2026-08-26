"""Persistence for ad-hoc code runs (FEAT-047).

Same shape as :mod:`condor.backtest_store` and the report index: one JSON file
per run under :func:`condor.paths.code_runs_dir` plus a light ``_index.json``
so listing a history needs no per-file reads. No sqlite anywhere in this repo,
and a store that holds a few hundred short records whose main consumer reads
one record at a time does not earn the first one.

The store keeps the **full** text of every run — code, stdout, result,
traceback. Clipping for a model's context is the caller's job, so a snippet
that failed can always be read back whole and corrected.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from condor import paths
from condor.fsutil import atomic_write_json

logger = logging.getLogger(__name__)

# Retention bound. Also the only thing standing between a snippet that printed a
# credential and an unbounded pile of them on disk (see FEAT-047 risks).
MAX_RUNS = 300

# Ids are generated here, but they also arrive from a tool argument — anything
# that is not this shape never becomes a path.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# The light meta the index carries, so `list` never opens a run file. Since
# FEAT-061 that includes the owner: a listing has to authorize its rows, and
# opening 300 files to learn who they belong to defeats the index's whole point.
_INDEX_FIELDS = (
    "id",
    "created",
    "agent",
    "label",
    "status",
    "error",
    "duration_ms",
    "user_id",
)


@dataclass
class CodeRun:
    """One execution of an ad-hoc snippet, exactly as it is persisted."""

    id: str
    created: float
    agent: str = ""  # attribution slug ("condor" | agent slug)
    # The authenticated caller who ran it. 0 means *unknown*, not public — it is
    # what every run written before FEAT-061 carries — and every reader that
    # filters by owner drops those (fail closed, the SEC-196 rule for reports).
    user_id: int = 0
    label: str = ""  # short purpose the caller passed ("returns of SOL 1h")
    server: str = ""
    status: str = "ok"  # "ok" | "error" | "timeout"
    duration_ms: int = 0
    code: str = ""
    stdout: str = ""
    result: str = ""  # the snippet's `result` global, rendered; "" if unset
    error: str = ""  # "ZeroDivisionError: division by zero"
    traceback: str = ""
    report_id: str = ""
    session_key: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def new_run_id() -> str:
    """``code_<epoch_ms>_<16 hex>`` — sortable by time, unique under concurrency.

    The suffix carries 64 bits: runs starting in the same millisecond are common
    (a loop, a burst of tool calls) and the store is keyed by this id, so a
    collision silently overwrites the earlier run instead of raising.
    """
    return f"code_{int(time.time() * 1000)}_{secrets.token_hex(8)}"


class CodeRunStore:
    """Per-file JSON persistence for code runs, newest-first index, capped."""

    def __init__(self, data_dir: Path | None = None) -> None:
        # paths.code_runs_dir() is repo-anchored, not cwd-relative: the main
        # process writes this dir and every MCP subprocess reads it, and they
        # need not agree on a working directory.
        self._dir = Path(data_dir) if data_dir is not None else paths.code_runs_dir()
        self._index_path = self._dir / "_index.json"
        self._index: list[dict] = []
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load_index()

    # ── public API ──

    def save(self, run: CodeRun) -> None:
        """Write the run, put it at the head of the index, evict past the cap."""
        data = run.to_dict()
        self._write_json(self._run_path(run.id), data)
        self._index = [e for e in self._index if e.get("id") != run.id]
        self._index.insert(0, {k: data.get(k) for k in _INDEX_FIELDS})
        self._evict()
        self._persist_index()

    def get(self, run_id: str) -> dict | None:
        """The full record, or None if it is unknown or unreadable."""
        path = self._run_path(run_id)
        if path is None or not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to read code run file %s", path)
            return None

    def list(
        self,
        agent: str | None = None,
        limit: int = 20,
        user_id: int | None = None,
    ) -> list[dict]:
        """Index meta only, newest first, optionally for one agent and owner.

        ``user_id=None`` is *no filter* — the admin scope, and what the MCP
        subprocess's own reads keep passing. An int is that owner and nobody
        else: an entry with no owner recorded is dropped rather than shared,
        because unowned means unknown and this store holds whatever a snippet
        printed (FEAT-061).
        """
        entries = self._index
        if agent:
            entries = [e for e in entries if e.get("agent") == agent]
        if user_id is not None:
            # ``0`` is the absence of an owner, never a filter that matches one:
            # an entry recorded before the field existed carries 0 (or nothing
            # at all, in an index written back then) and is dropped either way.
            entries = [
                e for e in entries if user_id and (e.get("user_id") or 0) == user_id
            ]
        return [dict(e) for e in entries[: max(0, limit)]]

    # ── internals ──

    def _run_path(self, run_id: str) -> Path | None:
        """The file for ``run_id``, or None when the id is not a legal id.

        Ids reach this store from a tool argument, so the shape check is what
        keeps ``get`` from being turned into an arbitrary file reader.
        """
        if not run_id or not _ID_RE.match(run_id):
            return None
        return self._dir / f"{run_id}.json"

    def _evict(self) -> None:
        """Drop the oldest runs past the cap — index entry *and* file."""
        for entry in self._index[MAX_RUNS:]:
            path = self._run_path(entry.get("id", ""))
            if path is not None and path.exists():
                try:
                    path.unlink()
                except OSError:
                    logger.warning("Could not evict code run file %s", path)
        del self._index[MAX_RUNS:]

    def _load_index(self) -> None:
        if not self._index_path.exists():
            return
        try:
            loaded = json.loads(self._index_path.read_text(encoding="utf-8"))
            self._index = loaded if isinstance(loaded, list) else []
        except Exception:
            logger.warning("Failed to load code run index, starting empty")
            self._index = []

    def _persist_index(self) -> None:
        self._write_json(self._index_path, self._index)

    def _write_json(self, path: Path | None, data) -> None:
        """Atomic create-or-replace, via the shared :mod:`condor.fsutil` helper."""
        if path is None:
            return
        atomic_write_json(path, data, ensure_ascii=False)


_store: CodeRunStore | None = None


def get_code_run_store() -> CodeRunStore:
    global _store
    if _store is None:
        _store = CodeRunStore()
    return _store

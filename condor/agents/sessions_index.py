"""Read-only index over the on-disk session/experiment layout of a strategy.

The layout itself (directory names, ``session_N`` / ``experiment_N.md`` naming,
journal and experiment file formats) is owned by :mod:`condor.agents.journal`;
this module provides the enumeration and lookup helpers that consumers (web
routes, MCP tools) use to browse that layout without re-implementing it.

All helpers take the strategy dir (``agents/{agent_slug}/strategies/{sslug}``)
and return plain data — no FastAPI/Pydantic dependencies.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from condor.agents.journal import count_journal_ticks

# New and legacy directory names, checked in order.
SESSION_DIRNAMES = ("sessions", "trading_sessions")
EXPERIMENT_DIRNAMES = ("dry_runs", "experiments")
_SNAPSHOT_DIRNAMES = ("snapshots", "runs")

_EXPERIMENT_FILE_RE = re.compile(r"experiment_(\d+)\.md")
_SNAPSHOT_FILE_RE = re.compile(r"(?:snapshot|run)_(\d+)\.md")
_SNAPSHOT_TITLE_RE = re.compile(r"^# (?:Snapshot|Tick) #\d+ — (.+)$", re.MULTILINE)


def infer_latest_session_status(
    strategy_dir: Path, run_key: str
) -> dict[str, Any] | None:
    """Status of the latest session on disk when no engine is in memory.

    Prefers the recorded status (FEAT-012) so a run the process died on reports
    ``interrupted`` rather than the ``idle`` this used to fabricate. Sessions
    written before status files existed have none, and those still fall back to
    ``idle`` — the honest answer when nothing was recorded.
    """
    from condor.runtime.registry_file import read_status

    session_dirs: list[Path] = []
    for dirname in SESSION_DIRNAMES:
        sessions_dir = strategy_dir / dirname
        if not sessions_dir.exists():
            continue
        session_dirs.extend(
            d
            for d in sessions_dir.iterdir()
            if d.is_dir() and d.name.startswith("session_")
        )
    if not session_dirs:
        return None

    latest = max(session_dirs, key=lambda d: d.stat().st_mtime)
    try:
        num = int(latest.name.split("_", 1)[1])
    except (ValueError, IndexError):
        return None

    status = read_status(latest) or {}
    return {
        "agent_id": status.get("agent_id") or f"{run_key}_{num}",
        "session_num": num,
        "status": status.get("state", "idle"),
        "tick_count": count_journal_ticks(latest / "journal.md"),
    }


def count_sessions(strategy_dir: Path) -> int:
    for dirname in SESSION_DIRNAMES:
        sessions_dir = strategy_dir / dirname
        if sessions_dir.exists():
            return len(
                [
                    d
                    for d in sessions_dir.iterdir()
                    if d.is_dir() and d.name.startswith("session_")
                ]
            )
    return 0


def count_experiments(strategy_dir: Path) -> int:
    count = 0
    for dirname in EXPERIMENT_DIRNAMES:
        d = strategy_dir / dirname
        if d.exists():
            count += len(
                [
                    f
                    for f in d.iterdir()
                    if f.is_file()
                    and f.suffix == ".md"
                    and f.name.startswith("experiment_")
                ]
            )
    return count


def list_sessions(strategy_dir: Path) -> list[dict[str, Any]]:
    """List sessions as dicts (number, snapshot_count, created_at), newest first."""
    sessions: list[dict[str, Any]] = []
    for dirname in SESSION_DIRNAMES:
        sessions_dir = strategy_dir / dirname
        if not sessions_dir.exists():
            continue
        for d in sorted(sessions_dir.iterdir(), reverse=True):
            if not d.is_dir() or not d.name.startswith("session_"):
                continue
            try:
                num = int(d.name.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            snap_count = 0
            for snap_dir_name in _SNAPSHOT_DIRNAMES:
                snap_dir = d / snap_dir_name
                if snap_dir.exists():
                    snap_count = len(list(snap_dir.glob("*.md")))
                    break
            created = ""
            journal_path = d / "journal.md"
            if journal_path.exists():
                created = str(os.path.getctime(journal_path))
            sessions.append(
                {"number": num, "snapshot_count": snap_count, "created_at": created}
            )
    return sessions


# Experiment snapshots are write-once (save_experiment_snapshot allocates a new
# number and writes each file exactly once), so an mtime-keyed cache avoids
# re-reading potentially hundreds of KB of .md files on every poll of the
# strategy detail endpoint.
_experiment_info_cache: dict[Path, tuple[float, dict[str, Any]]] = {}


def _parse_experiment_file(f: Path, num: int) -> dict[str, Any]:
    execution_mode = ""
    agent_key = ""
    content = f.read_text(errors="replace")
    mode_match = re.search(r"^Mode:\s*(\S+)", content, re.MULTILINE)
    if mode_match:
        execution_mode = mode_match.group(1)
    model_match = re.search(r"^Model:\s*(\S+)", content, re.MULTILINE)
    if model_match:
        agent_key = model_match.group(1)
    created = ""
    ts_match = re.search(r"^# Experiment #\d+ — (.+)$", content, re.MULTILINE)
    if ts_match:
        created = ts_match.group(1)
    # A tick whose model call failed writes the raw error string as its Agent
    # Response (e.g. "(error: status_code: 404, ...)"). Flag it so the UI can
    # mark the run as failed without opening it.
    error = bool(
        re.search(
            r"^## Agent Response\s*\n+\(?error\b",
            content,
            re.MULTILINE | re.IGNORECASE,
        )
    )
    return {
        "number": num,
        "execution_mode": execution_mode,
        "agent_key": agent_key,
        "snapshot_count": 1,
        "created_at": created,
        "error": error,
    }


def list_experiments(strategy_dir: Path) -> list[dict[str, Any]]:
    """List experiments as dicts (number, execution_mode, ...), newest first."""
    experiments: list[dict[str, Any]] = []
    all_files: list[Path] = []
    for dirname in EXPERIMENT_DIRNAMES:
        d = strategy_dir / dirname
        if d.exists():
            all_files.extend(d.glob("experiment_*.md"))
    stated = [(f, f.stat().st_mtime) for f in all_files]
    for f, mtime in sorted(stated, key=lambda x: x[1], reverse=True):
        m = _EXPERIMENT_FILE_RE.match(f.name)
        if not m:
            continue
        num = int(m.group(1))
        cached = _experiment_info_cache.get(f)
        if cached is not None and cached[0] == mtime:
            info = cached[1]
        else:
            info = _parse_experiment_file(f, num)
            _experiment_info_cache[f] = (mtime, info)
        experiments.append(info)
    return experiments


# Same idiom as _experiment_info_cache above, for the strictly worse case: a
# session holds up to MAX_SNAPSHOTS (100) dumps, each embedding the full system
# prompt and every tool call, and save_full_snapshot writes each file exactly
# once. Keyed by path with (mtime, size) so a rewritten file re-parses anyway.
_snapshot_info_cache: dict[Path, tuple[float, int, dict[str, Any]]] = {}


def _parse_snapshot_file(f: Path, tick: int) -> dict[str, Any]:
    """Summary fields of one snapshot: tick, timestamp, file.

    The timestamp is the tail of the title line, which ``SNAPSHOT_TEMPLATE``
    puts on line 1 — so the fast path reads only that line instead of pulling a
    multi-hundred-KB dump into memory. A file whose first line is not the title
    (legacy ``run_N.md`` layouts) falls back to scanning the rest, keeping the
    parsed result identical to a whole-file search.
    """
    timestamp = ""
    with f.open("r", errors="replace") as fh:
        first = fh.readline()
        m = _SNAPSHOT_TITLE_RE.match(first.rstrip("\n"))
        if m is None:
            m = _SNAPSHOT_TITLE_RE.search(first + fh.read())
        if m:
            timestamp = m.group(1)
    return {"tick": tick, "timestamp": timestamp, "file": f.name}


def list_session_snapshots(session_dir: Path) -> list[dict[str, Any]]:
    """List a session's snapshots as dicts (tick, timestamp, file), newest first.

    Only the first existing directory of ``_SNAPSHOT_DIRNAMES`` is read, so a
    session that still has a legacy ``runs/`` dir keeps resolving there.
    """
    for dirname in _SNAPSHOT_DIRNAMES:
        snap_dir = session_dir / dirname
        if not snap_dir.exists():
            continue
        stated: list[tuple[Path, float, int]] = []
        for f in snap_dir.glob("*.md"):
            try:
                st = f.stat()
            except OSError:  # unlinked between glob and stat
                continue
            stated.append((f, st.st_mtime, st.st_size))
        snapshots: list[dict[str, Any]] = []
        live: set[Path] = set()
        for f, mtime, size in sorted(stated, key=lambda x: x[1], reverse=True):
            m = _SNAPSHOT_FILE_RE.match(f.name)
            if not m:
                continue
            live.add(f)
            cached = _snapshot_info_cache.get(f)
            if cached is not None and cached[0] == mtime and cached[1] == size:
                info = cached[2]
            else:
                info = _parse_snapshot_file(f, int(m.group(1)))
                _snapshot_info_cache[f] = (mtime, size, info)
            snapshots.append(info)
        # _cleanup_old_snapshots unlinks the oldest files past MAX_SNAPSHOTS;
        # drop their entries so the cache tracks the directory, not history.
        for stale in [
            p for p in _snapshot_info_cache if p.parent == snap_dir and p not in live
        ]:
            del _snapshot_info_cache[stale]
        return snapshots
    return []


def enumerate_agent_ids(run_key: str, strategy_dir: Path) -> list[tuple[str, int, str]]:
    """Return (agent_id, session_num, kind) for every session and experiment on disk."""
    ids: list[tuple[str, int, str]] = []
    for dirname in SESSION_DIRNAMES:
        d = strategy_dir / dirname
        if not d.exists():
            continue
        for sd in d.iterdir():
            if not sd.is_dir() or not sd.name.startswith("session_"):
                continue
            try:
                n = int(sd.name.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            ids.append((f"{run_key}_{n}", n, "session"))
    for dirname in EXPERIMENT_DIRNAMES:
        d = strategy_dir / dirname
        if not d.exists():
            continue
        for f in d.glob("experiment_*.md"):
            m = _EXPERIMENT_FILE_RE.match(f.name)
            if not m:
                continue
            n = int(m.group(1))
            ids.append((f"{run_key}_e{n}", n, "experiment"))
    seen: set[str] = set()
    unique: list[tuple[str, int, str]] = []
    for tup in ids:
        if tup[0] in seen:
            continue
        seen.add(tup[0])
        unique.append(tup)
    return unique


def find_session_dir(strategy_dir: Path, session_num: int) -> Path | None:
    for dirname in SESSION_DIRNAMES:
        path = strategy_dir / dirname / f"session_{session_num}"
        if path.exists():
            return path
    return None


def find_experiment_file(strategy_dir: Path, experiment_num: int) -> Path | None:
    for dirname in EXPERIMENT_DIRNAMES:
        path = strategy_dir / dirname / f"experiment_{experiment_num}.md"
        if path.exists():
            return path
    return None

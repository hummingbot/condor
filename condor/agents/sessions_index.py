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

from condor.agents.journal import (
    SESSION_DIRNAMES,
    count_journal_ticks,
    iter_session_dirs,
)
from condor.agents.strategy import STRATEGIES_DIRNAME
from condor.memory.paths import agent_home

# New and legacy directory names, checked in order.
EXPERIMENT_DIRNAMES = ("dry_runs", "experiments")
_SNAPSHOT_DIRNAMES = ("snapshots", "runs")

_EXPERIMENT_FILE_RE = re.compile(r"experiment_(\d+)\.md")
# The agent_id format enumerate_agent_ids writes: "{run_key}_{N}" / "{run_key}_e{N}".
_AGENT_ID_RE = re.compile(r"(.+)_(e?)(\d+)")
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

    sessions = iter_session_dirs(strategy_dir)
    if not sessions:
        return None

    num, latest = max(sessions, key=lambda s: s[1].stat().st_mtime)

    status = read_status(latest) or {}
    return {
        "agent_id": status.get("agent_id") or f"{run_key}_{num}",
        "session_num": num,
        "status": status.get("state", "idle"),
        # Memoised (PERF-323): ``_build_strategy_summary`` calls this once per
        # strategy on every ``GET /api/v1/agents``, which the chat rail polls.
        "tick_count": _journal_tick_count(latest / "journal.md"),
    }


def count_sessions(strategy_dir: Path) -> int:
    return len(iter_session_dirs(strategy_dir))


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
    """List sessions as dicts (number, snapshot_count, created_at), newest first.

    Newest is the highest session number. ``created_at`` is ``journal.md``'s
    ctime as a string, or ``""`` when the session has no journal yet.
    """
    rows: list[dict[str, Any]] = []
    for num, session_dir in reversed(iter_session_dirs(strategy_dir)):
        created = _created_at(session_dir / "journal.md")
        rows.append(
            {
                "number": num,
                "snapshot_count": _snapshot_count(session_dir),
                "created_at": "" if created is None else str(created),
            }
        )
    return rows


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

    The timestamp is the tail of the title line, which ``save_full_snapshot``
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


# ── The runs index (FEAT-099) ──
#
# One row per *run* — a session or a dry run — for the Lab's rail. Disk only:
# no ``get_client``, no performance fan-out, nothing that could reach the
# Hummingbot API, which is what licenses the rail's 5s poll (the ``fleet-map``
# precedent). Money is deliberately absent; a run's PnL is looked up in the run
# overview, where the strategy's ``/performance`` query is already loaded.

# The journal is the only expensive read on this path: ``count_journal_ticks``
# pulls a whole ``journal.md``, once per session, on every poll. Memoised on
# (mtime, size) — the same idiom as the two caches above, and safe for the same
# reason: a journal only ever grows, so a changed file re-parses.
#
# ``infer_latest_session_status`` reads the same number through this memo too
# (PERF-323): it is the hotter of the two paths, since ``/api/v1/agents`` runs
# it once per strategy on a poll the chat rail never stops.
#
# Only the *count* is cached. A run's status and end time come from a small
# ``status.json`` read every time, because a live run's are exactly the fields
# that change between two polls.
_journal_ticks_cache: dict[Path, tuple[float, int, int]] = {}


def _journal_tick_count(journal_path: Path) -> int:
    try:
        st = journal_path.stat()
    except OSError:
        return 0
    cached = _journal_ticks_cache.get(journal_path)
    if cached is not None and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]
    count = count_journal_ticks(journal_path)
    _journal_ticks_cache[journal_path] = (st.st_mtime, st.st_size, count)
    return count


def _created_at(path: Path) -> float | None:
    """Creation time as a float, or ``None`` when the file is not there.

    ``list_sessions`` reports ``journal.md``'s ctime through this, and runs sort
    on the same fact rather than on a second definition of when a run began. It is
    the file's creation and not the first tick's timestamp — close enough to
    order a rail and to say "2h ago", never close enough to call a trade's start.
    """
    try:
        return os.path.getctime(path)
    except OSError:
        return None


def _snapshot_count(session_dir: Path) -> int:
    for dirname in _SNAPSHOT_DIRNAMES:
        snap_dir = session_dir / dirname
        if snap_dir.is_dir():
            return len(list(snap_dir.glob("*.md")))
    return 0


def _session_run(session_dir: Path, num: int, run_key: str) -> dict[str, Any]:
    """One session, as a run row."""
    from condor.agents.actions import ACTIONS_FILENAME
    from condor.runtime.registry_file import (
        LIVE_STATES,
        LoopState,
        is_stale,
        read_status,
    )

    status = read_status(session_dir) or {}
    # A live state stamped by a boot that is not ours belongs to a process that
    # died without recording an end — FEAT-012's distinction, and the only
    # honest label for it.
    state = status.get("state") or "idle"
    if is_stale(status):
        state = "interrupted"
    elif state == LoopState.SUSPENDED:
        # A shutdown wound this run down and the next boot will settle it into a
        # stopped run or a fresh session. To a reader it is over either way, and
        # "suspended" is a word about the process, not about this run.
        state = LoopState.STOPPED
    live = state in LIVE_STATES
    ended = status.get("updated_at")

    return {
        "run_id": f"s{num}",
        "kind": "session",
        "number": num,
        "agent_id": status.get("agent_id") or f"{run_key}_{num}",
        "status": state,
        "execution_mode": "",
        "tick_count": _journal_tick_count(session_dir / "journal.md"),
        "snapshot_count": _snapshot_count(session_dir),
        "started_at": _created_at(session_dir / "journal.md"),
        # A run still going has no end. Otherwise the last heartbeat is the
        # closest recorded thing to one.
        "ended_at": (
            None if live or not isinstance(ended, (int, float)) else float(ended)
        ),
        "error": state == "error",
        # The spine colours a tick with no deeds differently depending on this:
        # a run that recorded nothing did nothing, a run written before the log
        # existed recorded nothing about what it did. Nothing is backfilled, so
        # the distinction is the file's presence and only that.
        "has_actions_log": (session_dir / ACTIONS_FILENAME).is_file(),
    }


def _experiment_run(path: Path, info: dict[str, Any], run_key: str) -> dict[str, Any]:
    """One dry run / single tick, as a run row.

    A single tick has no journal, no snapshot directory and no status file: it
    is one file that was written once. So its whole lifecycle is that file's
    timestamps, and its only recorded outcome is whether the tick itself failed.
    """
    num = int(info["number"])
    try:
        ended: float | None = path.stat().st_mtime
    except OSError:
        ended = None
    return {
        "run_id": f"e{num}",
        "kind": "experiment",
        "number": num,
        "agent_id": f"{run_key}_e{num}",
        # Finished the moment it was written; "errored" when the tick's model
        # call failed, which `_parse_experiment_file` already reads off the file.
        "status": "error" if info.get("error") else "stopped",
        "execution_mode": info.get("execution_mode", ""),
        "tick_count": 1,
        "snapshot_count": 1,
        "started_at": _created_at(path),
        "ended_at": ended,
        "error": bool(info.get("error")),
        "has_actions_log": False,
    }


def list_runs(strategy_dir: Path, run_key: str) -> list[dict[str, Any]]:
    """Every run of one strategy — sessions and experiments — newest first.

    ``run_id`` is the rail's ``?run=`` value: ``s3`` for ``session_3``, ``e1``
    for ``experiment_1.md``. Unique within a strategy, which is all the URL
    needs, since the strategy is a separate parameter.
    """
    runs = [
        _session_run(session_dir, num, run_key)
        for num, session_dir in iter_session_dirs(strategy_dir)
    ]

    for info in list_experiments(strategy_dir):
        path = find_experiment_file(strategy_dir, int(info["number"]))
        if path is None:
            continue
        runs.append(_experiment_run(path, info, run_key))

    runs.sort(key=lambda r: (r["started_at"] or 0.0, r["number"]), reverse=True)
    return runs


def parse_agent_id(agent_id: str) -> tuple[str, int, str] | None:
    """Split an agent_id into ``(run_key, number, kind)``; None if malformed.

    The inverse of :func:`enumerate_agent_ids`: ``"{run_key}_{N}"`` is a
    ``"session"`` and ``"{run_key}_e{N}"`` an ``"experiment"``.
    """
    m = _AGENT_ID_RE.fullmatch(agent_id)
    if not m:
        return None
    run_key, exp, num = m.groups()
    return run_key, int(num), "experiment" if exp else "session"


def strategy_dir_for_run_key(run_key: str) -> Path | None:
    """The strategy folder a ``"{agent_slug}.{strategy_slug}"`` run key names.

    The same composition as ``Strategy.home``, without going through the
    StrategyStore, which would refuse a deleted strategy whose session dirs
    are still on disk. None for a key without the ``agent.strategy`` shape.
    """
    agent_slug, dot, slug = run_key.partition(".")
    if not dot:
        return None
    return agent_home(agent_slug) / STRATEGIES_DIRNAME / slug


def enumerate_agent_ids(run_key: str, strategy_dir: Path) -> list[tuple[str, int, str]]:
    """Return (agent_id, session_num, kind) for every session and experiment on disk."""
    ids: list[tuple[str, int, str]] = [
        (f"{run_key}_{n}", n, "session") for n, _ in iter_session_dirs(strategy_dir)
    ]
    # Experiments can sit in both a current and a legacy directory too; the
    # first one listed keeps the number.
    seen: set[int] = set()
    for dirname in EXPERIMENT_DIRNAMES:
        d = strategy_dir / dirname
        if not d.exists():
            continue
        for f in d.glob("experiment_*.md"):
            m = _EXPERIMENT_FILE_RE.match(f.name)
            if not m:
                continue
            n = int(m.group(1))
            if n in seen:
                continue
            seen.add(n)
            ids.append((f"{run_key}_e{n}", n, "experiment"))
    return ids


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

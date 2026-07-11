"""Read-only index over the on-disk session/experiment layout of an agent.

The layout itself (directory names, ``session_N`` / ``experiment_N.md`` naming,
``meta.yml``, journal and experiment file formats) is owned by
:mod:`condor.agents.journal`; this module provides the enumeration and lookup
helpers that consumers (web routes, MCP tools) use to browse that layout
without re-implementing it.

All helpers take the agent dir (``agents/{agent_slug}``) and return plain data
— no FastAPI/Pydantic dependencies. Sessions carry their ``meta.yml`` fields
(``kind``, ``strategy``, ``status``, …); readers tolerate missing metas
(crashed husks) by treating them as ``kind: tick_loop`` with unknown status.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from condor.agents.journal import count_journal_ticks, read_session_meta

_EXPERIMENT_FILE_RE = re.compile(r"experiment_(\d+)\.md")

# Session kinds that represent attributed trading runs (perf rollups, status).
TICK_KIND = "tick_loop"


def _iter_session_dirs(agent_dir: Path):
    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.exists():
        return
    for d in sessions_dir.iterdir():
        if not d.is_dir() or not d.name.startswith("session_"):
            continue
        try:
            num = int(d.name.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        yield num, d


def _session_kind(meta: dict) -> str:
    # Metaless husks predate meta.yml or crashed before writing it; every one
    # of those was a tick session (delegations/consults always write meta first).
    return meta.get("kind") or TICK_KIND


def infer_latest_session_status(agent_dir: Path, slug: str) -> dict[str, Any] | None:
    """Infer status from the latest tick session on disk when no engine is in memory."""
    tick_sessions = [
        (num, d)
        for num, d in _iter_session_dirs(agent_dir)
        if _session_kind(read_session_meta(d)) == TICK_KIND
    ]
    if not tick_sessions:
        return None

    num, latest = max(tick_sessions, key=lambda t: t[1].stat().st_mtime)
    # If no engine is in memory, the agent is not running — idle metadata only.
    return {
        "agent_id": f"{slug}_{num}",
        "session_num": num,
        "status": "idle",
        "strategy": read_session_meta(latest).get("strategy", ""),
        "tick_count": count_journal_ticks(latest / "journal.md"),
    }


def count_sessions(agent_dir: Path, kind: str | None = None) -> int:
    if kind is None:
        return sum(1 for _ in _iter_session_dirs(agent_dir))
    return sum(
        1
        for _, d in _iter_session_dirs(agent_dir)
        if _session_kind(read_session_meta(d)) == kind
    )


def count_experiments(agent_dir: Path) -> int:
    d = agent_dir / "dry_runs"
    if not d.exists():
        return 0
    return len(
        [
            f
            for f in d.iterdir()
            if f.is_file() and f.suffix == ".md" and f.name.startswith("experiment_")
        ]
    )


def list_sessions(
    agent_dir: Path, strategy: str | None = None, kind: str | None = None
) -> list[dict[str, Any]]:
    """List sessions as dicts (number, kind, strategy, status, …), newest first.

    ``strategy``/``kind`` filter on the meta.yml fields — the strategy filter is
    what gives per-playbook track records under the agent-level pool.
    """
    sessions: list[dict[str, Any]] = []
    for num, d in sorted(_iter_session_dirs(agent_dir), reverse=True):
        meta = read_session_meta(d)
        s_kind = _session_kind(meta)
        s_strategy = meta.get("strategy", "")
        if kind is not None and s_kind != kind:
            continue
        if strategy is not None and s_strategy != strategy:
            continue
        snap_dir = d / "snapshots"
        snap_count = len(list(snap_dir.glob("*.md"))) if snap_dir.exists() else 0
        sessions.append(
            {
                "number": num,
                "kind": s_kind,
                "strategy": s_strategy,
                "status": meta.get("status", ""),
                "task": meta.get("task", ""),
                "snapshot_count": snap_count,
                "created_at": meta.get("started_at", ""),
                "ended_at": meta.get("ended_at", ""),
                "has_transcript": (d / "transcript.md").exists(),
                "has_journal": (d / "journal.md").exists(),
            }
        )
    return sessions


# Experiment snapshots are write-once (save_experiment_snapshot allocates a new
# number and writes each file exactly once), so an mtime-keyed cache avoids
# re-reading potentially hundreds of KB of .md files on every poll of the
# agent detail endpoint.
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


def list_experiments(agent_dir: Path) -> list[dict[str, Any]]:
    """List experiments as dicts (number, execution_mode, ...), newest first."""
    experiments: list[dict[str, Any]] = []
    d = agent_dir / "dry_runs"
    if not d.exists():
        return experiments
    stated = [(f, f.stat().st_mtime) for f in d.glob("experiment_*.md")]
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


def enumerate_run_ids(slug: str, agent_dir: Path) -> list[dict[str, Any]]:
    """Enumerate attributed runs (tick sessions + experiments) for perf rollups.

    Returns dicts with ``agent_id``, ``controller_id`` (the executor tag —
    normally == agent_id, but migrated sessions keep their legacy composite tag
    in meta.yml so historical executors still attribute), ``num``, ``kind``
    ("session" | "experiment") and ``strategy``. Delegations/consults are
    excluded: they never tag ``controller_id``.
    """
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for num, d in _iter_session_dirs(agent_dir):
        meta = read_session_meta(d)
        if _session_kind(meta) != TICK_KIND:
            continue
        agent_id = f"{slug}_{num}"
        if agent_id in seen:
            continue
        seen.add(agent_id)
        runs.append(
            {
                "agent_id": agent_id,
                "controller_id": meta.get("controller_id") or agent_id,
                "num": num,
                "kind": "session",
                "strategy": meta.get("strategy", ""),
            }
        )
    d = agent_dir / "dry_runs"
    if d.exists():
        for f in d.glob("experiment_*.md"):
            m = _EXPERIMENT_FILE_RE.match(f.name)
            if not m:
                continue
            n = int(m.group(1))
            agent_id = f"{slug}_e{n}"
            if agent_id in seen:
                continue
            seen.add(agent_id)
            runs.append(
                {
                    "agent_id": agent_id,
                    "controller_id": agent_id,
                    "num": n,
                    "kind": "experiment",
                    "strategy": "",
                }
            )
    return runs


def find_session_dir(agent_dir: Path, session_num: int) -> Path | None:
    path = agent_dir / "sessions" / f"session_{session_num}"
    return path if path.exists() else None


def find_experiment_file(agent_dir: Path, experiment_num: int) -> Path | None:
    path = agent_dir / "dry_runs" / f"experiment_{experiment_num}.md"
    return path if path.exists() else None

"""Read-only index over an agent's on-disk history layout.

The layout itself (``sessions/session_N``, ``experiments/{date}-eN.md``,
``delegations/{date}-dN.md``, ``meta.yml``, journal and snapshot formats) is
owned by :mod:`condor.agents.journal`; this module provides the enumeration
and lookup helpers that consumers (web routes, MCP tools) use to browse that
layout without re-implementing it.

All helpers take the agent dir (``agents/{agent_slug}``) and return plain data
— no FastAPI/Pydantic dependencies. Every ``sessions/session_N`` dir IS a real
session (refactor-07): delegations and experiments live in their own flat
dirs, so there is no ``kind`` field and no kind filtering.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from condor.agents.journal import (
    _DELEGATION_FILE_RE,
    _EXPERIMENT_FILE_RE,
    count_journal_ticks,
    read_session_meta,
)


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


def infer_latest_session_status(agent_dir: Path, slug: str) -> dict[str, Any] | None:
    """Infer status from the latest session on disk when no engine is in memory."""
    sessions = list(_iter_session_dirs(agent_dir))
    if not sessions:
        return None

    num, latest = max(sessions, key=lambda t: t[1].stat().st_mtime)
    # If no engine is in memory, the agent is not running — idle metadata only.
    return {
        "agent_id": f"{slug}_{num}",
        "session_num": num,
        "status": "idle",
        "strategy": read_session_meta(latest).get("strategy", ""),
        "tick_count": count_journal_ticks(latest / "journal.md"),
    }


def count_sessions(agent_dir: Path) -> int:
    return sum(1 for _ in _iter_session_dirs(agent_dir))


def count_experiments(agent_dir: Path) -> int:
    d = agent_dir / "experiments"
    if not d.exists():
        return 0
    return sum(1 for f in d.iterdir() if _EXPERIMENT_FILE_RE.match(f.name))


def list_sessions(
    agent_dir: Path, strategy: str | None = None
) -> list[dict[str, Any]]:
    """List sessions as dicts (number, strategy, status, …), newest first.

    ``strategy`` filters on the meta.yml field — that filter is what gives
    per-playbook track records under the agent-level pool.
    """
    sessions: list[dict[str, Any]] = []
    for num, d in sorted(_iter_session_dirs(agent_dir), reverse=True):
        meta = read_session_meta(d)
        s_strategy = meta.get("strategy", "")
        if strategy is not None and s_strategy != strategy:
            continue
        snap_dir = d / "snapshots"
        snap_count = len(list(snap_dir.glob("*.md"))) if snap_dir.exists() else 0
        sessions.append(
            {
                "number": num,
                "strategy": s_strategy,
                "status": meta.get("status", ""),
                "snapshot_count": snap_count,
                "created_at": meta.get("started_at", ""),
                "ended_at": meta.get("ended_at", ""),
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
    d = agent_dir / "experiments"
    if not d.exists():
        return experiments
    stated = [(f, f.stat().st_mtime) for f in d.iterdir() if f.suffix == ".md"]
    for f, mtime in sorted(stated, key=lambda x: x[1], reverse=True):
        m = _EXPERIMENT_FILE_RE.match(f.name)
        if not m:
            continue
        num = int(m.group(2))
        cached = _experiment_info_cache.get(f)
        if cached is not None and cached[0] == mtime:
            info = cached[1]
        else:
            info = _parse_experiment_file(f, num)
            _experiment_info_cache[f] = (mtime, info)
        experiments.append(info)
    return experiments


# ── Delegations (flat transcripts — refactor-07) ──

# Header lines written by delegate._write_transcript, e.g. "- **Status:** done"
_DELEGATION_FIELD_RE = re.compile(r"^- \*\*(\w[\w ]*):\*\* (.*)$", re.MULTILINE)


def _parse_delegation_file(f: Path, num: int) -> dict[str, Any]:
    content = f.read_text(errors="replace")
    fields = {
        m.group(1): m.group(2).strip()
        for m in _DELEGATION_FIELD_RE.finditer(content)
    }
    task = ""
    task_match = re.search(
        r"^## Task\s*\n+(.+?)(?=\n## |\Z)", content, re.MULTILINE | re.DOTALL
    )
    if task_match:
        task = " ".join(task_match.group(1).strip().split())[:500]
    agent_slug = fields.get("Agent", "")

    def _field(name: str) -> str:
        v = fields.get(name, "")
        return "" if v == "-" else v

    return {
        "number": num,
        "task_id": f"{agent_slug}-d{num}" if agent_slug else f"d{num}",
        "status": fields.get("Status", ""),
        "task": task,
        "created_at": _field("Started"),
        "ended_at": _field("Ended"),
        "file": f.name,
    }


def list_delegations_on_disk(agent_dir: Path) -> list[dict[str, Any]]:
    """List delegation transcripts (number, status, task, …), newest first.

    A file whose Status is still ``running`` with no live registry entry is a
    crash husk — surfaced as-is; the caller decides how to present it.
    """
    out: list[dict[str, Any]] = []
    d = agent_dir / "delegations"
    if not d.exists():
        return out
    entries = []
    for f in d.iterdir():
        m = _DELEGATION_FILE_RE.match(f.name)
        if m:
            entries.append((int(m.group(2)), f))
    for num, f in sorted(entries, reverse=True):
        out.append(_parse_delegation_file(f, num))
    return out


def enumerate_run_ids(slug: str, agent_dir: Path) -> list[dict[str, Any]]:
    """Enumerate attributed runs (sessions + experiments) for perf rollups.

    Returns dicts with ``agent_id``, ``controller_id`` (the executor tag —
    normally == agent_id, but migrated sessions keep their legacy composite tag
    in meta.yml so historical executors still attribute), ``num``, ``kind``
    ("session" | "experiment") and ``strategy``. Delegations are excluded:
    they never tag ``controller_id``.
    """
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for num, d in _iter_session_dirs(agent_dir):
        meta = read_session_meta(d)
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
    d = agent_dir / "experiments"
    if d.exists():
        for f in d.iterdir():
            m = _EXPERIMENT_FILE_RE.match(f.name)
            if not m:
                continue
            n = int(m.group(2))
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
    d = agent_dir / "experiments"
    if not d.exists():
        return None
    for f in d.iterdir():
        m = _EXPERIMENT_FILE_RE.match(f.name)
        if m and int(m.group(2)) == experiment_num:
            return f
    return None

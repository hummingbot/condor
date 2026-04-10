"""Trading Agents API routes."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.web.auth import get_current_user
from condor.web.models import WebUser

# ── Simple in-memory TTL cache for performance data ──
_PERF_CACHE: dict[str, tuple[float, Any]] = {}
_PERF_TTL = 30.0  # seconds


def _cache_get(key: str) -> Any | None:
    entry = _PERF_CACHE.get(key)
    if not entry:
        return None
    ts, val = entry
    if time.time() - ts > _PERF_TTL:
        _PERF_CACHE.pop(key, None)
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    _PERF_CACHE[key] = (time.time(), val)


log = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["agents"])

_DATA_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "trading_agents"


# ── Request/Response Models ──


class RunningInstance(BaseModel):
    agent_id: str
    session_num: int
    status: str
    agent_key: str = ""
    tick_count: int = 0
    daily_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    volume: float = 0.0
    fees: float = 0.0
    open_count: int = 0
    closed_count: int = 0
    win_rate: float = 0.0
    server_name: str = ""
    total_amount_quote: float = 100.0
    trading_context: str = ""
    frequency_sec: int = 60
    execution_mode: str = "loop"
    risk_limits: dict[str, Any] = {}


class AgentSummary(BaseModel):
    slug: str
    name: str
    description: str
    status: str  # running, paused, stopped, idle
    agent_id: str = ""
    session_count: int = 0
    experiment_count: int = 0
    tick_count: int = 0
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    total_volume: float = 0.0
    open_positions: int = 0
    instances: list[RunningInstance] = []


class AgentPerformanceModel(BaseModel):
    agent_id: str
    session_num: int = 0
    kind: str = "session"  # session | experiment
    status: str = ""
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    volume: float = 0.0
    fees: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    open_count: int = 0
    closed_count: int = 0
    executors: list[dict[str, Any]] = []


class AgentPerformanceResponse(BaseModel):
    slug: str
    sessions: list[AgentPerformanceModel] = []
    totals: dict[str, float] = {}


class SessionInfo(BaseModel):
    number: int
    snapshot_count: int = 0
    created_at: str = ""


class ExperimentInfo(BaseModel):
    number: int
    execution_mode: str = ""  # dry_run or run_once
    agent_key: str = ""
    snapshot_count: int = 0
    created_at: str = ""


class AgentDetail(BaseModel):
    slug: str
    name: str
    description: str
    agent_md: str
    config: dict[str, Any] = {}
    default_trading_context: str = ""
    learnings: str = ""
    status: str = "idle"
    agent_id: str = ""
    sessions: list[SessionInfo] = []
    experiments: list[ExperimentInfo] = []
    instances: list[RunningInstance] = []


class SnapshotSummary(BaseModel):
    tick: int
    timestamp: str = ""
    file: str = ""


class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""
    agent_key: str = "claude-code"
    default_trading_context: str = ""
    config: dict[str, Any] = {}


class UpdateAgentMdRequest(BaseModel):
    content: str


class UpdateConfigRequest(BaseModel):
    config: dict[str, Any]


class UpdateLearningsRequest(BaseModel):
    content: str


class StartAgentRequest(BaseModel):
    config: dict[str, Any] = {}
    trading_context: str = ""


# ── Helpers ──


def _get_store():
    from condor.trading_agent.strategy import StrategyStore

    return StrategyStore()


def _get_strategy_by_slug(slug: str):
    store = _get_store()
    strategy = store.get_by_slug(slug)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return strategy


def _get_running_engine(slug: str):
    from condor.trading_agent.engine import get_all_engines

    for engine in get_all_engines().values():
        if engine.strategy.slug == slug and engine.is_running:
            return engine
    return None


def _get_engine_for_slug(slug: str):
    """Get any engine (running or paused) for a slug."""
    from condor.trading_agent.engine import get_all_engines

    for engine in get_all_engines().values():
        if engine.strategy.slug == slug:
            return engine
    return None


def _get_engines_for_slug(slug: str) -> list:
    """Get all engines for a slug (multiple instances possible)."""
    from condor.trading_agent.engine import get_all_engines

    return [e for e in get_all_engines().values() if e.strategy.slug == slug]


def _infer_latest_session_status(agent_dir: Path, slug: str) -> dict[str, Any] | None:
    """When no engine is in memory, infer status from the latest session on disk.

    Returns a dict with agent_id, status, tick_count, daily_pnl if a recent
    session exists, or None.
    """
    import time

    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.exists():
        return None

    # Find the latest session directory
    session_dirs = sorted(
        [
            d
            for d in sessions_dir.iterdir()
            if d.is_dir() and d.name.startswith("session_")
        ],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if not session_dirs:
        return None

    latest = session_dirs[0]
    try:
        num = int(latest.name.split("_", 1)[1])
    except (ValueError, IndexError):
        return None

    # Check if the session has recent activity (snapshot written in last 5 min)
    snap_dir = latest / "snapshots"
    last_activity = latest.stat().st_mtime
    if snap_dir.exists():
        snaps = list(snap_dir.glob("snapshot_*.md"))
        if snaps:
            last_activity = max(f.stat().st_mtime for f in snaps)

    # If no engine is in memory, the agent is not running.
    # Only mark as "running" if a TickEngine is actively processing
    # (handled by the caller via get_engines_for_slug).
    # This function just provides fallback metadata for idle agents.
    status = "idle"

    # Tick count from journal (PnL now comes from backend performance data)
    tick_count = 0
    journal_path = latest / "journal.md"
    if journal_path.exists():
        import re as _re

        text = journal_path.read_text(errors="replace")
        tick_count = len(_re.findall(r"^- tick#", text, _re.MULTILINE))

    return {
        "agent_id": f"{slug}_{num}",
        "session_num": num,
        "status": status,
        "tick_count": tick_count,
    }


def _count_sessions(agent_dir: Path) -> int:
    for dirname in ("sessions", "trading_sessions"):
        sessions_dir = agent_dir / dirname
        if sessions_dir.exists():
            return len(
                [
                    d
                    for d in sessions_dir.iterdir()
                    if d.is_dir() and d.name.startswith("session_")
                ]
            )
    return 0


def _count_experiments(agent_dir: Path) -> int:
    count = 0
    for dirname in ("dry_runs", "experiments"):
        d = agent_dir / dirname
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


def _list_sessions(agent_dir: Path) -> list[SessionInfo]:
    sessions = []
    for dirname in ("sessions", "trading_sessions"):
        sessions_dir = agent_dir / dirname
        if not sessions_dir.exists():
            continue
        for d in sorted(sessions_dir.iterdir(), reverse=True):
            if not d.is_dir() or not d.name.startswith("session_"):
                continue
            try:
                num = int(d.name.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            # Count snapshots
            snap_count = 0
            for snap_dir_name in ("snapshots", "runs"):
                snap_dir = d / snap_dir_name
                if snap_dir.exists():
                    snap_count = len(list(snap_dir.glob("*.md")))
                    break
            created = ""
            if (d / "journal.md").exists():
                import os

                created = str(os.path.getctime(d / "journal.md"))
            sessions.append(
                SessionInfo(number=num, snapshot_count=snap_count, created_at=created)
            )
    return sessions


def _list_experiments(agent_dir: Path) -> list[ExperimentInfo]:
    experiments = []
    all_files = []
    for dirname in ("dry_runs", "experiments"):
        d = agent_dir / dirname
        if d.exists():
            all_files.extend(d.glob("experiment_*.md"))
    for f in sorted(all_files, key=lambda x: x.stat().st_mtime, reverse=True):
        m = re.match(r"experiment_(\d+)\.md", f.name)
        if not m:
            continue
        num = int(m.group(1))
        # Extract mode, model, and timestamp from file header
        execution_mode = ""
        agent_key = ""
        content = f.read_text(errors="replace")
        mode_match = re.search(r"^Mode:\s*(\S+)", content, re.MULTILINE)
        if mode_match:
            execution_mode = mode_match.group(1)
        model_match = re.search(r"^Model:\s*(\S+)", content, re.MULTILINE)
        if model_match:
            agent_key = model_match.group(1)
        # Extract timestamp
        created = ""
        ts_match = re.search(r"^# Experiment #\d+ — (.+)$", content, re.MULTILINE)
        if ts_match:
            created = ts_match.group(1)
        experiments.append(
            ExperimentInfo(
                number=num,
                execution_mode=execution_mode,
                agent_key=agent_key,
                snapshot_count=1,
                created_at=created,
            )
        )
    return experiments


async def _get_client_for_agent(agent_dir: Path, default_config: dict | None = None):
    """Resolve a Hummingbot API client for an agent, based on its config.yml."""
    from condor.trading_agent.config import load_agent_config
    from config_manager import get_config_manager

    try:
        cfg = load_agent_config(agent_dir, default_config)
    except Exception:
        return None, ""
    server_name = cfg.server_name or ""
    if not server_name:
        return None, ""
    cm = get_config_manager()
    try:
        client = await cm.get_client(server_name)
    except Exception as e:
        log.warning("get_client(%s) failed: %s", server_name, e)
        return None, server_name
    return client, server_name


def _enumerate_agent_ids(slug: str, agent_dir: Path) -> list[tuple[str, int, str]]:
    """Return (agent_id, session_num, kind) for every session and experiment on disk."""
    ids: list[tuple[str, int, str]] = []
    for dirname in ("sessions", "trading_sessions"):
        d = agent_dir / dirname
        if not d.exists():
            continue
        for sd in d.iterdir():
            if not sd.is_dir() or not sd.name.startswith("session_"):
                continue
            try:
                n = int(sd.name.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            ids.append((f"{slug}_{n}", n, "session"))
    for dirname in ("dry_runs", "experiments"):
        d = agent_dir / dirname
        if not d.exists():
            continue
        for f in d.glob("experiment_*.md"):
            m = re.match(r"experiment_(\d+)\.md", f.name)
            if not m:
                continue
            n = int(m.group(1))
            ids.append((f"{slug}_e{n}", n, "experiment"))
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[tuple[str, int, str]] = []
    for tup in ids:
        if tup[0] in seen:
            continue
        seen.add(tup[0])
        unique.append(tup)
    return unique


async def _compute_agent_performance(
    slug: str, agent_dir: Path, default_config: dict | None
):
    """Return list of AgentPerformanceModel plus rolled-up totals. Cached for ~10s."""
    from condor.trading_agent.performance import fetch_agent_performance_batch

    cached = _cache_get(f"perf:{slug}")
    if cached is not None:
        return cached

    ids = _enumerate_agent_ids(slug, agent_dir)
    client, _server = await _get_client_for_agent(agent_dir, default_config)

    sessions: list[AgentPerformanceModel] = []
    if client and ids:
        agent_ids = [aid for aid, _, _ in ids]
        try:
            perf_map = await fetch_agent_performance_batch(client, agent_ids)
        except Exception as e:
            log.warning("fetch_agent_performance_batch(%s) failed: %s", slug, e)
            perf_map = {}
        for agent_id, num, kind in ids:
            perf = perf_map.get(agent_id)
            if perf is None:
                continue
            # Skip experiments with no activity to reduce payload
            if kind == "experiment" and perf.trade_count == 0:
                continue
            sessions.append(
                AgentPerformanceModel(
                    agent_id=agent_id,
                    session_num=num,
                    kind=kind,
                    realized_pnl=perf.realized_pnl,
                    unrealized_pnl=perf.unrealized_pnl,
                    total_pnl=perf.total_pnl,
                    volume=perf.volume,
                    fees=perf.fees,
                    trade_count=perf.trade_count,
                    win_rate=perf.win_rate,
                    open_count=perf.open_count,
                    closed_count=perf.closed_count,
                    executors=perf.executors,
                )
            )

    # Roll-up totals exclude experiments (dry runs / one-shots).
    real_sessions = [s for s in sessions if s.kind == "session"]
    totals = {
        "total_pnl": sum(s.total_pnl for s in real_sessions),
        "realized_pnl": sum(s.realized_pnl for s in real_sessions),
        "unrealized_pnl": sum(s.unrealized_pnl for s in real_sessions),
        "volume": sum(s.volume for s in real_sessions),
        "fees": sum(s.fees for s in real_sessions),
        "open_positions": sum(s.open_count for s in real_sessions),
        "trade_count": float(sum(s.trade_count for s in real_sessions)),
    }
    result = (sessions, totals)
    _cache_set(f"perf:{slug}", result)
    return result


def _get_session_dir(agent_dir: Path, session_num: int) -> Path | None:
    for dirname in ("sessions", "trading_sessions"):
        path = agent_dir / dirname / f"session_{session_num}"
        if path.exists():
            return path
    return None


def _get_experiment_file(agent_dir: Path, experiment_num: int) -> Path | None:
    for dirname in ("dry_runs", "experiments"):
        path = agent_dir / dirname / f"experiment_{experiment_num}.md"
        if path.exists():
            return path
    return None


# ── Routes ──


@router.get("", response_model=list[AgentSummary])
async def list_agents(user: WebUser = Depends(get_current_user)):
    """List all trading agents with status."""
    import asyncio as _asyncio

    store = _get_store()
    strategies = store.list_all()
    results = []

    # Parallelize per-agent performance compute
    perf_coros = [
        _compute_agent_performance(s.slug, s.agent_dir, s.default_config)
        for s in strategies
    ]
    perf_results = await _asyncio.gather(*perf_coros, return_exceptions=True)

    for s, pres in zip(strategies, perf_results):
        engines = _get_engines_for_slug(s.slug)
        status = "idle"
        agent_id = ""
        tick_count = 0
        instances: list[RunningInstance] = []

        # Parallel-computed performance above
        if isinstance(pres, Exception):
            log.warning("compute_agent_performance(%s) failed: %s", s.slug, pres)
            sessions_perf, totals = [], {}
        else:
            sessions_perf, totals = pres

        perf_by_id = {p.agent_id: p for p in sessions_perf}

        for engine in engines:
            info = engine.get_info()
            p = perf_by_id.get(info["agent_id"])
            instances.append(
                RunningInstance(
                    agent_id=info["agent_id"],
                    session_num=info["session_num"],
                    status=info["status"],
                    tick_count=info["tick_count"],
                    daily_pnl=(p.total_pnl if p else info["daily_pnl"]),
                    realized_pnl=p.realized_pnl if p else 0.0,
                    unrealized_pnl=p.unrealized_pnl if p else 0.0,
                    total_pnl=p.total_pnl if p else 0.0,
                    volume=p.volume if p else 0.0,
                    fees=p.fees if p else 0.0,
                    open_count=p.open_count if p else 0,
                    closed_count=p.closed_count if p else 0,
                    win_rate=p.win_rate if p else 0.0,
                    server_name=info.get("server_name", ""),
                    total_amount_quote=info.get("total_amount_quote", 100),
                    trading_context=info.get("trading_context", ""),
                    frequency_sec=info.get("frequency_sec", 60),
                    agent_key=info.get("agent_key", ""),
                    execution_mode=info.get("execution_mode", "loop"),
                    risk_limits=info.get("risk_limits", {}),
                )
            )
            if not agent_id:
                status = info["status"]
                agent_id = info["agent_id"]
                tick_count = info["tick_count"]

        if not engines:
            disk_info = _infer_latest_session_status(s.agent_dir, s.slug)
            if disk_info:
                status = disk_info["status"]
                agent_id = disk_info["agent_id"]
                tick_count = disk_info["tick_count"]

        # Find "today's" pnl: prefer the most recent session perf
        latest_session_pnl = 0.0
        if sessions_perf:
            latest = max(
                (p for p in sessions_perf if p.kind == "session"),
                key=lambda p: p.session_num,
                default=None,
            )
            if latest:
                latest_session_pnl = latest.total_pnl

        results.append(
            AgentSummary(
                slug=s.slug,
                name=s.name,
                description=s.description,
                status=status,
                agent_id=agent_id,
                session_count=_count_sessions(s.agent_dir),
                experiment_count=_count_experiments(s.agent_dir),
                tick_count=tick_count,
                daily_pnl=latest_session_pnl,
                total_pnl=float(totals.get("total_pnl", 0.0)),
                total_volume=float(totals.get("volume", 0.0)),
                open_positions=int(totals.get("open_positions", 0)),
                instances=instances,
            )
        )

    return results


@router.get("/{slug}", response_model=AgentDetail)
async def get_agent(slug: str, user: WebUser = Depends(get_current_user)):
    """Get agent detail."""
    strategy = _get_strategy_by_slug(slug)
    agent_dir = strategy.agent_dir

    # Read agent.md raw content
    agent_md_path = agent_dir / "agent.md"
    agent_md = agent_md_path.read_text() if agent_md_path.exists() else ""

    # Read config
    from condor.trading_agent.config import load_agent_config

    config = load_agent_config(agent_dir, strategy.default_config)

    # Read learnings
    learnings_path = agent_dir / "learnings.md"
    learnings = learnings_path.read_text() if learnings_path.exists() else ""

    # Compute performance for all sessions (cached)
    try:
        sessions_perf, _totals = await _compute_agent_performance(
            slug,
            agent_dir,
            strategy.default_config,
        )
    except Exception as e:
        log.warning("compute_agent_performance(%s) failed: %s", slug, e)
        sessions_perf = []
    perf_by_id = {p.agent_id: p for p in sessions_perf}

    # Get engine status
    engines = _get_engines_for_slug(slug)
    status = "idle"
    agent_id = ""
    instances = []
    for engine in engines:
        info = engine.get_info()
        p = perf_by_id.get(info["agent_id"])
        instances.append(
            RunningInstance(
                agent_id=info["agent_id"],
                session_num=info["session_num"],
                status=info["status"],
                tick_count=info["tick_count"],
                daily_pnl=(p.total_pnl if p else info["daily_pnl"]),
                realized_pnl=p.realized_pnl if p else 0.0,
                unrealized_pnl=p.unrealized_pnl if p else 0.0,
                total_pnl=p.total_pnl if p else 0.0,
                volume=p.volume if p else 0.0,
                fees=p.fees if p else 0.0,
                open_count=p.open_count if p else 0,
                closed_count=p.closed_count if p else 0,
                win_rate=p.win_rate if p else 0.0,
                server_name=info.get("server_name", ""),
                total_amount_quote=info.get("total_amount_quote", 100),
                trading_context=info.get("trading_context", ""),
                frequency_sec=info.get("frequency_sec", 60),
                execution_mode=info.get("execution_mode", "loop"),
                risk_limits=info.get("risk_limits", {}),
            )
        )
        if not agent_id:
            status = info["status"]
            agent_id = info["agent_id"]

    # Fallback: infer from latest session on disk when no engine in memory
    if not engines:
        disk_info = _infer_latest_session_status(agent_dir, slug)
        if disk_info:
            status = disk_info["status"]
            agent_id = disk_info["agent_id"]

    # List sessions and experiments
    sessions = _list_sessions(agent_dir)
    experiments = _list_experiments(agent_dir)

    return AgentDetail(
        slug=slug,
        name=strategy.name,
        description=strategy.description,
        agent_md=agent_md,
        config=config.model_dump(),
        default_trading_context=strategy.default_trading_context,
        learnings=learnings,
        status=status,
        agent_id=agent_id,
        sessions=sessions,
        experiments=experiments,
        instances=instances,
    )


@router.post("", response_model=AgentSummary)
async def create_agent(
    req: CreateAgentRequest, user: WebUser = Depends(get_current_user)
):
    """Create a new trading agent."""
    store = _get_store()
    strategy = store.create(
        name=req.name,
        description=req.description,
        agent_key=req.agent_key,
        instructions=req.instructions,
        default_config=req.config,
        default_trading_context=req.default_trading_context,
        created_by=user.id,
    )

    # Save config.yml
    if req.config:
        from condor.trading_agent.config import AgentConfig, save_agent_config

        config = AgentConfig.from_dict(req.config)
        save_agent_config(strategy.agent_dir, config)

    # Create empty learnings.md
    learnings_path = strategy.agent_dir / "learnings.md"
    if not learnings_path.exists():
        learnings_path.write_text(
            "# Learnings\n\n## Active Insights\n\n## Retired Insights\n"
        )

    return AgentSummary(
        slug=strategy.slug,
        name=strategy.name,
        description=strategy.description,
        status="idle",
    )


@router.put("/{slug}")
async def update_agent_md(
    slug: str, req: UpdateAgentMdRequest, user: WebUser = Depends(get_current_user)
):
    """Update agent.md content."""
    strategy = _get_strategy_by_slug(slug)
    agent_md_path = strategy.agent_dir / "agent.md"
    agent_md_path.write_text(req.content)
    return {"updated": True}


@router.put("/{slug}/config")
async def update_agent_config(
    slug: str, req: UpdateConfigRequest, user: WebUser = Depends(get_current_user)
):
    """Update agent config."""
    strategy = _get_strategy_by_slug(slug)
    from condor.trading_agent.config import AgentConfig, save_agent_config

    config = AgentConfig.from_dict(req.config)
    save_agent_config(strategy.agent_dir, config)
    return {"updated": True, "config": config.model_dump()}


@router.delete("/{slug}")
async def delete_agent(slug: str, user: WebUser = Depends(get_current_user)):
    """Delete an agent."""
    strategy = _get_strategy_by_slug(slug)
    # Don't delete if any instance is running
    running = [e for e in _get_engines_for_slug(slug) if e.is_running]
    if running:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a running agent. Stop all instances first.",
        )

    store = _get_store()
    store.delete(strategy.id)
    return {"deleted": True}


# ── Performance ──


@router.get("/{slug}/performance", response_model=AgentPerformanceResponse)
async def get_agent_performance(slug: str, user: WebUser = Depends(get_current_user)):
    """Return per-session performance and roll-up totals for an agent."""
    strategy = _get_strategy_by_slug(slug)
    sessions, totals = await _compute_agent_performance(
        slug,
        strategy.agent_dir,
        strategy.default_config,
    )
    # Annotate status using running engines
    running_ids = {e.agent_id for e in _get_engines_for_slug(slug) if e.is_running}
    for s in sessions:
        s.status = "running" if s.agent_id in running_ids else "closed"
    return AgentPerformanceResponse(slug=slug, sessions=sessions, totals=totals)


@router.get("/{slug}/sessions/{session_num}/executors")
async def get_session_executors(
    slug: str, session_num: int, user: WebUser = Depends(get_current_user)
):
    """Return executors + performance for a single session."""
    from condor.trading_agent.performance import fetch_agent_performance

    strategy = _get_strategy_by_slug(slug)
    agent_id = f"{slug}_{session_num}"
    client, _server = await _get_client_for_agent(
        strategy.agent_dir,
        strategy.default_config,
    )
    if client is None:
        return {
            "executors": [],
            "performance": AgentPerformanceModel(
                agent_id=agent_id, session_num=session_num
            ).model_dump(),
        }
    perf = await fetch_agent_performance(client, agent_id)
    model = AgentPerformanceModel(
        agent_id=agent_id,
        session_num=session_num,
        realized_pnl=perf.realized_pnl,
        unrealized_pnl=perf.unrealized_pnl,
        total_pnl=perf.total_pnl,
        volume=perf.volume,
        fees=perf.fees,
        trade_count=perf.trade_count,
        win_rate=perf.win_rate,
        open_count=perf.open_count,
        closed_count=perf.closed_count,
        executors=perf.executors,
    )
    return {"executors": perf.executors, "performance": model.model_dump()}


# ── Lifecycle ──


@router.post("/{slug}/start")
async def start_agent(
    slug: str, req: StartAgentRequest, user: WebUser = Depends(get_current_user)
):
    """Start an agent (creates new session)."""
    from condor.trading_agent.config import load_agent_config
    from condor.trading_agent.engine import TickEngine

    strategy = _get_strategy_by_slug(slug)

    # Load config (merge request overrides)
    config = load_agent_config(strategy.agent_dir, strategy.default_config)
    config_dict = config.model_dump()
    if req.config:
        config_dict.update(req.config)

    # Apply trading context: explicit request > strategy default
    if req.trading_context:
        config_dict["trading_context"] = req.trading_context
    elif not config_dict.get("trading_context") and strategy.default_trading_context:
        config_dict["trading_context"] = strategy.default_trading_context

    new_engine = TickEngine(
        strategy=strategy,
        config=config_dict,
        chat_id=0,  # Web-launched, no chat
        user_id=user.id,
    )
    await new_engine.start()

    return {
        "started": True,
        "agent_id": new_engine.agent_id,
        "session_num": new_engine.session_num,
    }


@router.post("/{slug}/stop")
async def stop_agent(
    slug: str, agent_id: str | None = None, user: WebUser = Depends(get_current_user)
):
    """Stop a running agent. If agent_id given, stop that specific instance; otherwise stop all."""
    if agent_id:
        from condor.trading_agent.engine import get_engine

        engine = get_engine(agent_id)
        if not engine:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        await engine.stop()
    else:
        engines = _get_engines_for_slug(slug)
        if not engines:
            raise HTTPException(status_code=404, detail="No running agent found")
        for engine in engines:
            await engine.stop()
    return {"stopped": True}


@router.post("/{slug}/pause")
async def pause_agent(
    slug: str, agent_id: str | None = None, user: WebUser = Depends(get_current_user)
):
    """Pause a running agent."""
    if agent_id:
        from condor.trading_agent.engine import get_engine

        engine = get_engine(agent_id)
        if not engine or not engine.is_running:
            raise HTTPException(
                status_code=404, detail=f"Agent '{agent_id}' not found or not running"
            )
        engine.pause()
    else:
        engine = _get_running_engine(slug)
        if not engine:
            raise HTTPException(status_code=404, detail="No running agent found")
        engine.pause()
    return {"paused": True}


@router.post("/{slug}/resume")
async def resume_agent(
    slug: str, agent_id: str | None = None, user: WebUser = Depends(get_current_user)
):
    """Resume a paused agent."""
    if agent_id:
        from condor.trading_agent.engine import get_engine

        engine = get_engine(agent_id)
        if not engine:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        engine.resume()
    else:
        engine = _get_engine_for_slug(slug)
        if not engine:
            raise HTTPException(status_code=404, detail="No agent found")
        engine.resume()
    return {"resumed": True}


# ── Learnings ──


@router.get("/{slug}/learnings")
async def get_learnings(slug: str, user: WebUser = Depends(get_current_user)):
    """Read learnings.md."""
    strategy = _get_strategy_by_slug(slug)
    learnings_path = strategy.agent_dir / "learnings.md"
    content = learnings_path.read_text() if learnings_path.exists() else ""
    return {"content": content}


@router.put("/{slug}/learnings")
async def update_learnings(
    slug: str, req: UpdateLearningsRequest, user: WebUser = Depends(get_current_user)
):
    """Update learnings.md."""
    strategy = _get_strategy_by_slug(slug)
    learnings_path = strategy.agent_dir / "learnings.md"
    learnings_path.write_text(req.content)
    return {"updated": True}


# ── Sessions ──


@router.get("/{slug}/sessions")
async def list_sessions(slug: str, user: WebUser = Depends(get_current_user)):
    """List sessions for an agent."""
    strategy = _get_strategy_by_slug(slug)
    sessions = _list_sessions(strategy.agent_dir)
    return {"sessions": [s.model_dump() for s in sessions]}


@router.get("/{slug}/sessions/{session_num}/journal")
async def get_journal(
    slug: str, session_num: int, user: WebUser = Depends(get_current_user)
):
    """Read journal.md for a session."""
    strategy = _get_strategy_by_slug(slug)
    session_dir = _get_session_dir(strategy.agent_dir, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")

    journal_path = session_dir / "journal.md"
    content = journal_path.read_text() if journal_path.exists() else ""
    return {"content": content}


@router.get("/{slug}/sessions/{session_num}/snapshots")
async def list_snapshots(
    slug: str, session_num: int, user: WebUser = Depends(get_current_user)
):
    """List snapshots for a session."""
    strategy = _get_strategy_by_slug(slug)
    session_dir = _get_session_dir(strategy.agent_dir, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")

    snapshots = []
    for snap_dir_name in ("snapshots", "runs"):
        snap_dir = session_dir / snap_dir_name
        if not snap_dir.exists():
            continue
        for f in sorted(
            snap_dir.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True
        ):
            m = re.match(r"(?:snapshot|run)_(\d+)\.md", f.name)
            if m:
                tick = int(m.group(1))
                # Extract timestamp from file
                content = f.read_text()
                ts_match = re.search(
                    r"^# (?:Snapshot|Tick) #\d+ — (.+)$", content, re.MULTILINE
                )
                timestamp = ts_match.group(1) if ts_match else ""

                snapshots.append(
                    SnapshotSummary(
                        tick=tick,
                        timestamp=timestamp,
                        file=f.name,
                    )
                )
        break  # Use whichever dir exists first

    return {"snapshots": [s.model_dump() for s in snapshots]}


@router.get("/{slug}/sessions/{session_num}/snapshots/{tick}")
async def get_snapshot(
    slug: str, session_num: int, tick: int, user: WebUser = Depends(get_current_user)
):
    """Read a specific snapshot."""
    strategy = _get_strategy_by_slug(slug)
    session_dir = _get_session_dir(strategy.agent_dir, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")

    # Try new format first, then legacy
    for snap_dir_name, prefix in [("snapshots", "snapshot"), ("runs", "run")]:
        path = session_dir / snap_dir_name / f"{prefix}_{tick}.md"
        if path.exists():
            return {"content": path.read_text(), "tick": tick}

    raise HTTPException(status_code=404, detail=f"Snapshot {tick} not found")


# ── Experiments ──


@router.get("/{slug}/experiments")
async def list_experiments(slug: str, user: WebUser = Depends(get_current_user)):
    """List experiments for an agent."""
    strategy = _get_strategy_by_slug(slug)
    experiments = _list_experiments(strategy.agent_dir)
    return {"experiments": [e.model_dump() for e in experiments]}


@router.get("/{slug}/experiments/{exp_num}")
async def get_experiment(
    slug: str, exp_num: int, user: WebUser = Depends(get_current_user)
):
    """Read an experiment snapshot."""
    strategy = _get_strategy_by_slug(slug)
    path = _get_experiment_file(strategy.agent_dir, exp_num)
    if not path:
        raise HTTPException(status_code=404, detail=f"Experiment {exp_num} not found")
    return {"content": path.read_text(), "number": exp_num}

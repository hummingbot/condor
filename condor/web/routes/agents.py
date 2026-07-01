"""Trading Agents API routes.

An **Agent** is the top-level unit: identity + shared brain (memory/skills) that
``condor`` can *consult*. An Agent **owns strategies** — playbooks that loop via
``TickEngine``. So the route shape is::

    /agents                                  -> list Agents (+ their strategies)
    /agents/{slug}                           -> Agent detail
    /agents/{slug}/consult                   -> run the Agent's brain to completion
    /agents/{slug}/strategies                -> CRUD strategies under an Agent
    /agents/{slug}/strategies/{sslug}/...    -> per-strategy run/journal/perf

Per-strategy operational history (sessions, learnings, experiments, routines)
hangs off ``agents/{slug}/strategies/{sslug}/`` while the Agent's brain
stays shared at ``agents/{slug}/``.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.web.auth import get_current_user
from condor.web.models import ReportSummary, WebUser

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


def _runkey(agent_slug: str, sslug: str) -> str:
    """Composite run key embedded in agent_ids: ``"{agent_slug}.{strategy_slug}"``."""
    return f"{agent_slug}.{sslug}"


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


class StrategySummary(BaseModel):
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


class AgentSummary(BaseModel):
    slug: str
    name: str
    description: str
    consultable: bool = False
    when_to_consult: str = ""
    agent_key: str = ""
    strategy_count: int = 0
    strategies: list[StrategySummary] = []
    # Aggregated performance rolled up across the agent's strategies, used by
    # the dashboard summary cards (Portfolio strip + Agents page). FEAT-004 moved
    # perf data onto strategies; these aggregates keep the agent-level views working.
    status: str = "idle"  # "running" if any strategy is running
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


class StrategyPerformanceResponse(BaseModel):
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
    error: bool = False  # the tick's model call failed (Agent Response is an error)


class AgentDetail(BaseModel):
    slug: str
    name: str
    description: str
    agent_md: str
    agent_key: str = ""
    tools: list[str] = []
    when_to_consult: str = ""
    consultable: bool = False
    server_required: bool = True
    strategies: list[StrategySummary] = []


class StrategyDetail(BaseModel):
    slug: str
    agent_slug: str
    name: str
    description: str
    strategy_md: str
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
    agent_key: str = ""
    tools: list[str] = []
    when_to_consult: str = ""
    server_required: bool = True


class UpdateAgentMdRequest(BaseModel):
    content: str


class CreateStrategyRequest(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""
    agent_key: str | None = None
    default_trading_context: str = ""
    config: dict[str, Any] = {}


class UpdateStrategyMdRequest(BaseModel):
    content: str


class UpdateConfigRequest(BaseModel):
    config: dict[str, Any]


class UpdateLearningsRequest(BaseModel):
    content: str


class ConsultRequest(BaseModel):
    task: str
    context: str = ""
    chat_id: int = 0
    user_id: int | None = None
    server_name: str | None = None


class StartStrategyRequest(BaseModel):
    config: dict[str, Any] = {}
    trading_context: str = ""
    chat_id: int = 0  # Telegram chat for notifications (0 = web-launched, no chat)
    user_id: int | None = None  # Override user_id (for internal/MCP calls)


class DelegateRequest(BaseModel):
    task: str
    chat_id: int = 0  # Telegram chat for the completion notification
    user_id: int | None = None  # Override user_id (for internal/MCP calls)
    server_name: str | None = None
    timeout_s: int = 900


# ── Stores / lookups ──


def _agent_store():
    from condor.agents.agent import AgentStore

    return AgentStore()


def _strategy_store():
    from condor.agents.strategy import StrategyStore

    return StrategyStore()


def _get_agent(slug: str):
    agent = _agent_store().get(slug)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return agent


def _get_strategy(slug: str, sslug: str):
    strategy = _strategy_store().get(slug, sslug)
    if not strategy:
        raise HTTPException(
            status_code=404,
            detail=f"Strategy '{sslug}' not found under agent '{slug}'",
        )
    return strategy


def _get_engines_for(agent_slug: str, sslug: str) -> list:
    """All engines (running or paused) for a given (agent, strategy)."""
    from condor.agents.engine import get_all_engines

    return [
        e
        for e in get_all_engines().values()
        if e.agent.slug == agent_slug and e.strategy.slug == sslug
    ]


# ── Disk helpers (keyed by the strategy dir + run_key) ──


def _infer_latest_session_status(
    strategy_dir: Path, run_key: str
) -> dict[str, Any] | None:
    """Infer status from the latest session on disk when no engine is in memory."""
    sessions_dir = strategy_dir / "sessions"
    if not sessions_dir.exists():
        return None

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

    # If no engine is in memory, the agent is not running — idle metadata only.
    tick_count = 0
    journal_path = latest / "journal.md"
    if journal_path.exists():
        text = journal_path.read_text(errors="replace")
        tick_count = len(re.findall(r"^- tick#", text, re.MULTILINE))

    return {
        "agent_id": f"{run_key}_{num}",
        "session_num": num,
        "status": "idle",
        "tick_count": tick_count,
    }


def _count_sessions(strategy_dir: Path) -> int:
    for dirname in ("sessions", "trading_sessions"):
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


def _count_experiments(strategy_dir: Path) -> int:
    count = 0
    for dirname in ("dry_runs", "experiments"):
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


def _list_sessions(strategy_dir: Path) -> list[SessionInfo]:
    sessions = []
    for dirname in ("sessions", "trading_sessions"):
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


def _list_experiments(strategy_dir: Path) -> list[ExperimentInfo]:
    experiments = []
    all_files = []
    for dirname in ("dry_runs", "experiments"):
        d = strategy_dir / dirname
        if d.exists():
            all_files.extend(d.glob("experiment_*.md"))
    for f in sorted(all_files, key=lambda x: x.stat().st_mtime, reverse=True):
        m = re.match(r"experiment_(\d+)\.md", f.name)
        if not m:
            continue
        num = int(m.group(1))
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
        experiments.append(
            ExperimentInfo(
                number=num,
                execution_mode=execution_mode,
                agent_key=agent_key,
                snapshot_count=1,
                created_at=created,
                error=error,
            )
        )
    return experiments


async def _get_client_for_strategy(strategy_dir: Path, default_config: dict | None):
    """Resolve a Hummingbot API client for a strategy, based on its config.yml."""
    from condor.agents.config import load_agent_config
    from config_manager import get_config_manager

    try:
        cfg = load_agent_config(strategy_dir, default_config)
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


def _enumerate_agent_ids(
    run_key: str, strategy_dir: Path
) -> list[tuple[str, int, str]]:
    """Return (agent_id, session_num, kind) for every session and experiment on disk."""
    ids: list[tuple[str, int, str]] = []
    for dirname in ("sessions", "trading_sessions"):
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
    for dirname in ("dry_runs", "experiments"):
        d = strategy_dir / dirname
        if not d.exists():
            continue
        for f in d.glob("experiment_*.md"):
            m = re.match(r"experiment_(\d+)\.md", f.name)
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


async def _compute_strategy_performance(
    run_key: str, strategy_dir: Path, default_config: dict | None
):
    """Return list of AgentPerformanceModel plus rolled-up totals. Cached ~30s."""
    from condor.agents.config import load_full_config
    from condor.agents.performance import fetch_agent_performance_batch

    cached = _cache_get(f"perf:{run_key}")
    if cached is not None:
        return cached

    ids = _enumerate_agent_ids(run_key, strategy_dir)
    client, _server = await _get_client_for_strategy(strategy_dir, default_config)

    # Controller mode: a strategy with a configured bot_name attributes that bot's
    # PnL to every one of its agent sessions. The bot is persistent infrastructure
    # tied to the stable run_key, so the name is shared across sessions.
    bot_name = load_full_config(strategy_dir, default_config).get("bot_name", "")
    bot_names = {aid: bot_name for aid, _, _ in ids} if bot_name else None

    sessions: list[AgentPerformanceModel] = []
    if client and ids:
        agent_ids = [aid for aid, _, _ in ids]
        try:
            perf_map = await fetch_agent_performance_batch(client, agent_ids, bot_names)
        except Exception as e:
            log.warning("fetch_agent_performance_batch(%s) failed: %s", run_key, e)
            perf_map = {}
        for agent_id, num, kind in ids:
            perf = perf_map.get(agent_id)
            if perf is None:
                continue
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
    _cache_set(f"perf:{run_key}", result)
    return result


def _get_session_dir(strategy_dir: Path, session_num: int) -> Path | None:
    for dirname in ("sessions", "trading_sessions"):
        path = strategy_dir / dirname / f"session_{session_num}"
        if path.exists():
            return path
    return None


def _get_experiment_file(strategy_dir: Path, experiment_num: int) -> Path | None:
    for dirname in ("dry_runs", "experiments"):
        path = strategy_dir / dirname / f"experiment_{experiment_num}.md"
        if path.exists():
            return path
    return None


def _instance_from_engine(engine, perf_by_id: dict) -> RunningInstance:
    info = engine.get_info()
    p = perf_by_id.get(info["agent_id"])
    return RunningInstance(
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


async def _build_strategy_summary(strategy) -> StrategySummary:
    """Roll up disk + engine + performance state for one strategy."""
    run_key = _runkey(strategy.agent_slug, strategy.slug)
    strategy_dir = strategy.dir

    try:
        sessions_perf, totals = await _compute_strategy_performance(
            run_key, strategy_dir, strategy.default_config
        )
    except Exception as e:
        log.warning("compute_strategy_performance(%s) failed: %s", run_key, e)
        sessions_perf, totals = [], {}
    perf_by_id = {p.agent_id: p for p in sessions_perf}

    engines = _get_engines_for(strategy.agent_slug, strategy.slug)
    status = "idle"
    agent_id = ""
    tick_count = 0
    instances: list[RunningInstance] = []
    for engine in engines:
        inst = _instance_from_engine(engine, perf_by_id)
        instances.append(inst)
        if not agent_id:
            status = inst.status
            agent_id = inst.agent_id
            tick_count = inst.tick_count

    if not engines:
        disk_info = _infer_latest_session_status(strategy_dir, run_key)
        if disk_info:
            status = disk_info["status"]
            agent_id = disk_info["agent_id"]
            tick_count = disk_info["tick_count"]

    latest_session_pnl = 0.0
    if sessions_perf:
        latest = max(
            (p for p in sessions_perf if p.kind == "session"),
            key=lambda p: p.session_num,
            default=None,
        )
        if latest:
            latest_session_pnl = latest.total_pnl

    return StrategySummary(
        slug=strategy.slug,
        name=strategy.name,
        description=strategy.description,
        status=status,
        agent_id=agent_id,
        session_count=_count_sessions(strategy_dir),
        experiment_count=_count_experiments(strategy_dir),
        tick_count=tick_count,
        daily_pnl=latest_session_pnl,
        total_pnl=float(totals.get("total_pnl", 0.0)),
        total_volume=float(totals.get("volume", 0.0)),
        open_positions=int(totals.get("open_positions", 0)),
        instances=instances,
    )


# ── Agent routes ──


@router.get("", response_model=list[AgentSummary])
async def list_agents(user: WebUser = Depends(get_current_user)):
    """List all Agents, each with its strategies and their status."""
    import asyncio as _asyncio

    agents = _agent_store().list_all()
    store = _strategy_store()

    # Flatten every (agent, strategy) summary into a single gather so all
    # per-strategy performance fetches run concurrently across all agents,
    # not just within each agent (cold-cache latency O(1) round-trips).
    coros = []
    owners: list[str] = []
    for agent in agents:
        for strategy in store.list(agent.slug):
            coros.append(_build_strategy_summary(strategy))
            owners.append(agent.slug)

    summaries = await _asyncio.gather(*coros, return_exceptions=True)

    by_agent: dict[str, list[StrategySummary]] = {agent.slug: [] for agent in agents}
    for owner_slug, summary in zip(owners, summaries):
        if isinstance(summary, StrategySummary):
            by_agent[owner_slug].append(summary)

    results: list[AgentSummary] = []
    for agent in agents:
        strat_summaries = by_agent[agent.slug]
        results.append(
            AgentSummary(
                slug=agent.slug,
                name=agent.name,
                description=agent.description,
                consultable=agent.consultable,
                when_to_consult=agent.when_to_consult,
                agent_key=agent.agent_key,
                strategy_count=len(strat_summaries),
                strategies=strat_summaries,
                **_aggregate_strategy_perf(strat_summaries),
            )
        )
    return results


def _aggregate_strategy_perf(strategies: list[StrategySummary]) -> dict[str, Any]:
    """Roll up per-strategy performance into agent-level aggregates for summary cards."""
    return {
        "status": (
            "running" if any(s.status == "running" for s in strategies) else "idle"
        ),
        "session_count": sum(s.session_count for s in strategies),
        "experiment_count": sum(s.experiment_count for s in strategies),
        "tick_count": sum(s.tick_count for s in strategies),
        "daily_pnl": sum(s.daily_pnl for s in strategies),
        "total_pnl": sum(s.total_pnl for s in strategies),
        "total_volume": sum(s.total_volume for s in strategies),
        "open_positions": sum(s.open_positions for s in strategies),
        "instances": [inst for s in strategies for inst in s.instances],
    }


@router.get("/{slug}", response_model=AgentDetail)
async def get_agent(slug: str, user: WebUser = Depends(get_current_user)):
    """Get Agent detail + its strategies."""
    agent = _get_agent(slug)
    strategies = _strategy_store().list(slug)
    import asyncio as _asyncio

    summaries = await _asyncio.gather(
        *[_build_strategy_summary(s) for s in strategies],
        return_exceptions=True,
    )
    strat_summaries = [s for s in summaries if isinstance(s, StrategySummary)]

    return AgentDetail(
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        agent_md=(
            (agent.agent_dir / "AGENT.md").read_text()
            if (agent.agent_dir / "AGENT.md").exists()
            else ""
        ),
        agent_key=agent.agent_key,
        tools=agent.tools,
        when_to_consult=agent.when_to_consult,
        consultable=agent.consultable,
        server_required=agent.server_required,
        strategies=strat_summaries,
    )


@router.post("", response_model=AgentSummary)
async def create_agent(
    req: CreateAgentRequest, user: WebUser = Depends(get_current_user)
):
    """Create a new Agent (identity + brain; strategies are added separately)."""
    agent = _agent_store().create(
        name=req.name,
        description=req.description,
        instructions=req.instructions,
        agent_key=req.agent_key,
        tools=req.tools,
        when_to_consult=req.when_to_consult,
        server_required=req.server_required,
        created_by=user.id,
    )
    return AgentSummary(
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        consultable=agent.consultable,
        when_to_consult=agent.when_to_consult,
        agent_key=agent.agent_key,
    )


@router.put("/{slug}")
async def update_agent_md(
    slug: str, req: UpdateAgentMdRequest, user: WebUser = Depends(get_current_user)
):
    """Update AGENT.md content."""
    agent = _get_agent(slug)
    (agent.agent_dir / "AGENT.md").write_text(req.content)
    return {"updated": True}


@router.delete("/{slug}")
async def delete_agent(slug: str, user: WebUser = Depends(get_current_user)):
    """Delete an Agent. Refuses if any of its strategies has a running instance."""
    _get_agent(slug)
    store = _strategy_store()
    for s in store.list(slug):
        running = [e for e in _get_engines_for(slug, s.slug) if e.is_running]
        if running:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete an Agent with running strategies. Stop them first.",
            )
    _agent_store().delete(slug)
    return {"deleted": True}


@router.post("/{slug}/consult")
async def consult_agent(
    slug: str, req: ConsultRequest, user: WebUser = Depends(get_current_user)
):
    """Run an Agent consult (its brain to completion) and return the answer."""
    from condor.agents.consult import run_consult
    from config_manager import get_config_manager

    if not req.task:
        raise HTTPException(status_code=400, detail="task is required")

    # The consult binds the agent's MCP toolset to ``server_name``'s live
    # credentials, so gate it on server access exactly like the portfolio/bots
    # routes do — otherwise any session could consult against a server it was
    # never granted (IDOR). Only enforce when a server is actually requested;
    # serverless consults need no server scope.
    if req.server_name and not get_config_manager().has_server_access(
        user.id, req.server_name
    ):
        raise HTTPException(status_code=403, detail="No access")

    # Web callers always act as themselves; the ``user_id`` override is reserved
    # for trusted internal/MCP callers and must not let a session impersonate
    # another user's memory/skill scope.
    answer = await run_consult(
        slug=slug,
        user_id=user.id,
        chat_id=req.chat_id,
        server_name=req.server_name,
        task=req.task,
        context=req.context,
    )
    return {"agent": slug, "answer": answer}


# ── Delegate (fire-and-forget background tasks) ──


@router.post("/{slug}/delegate")
async def delegate_agent(
    slug: str, req: DelegateRequest, user: WebUser = Depends(get_current_user)
):
    """Delegate a one-off task to a detached background Agent instance.

    Returns immediately with a ``task_id``; the agent runs unattended (ACP
    auto-approve) until done, then notifies the user. The async sibling of
    ``/consult``.
    """
    from condor.agents.delegate import start_delegation
    from config_manager import get_config_manager

    _get_agent(slug)
    if not req.task:
        raise HTTPException(status_code=400, detail="task is required")

    # Same server-scope gate as consult: a delegate binds the agent's MCP toolset
    # to ``server_name``'s live credentials, so refuse a server the caller can't access.
    if req.server_name and not get_config_manager().has_server_access(
        user.id, req.server_name
    ):
        raise HTTPException(status_code=403, detail="No access")

    dt = await start_delegation(
        agent_slug=slug,
        user_id=req.user_id or user.id,
        chat_id=req.chat_id,
        server_name=req.server_name,
        task=req.task,
        timeout_s=req.timeout_s,
    )
    return {"task_id": dt.task_id, "status": dt.status}


@router.get("/delegations")
async def list_delegations(user: WebUser = Depends(get_current_user)):
    """List in-flight and finished delegations (this process).

    Returns the full record per task (status + result/error) so the dashboard can
    render an at-a-glance list without a follow-up fetch per row. The registry is
    in-memory and small (ephemeral, per-process), so the payload stays cheap.
    """
    from condor.agents.delegate import get_all_delegations

    return {
        "delegations": [dt.to_dict() for dt in get_all_delegations().values()]
    }


@router.get("/delegations/{task_id}")
async def get_delegation_status(
    task_id: str, user: WebUser = Depends(get_current_user)
):
    """Get a delegation's status + result/error."""
    from condor.agents.delegate import get_delegation

    dt = get_delegation(task_id)
    if dt is None:
        raise HTTPException(status_code=404, detail=f"Delegation '{task_id}' not found")
    return dt.to_dict()


@router.post("/delegations/{task_id}/stop")
async def stop_delegation_route(
    task_id: str, user: WebUser = Depends(get_current_user)
):
    """Cancel a running delegation (status -> stopped)."""
    from condor.agents.delegate import get_delegation, stop_delegation

    if get_delegation(task_id) is None:
        raise HTTPException(status_code=404, detail=f"Delegation '{task_id}' not found")
    stopped = await stop_delegation(task_id)
    return {"stopped": stopped}


# ── Strategy CRUD ──


@router.get("/{slug}/strategies", response_model=list[StrategySummary])
async def list_strategies(slug: str, user: WebUser = Depends(get_current_user)):
    """List strategies owned by an Agent with status/perf."""
    _get_agent(slug)
    import asyncio as _asyncio

    strategies = _strategy_store().list(slug)
    summaries = await _asyncio.gather(
        *[_build_strategy_summary(s) for s in strategies],
        return_exceptions=True,
    )
    return [s for s in summaries if isinstance(s, StrategySummary)]


@router.post("/{slug}/strategies", response_model=StrategySummary)
async def create_strategy(
    slug: str, req: CreateStrategyRequest, user: WebUser = Depends(get_current_user)
):
    """Create a new strategy (playbook) under an Agent."""
    _get_agent(slug)
    strategy = _strategy_store().create(
        agent_slug=slug,
        name=req.name,
        description=req.description,
        instructions=req.instructions,
        agent_key=req.agent_key,
        default_config=req.config,
        default_trading_context=req.default_trading_context,
        created_by=user.id,
    )

    if req.config:
        from condor.agents.config import AgentConfig, save_agent_config

        save_agent_config(strategy.dir, AgentConfig.from_dict(req.config))

    learnings_path = strategy.dir / "learnings.md"
    if not learnings_path.exists():
        learnings_path.write_text(
            "# Learnings\n\n## Active Insights\n\n## Retired Insights\n"
        )

    return StrategySummary(
        slug=strategy.slug,
        name=strategy.name,
        description=strategy.description,
        status="idle",
    )


@router.get("/{slug}/strategies/{sslug}", response_model=StrategyDetail)
async def get_strategy(
    slug: str, sslug: str, user: WebUser = Depends(get_current_user)
):
    """Get strategy detail."""
    strategy = _get_strategy(slug, sslug)
    strategy_dir = strategy.dir
    run_key = _runkey(slug, sslug)

    md_path = strategy_dir / "strategy.md"
    strategy_md = md_path.read_text() if md_path.exists() else ""

    from condor.agents.config import load_full_config

    config_dict = load_full_config(strategy_dir, strategy.default_config)

    learnings_path = strategy_dir / "learnings.md"
    learnings = learnings_path.read_text() if learnings_path.exists() else ""

    try:
        sessions_perf, _totals = await _compute_strategy_performance(
            run_key, strategy_dir, strategy.default_config
        )
    except Exception as e:
        log.warning("compute_strategy_performance(%s) failed: %s", run_key, e)
        sessions_perf = []
    perf_by_id = {p.agent_id: p for p in sessions_perf}

    engines = _get_engines_for(slug, sslug)
    status = "idle"
    agent_id = ""
    instances = []
    for engine in engines:
        inst = _instance_from_engine(engine, perf_by_id)
        instances.append(inst)
        if not agent_id:
            status = inst.status
            agent_id = inst.agent_id

    if not engines:
        disk_info = _infer_latest_session_status(strategy_dir, run_key)
        if disk_info:
            status = disk_info["status"]
            agent_id = disk_info["agent_id"]

    return StrategyDetail(
        slug=sslug,
        agent_slug=slug,
        name=strategy.name,
        description=strategy.description,
        strategy_md=strategy_md,
        config=config_dict,
        default_trading_context=strategy.default_trading_context,
        learnings=learnings,
        status=status,
        agent_id=agent_id,
        sessions=_list_sessions(strategy_dir),
        experiments=_list_experiments(strategy_dir),
        instances=instances,
    )


@router.put("/{slug}/strategies/{sslug}")
async def update_strategy_md(
    slug: str,
    sslug: str,
    req: UpdateStrategyMdRequest,
    user: WebUser = Depends(get_current_user),
):
    """Update strategy.md content."""
    strategy = _get_strategy(slug, sslug)
    (strategy.dir / "strategy.md").write_text(req.content)
    return {"updated": True}


@router.put("/{slug}/strategies/{sslug}/config")
async def update_strategy_config(
    slug: str,
    sslug: str,
    req: UpdateConfigRequest,
    user: WebUser = Depends(get_current_user),
):
    """Update a strategy's runtime config."""
    strategy = _get_strategy(slug, sslug)
    from condor.agents.config import load_full_config, save_full_config

    config_dict = load_full_config(strategy.dir, strategy.default_config)
    config_dict.update(req.config)
    save_full_config(strategy.dir, config_dict)
    return {"updated": True, "config": config_dict}


@router.delete("/{slug}/strategies/{sslug}")
async def delete_strategy(
    slug: str, sslug: str, user: WebUser = Depends(get_current_user)
):
    """Delete a strategy. Refuses if it has a running instance."""
    _get_strategy(slug, sslug)
    running = [e for e in _get_engines_for(slug, sslug) if e.is_running]
    if running:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a running strategy. Stop all instances first.",
        )
    _strategy_store().delete(slug, sslug)
    return {"deleted": True}


# ── Strategy performance ──


@router.get(
    "/{slug}/strategies/{sslug}/performance",
    response_model=StrategyPerformanceResponse,
)
async def get_strategy_performance(
    slug: str, sslug: str, user: WebUser = Depends(get_current_user)
):
    """Return per-session performance and roll-up totals for a strategy."""
    strategy = _get_strategy(slug, sslug)
    run_key = _runkey(slug, sslug)
    sessions, totals = await _compute_strategy_performance(
        run_key, strategy.dir, strategy.default_config
    )
    running_ids = {e.agent_id for e in _get_engines_for(slug, sslug) if e.is_running}
    for s in sessions:
        s.status = "running" if s.agent_id in running_ids else "closed"
    return StrategyPerformanceResponse(slug=sslug, sessions=sessions, totals=totals)


@router.get("/{slug}/strategies/{sslug}/sessions/{session_num}/executors")
async def get_session_executors(
    slug: str,
    sslug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    """Return executors + performance for a single session."""
    from condor.agents.performance import fetch_agent_performance

    strategy = _get_strategy(slug, sslug)
    agent_id = f"{_runkey(slug, sslug)}_{session_num}"
    client, _server = await _get_client_for_strategy(
        strategy.dir, strategy.default_config
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


# ── Strategy lifecycle ──


@router.post("/{slug}/strategies/{sslug}/start")
async def start_strategy(
    slug: str,
    sslug: str,
    req: StartStrategyRequest,
    user: WebUser = Depends(get_current_user),
):
    """Start a strategy (creates a new session under its Agent)."""
    from condor.agents.config import load_full_config
    from condor.agents.engine import TickEngine

    agent = _get_agent(slug)
    strategy = _get_strategy(slug, sslug)

    config_dict = load_full_config(strategy.dir, strategy.default_config)
    if req.config:
        config_dict.update(req.config)

    if req.trading_context:
        config_dict["trading_context"] = req.trading_context
    elif not config_dict.get("trading_context") and strategy.default_trading_context:
        config_dict["trading_context"] = strategy.default_trading_context

    new_engine = TickEngine(
        agent=agent,
        strategy=strategy,
        config=config_dict,
        chat_id=req.chat_id,
        user_id=req.user_id or user.id,
    )
    await new_engine.start()
    return {
        "started": True,
        "agent_id": new_engine.agent_id,
        "session_num": new_engine.session_num,
    }


@router.post("/{slug}/strategies/{sslug}/stop")
async def stop_strategy(
    slug: str,
    sslug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Stop a running strategy. If agent_id given, stop that instance; else all."""
    if agent_id:
        from condor.agents.engine import get_engine

        engine = get_engine(agent_id)
        if not engine:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        await engine.stop()
    else:
        engines = _get_engines_for(slug, sslug)
        if not engines:
            raise HTTPException(status_code=404, detail="No running strategy found")
        for engine in engines:
            await engine.stop()
    return {"stopped": True}


@router.post("/{slug}/strategies/{sslug}/shutdown")
async def shutdown_strategy(
    slug: str,
    sslug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Emergency shutdown: wind down positions/executors per shutdown.md, then stop.

    Escalation above the plain (position-preserving) ``/stop``. If ``agent_id`` is
    given, only that instance is wound down; otherwise every running instance of
    this strategy is.
    """
    reason = "manual emergency stop"
    if agent_id:
        from condor.agents.engine import get_engine

        engine = get_engine(agent_id)
        if not engine:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        await engine._run_shutdown(reason=reason)
    else:
        engines = _get_engines_for(slug, sslug)
        if not engines:
            raise HTTPException(status_code=404, detail="No running strategy found")
        for engine in engines:
            await engine._run_shutdown(reason=reason)
    return {"shutdown": True}


@router.post("/{slug}/strategies/{sslug}/pause")
async def pause_strategy(
    slug: str,
    sslug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Pause a running strategy."""
    if agent_id:
        from condor.agents.engine import get_engine

        engine = get_engine(agent_id)
        if not engine or not engine.is_running:
            raise HTTPException(
                status_code=404, detail=f"Agent '{agent_id}' not found or not running"
            )
        engine.pause()
    else:
        engines = [e for e in _get_engines_for(slug, sslug) if e.is_running]
        if not engines:
            raise HTTPException(status_code=404, detail="No running strategy found")
        engines[0].pause()
    return {"paused": True}


@router.post("/{slug}/strategies/{sslug}/resume")
async def resume_strategy(
    slug: str,
    sslug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Resume a paused strategy."""
    if agent_id:
        from condor.agents.engine import get_engine

        engine = get_engine(agent_id)
        if not engine:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        engine.resume()
    else:
        engines = _get_engines_for(slug, sslug)
        if not engines:
            raise HTTPException(status_code=404, detail="No strategy found")
        engines[0].resume()
    return {"resumed": True}


# ── Learnings ──


@router.get("/{slug}/strategies/{sslug}/learnings")
async def get_learnings(
    slug: str, sslug: str, user: WebUser = Depends(get_current_user)
):
    """Read a strategy's learnings.md."""
    strategy = _get_strategy(slug, sslug)
    learnings_path = strategy.dir / "learnings.md"
    content = learnings_path.read_text() if learnings_path.exists() else ""
    return {"content": content}


@router.put("/{slug}/strategies/{sslug}/learnings")
async def update_learnings(
    slug: str,
    sslug: str,
    req: UpdateLearningsRequest,
    user: WebUser = Depends(get_current_user),
):
    """Update a strategy's learnings.md."""
    strategy = _get_strategy(slug, sslug)
    (strategy.dir / "learnings.md").write_text(req.content)
    return {"updated": True}


# ── Sessions ──


@router.get("/{slug}/strategies/{sslug}/sessions")
async def list_strategy_sessions(
    slug: str, sslug: str, user: WebUser = Depends(get_current_user)
):
    """List sessions for a strategy."""
    strategy = _get_strategy(slug, sslug)
    sessions = _list_sessions(strategy.dir)
    return {"sessions": [s.model_dump() for s in sessions]}


@router.get("/{slug}/strategies/{sslug}/sessions/{session_num}/journal")
async def get_journal(
    slug: str,
    sslug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    """Read journal.md for a session."""
    strategy = _get_strategy(slug, sslug)
    session_dir = _get_session_dir(strategy.dir, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")
    journal_path = session_dir / "journal.md"
    content = journal_path.read_text() if journal_path.exists() else ""
    return {"content": content}


@router.get("/{slug}/strategies/{sslug}/sessions/{session_num}/snapshots")
async def list_snapshots(
    slug: str,
    sslug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    """List snapshots for a session."""
    strategy = _get_strategy(slug, sslug)
    session_dir = _get_session_dir(strategy.dir, session_num)
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
                content = f.read_text()
                ts_match = re.search(
                    r"^# (?:Snapshot|Tick) #\d+ — (.+)$", content, re.MULTILINE
                )
                timestamp = ts_match.group(1) if ts_match else ""
                snapshots.append(
                    SnapshotSummary(tick=tick, timestamp=timestamp, file=f.name)
                )
        break

    return {"snapshots": [s.model_dump() for s in snapshots]}


@router.get("/{slug}/strategies/{sslug}/sessions/{session_num}/snapshots/{tick}")
async def get_snapshot(
    slug: str,
    sslug: str,
    session_num: int,
    tick: int,
    user: WebUser = Depends(get_current_user),
):
    """Read a specific snapshot."""
    strategy = _get_strategy(slug, sslug)
    session_dir = _get_session_dir(strategy.dir, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")

    for snap_dir_name, prefix in [("snapshots", "snapshot"), ("runs", "run")]:
        path = session_dir / snap_dir_name / f"{prefix}_{tick}.md"
        if path.exists():
            return {"content": path.read_text(), "tick": tick}
    raise HTTPException(status_code=404, detail=f"Snapshot {tick} not found")


# ── Experiments ──


@router.get("/{slug}/strategies/{sslug}/experiments")
async def list_strategy_experiments(
    slug: str, sslug: str, user: WebUser = Depends(get_current_user)
):
    """List experiments for a strategy."""
    strategy = _get_strategy(slug, sslug)
    experiments = _list_experiments(strategy.dir)
    return {"experiments": [e.model_dump() for e in experiments]}


@router.get("/{slug}/strategies/{sslug}/experiments/{exp_num}")
async def get_experiment(
    slug: str, sslug: str, exp_num: int, user: WebUser = Depends(get_current_user)
):
    """Read an experiment snapshot."""
    strategy = _get_strategy(slug, sslug)
    path = _get_experiment_file(strategy.dir, exp_num)
    if not path:
        raise HTTPException(status_code=404, detail=f"Experiment {exp_num} not found")
    return {"content": path.read_text(), "number": exp_num}


# ── Routines / reports ──


@router.get("/{slug}/strategies/{sslug}/routines")
async def get_strategy_routines(
    slug: str, sslug: str, user: WebUser = Depends(get_current_user)
):
    """List routines available to this strategy.

    Routines live at the **agent** level (``agents/{slug}/routines``) and
    are shared across all of the agent's strategies, so this lists the owning
    agent's routines (keyed ``{agent_slug}/{name}`` in the store).
    """
    _get_strategy(slug, sslug)  # validate exists
    from condor.routine_store import get_routine_store

    store = get_routine_store()
    all_routines = store.list_routines()
    prefix = f"{slug}/"
    return [r for r in all_routines if r.get("name", "").startswith(prefix)]


@router.get("/{slug}/strategies/{sslug}/reports")
async def get_strategy_reports(
    slug: str,
    sslug: str,
    limit: int = 50,
    user: WebUser = Depends(get_current_user),
):
    """Get reports generated by this strategy's routines."""
    _get_strategy(slug, sslug)  # validate exists
    from condor.reports import list_reports

    run_key = _runkey(slug, sslug)
    prefix = f"{run_key}/"
    reports, _total = list_reports(source_type="routine", search=run_key, limit=limit)
    matched = [r for r in reports if r.get("source_name", "").startswith(prefix)]
    return {
        "reports": [ReportSummary(**r).model_dump() for r in matched],
        "total": len(matched),
    }

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
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.agents.sessions_index import (
    count_experiments,
    count_sessions,
    enumerate_agent_ids,
    find_experiment_file,
    find_session_dir,
    infer_latest_session_status,
    list_experiments,
    list_sessions,
)
from condor.web.auth import get_current_user
from condor.web.models import ReportSummary, WebUser

# ── Simple in-memory TTL cache for performance data ──
_PERF_CACHE: dict[str, tuple[float, Any]] = {}
_PERF_TTL = 30.0  # seconds

# Long-lived cache for CLOSED sessions/experiments, keyed by agent_id.
# A closed session's executors are immutable (no engine running, no open
# executors), so its performance never changes — fetch it once and freeze it.
# Only ids that are inactive (no registered engine, not the newest session),
# fetched successfully, with open_count == 0, and not in controller mode land
# here; everything else keeps flowing through the 30s TTL path above.
_CLOSED_PERF_CACHE: dict[str, Any] = {}


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
    server_name: str = ""
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
    server_name: str = ""


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
    user_id: int | None = None  # Accepted for compat but ignored (see handler)


class DelegateRequest(BaseModel):
    task: str
    chat_id: int = 0  # Telegram chat for the completion notification
    user_id: int | None = None  # Accepted for compat but ignored (see handler)
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


# ── Disk lookups ──
# Enumeration/counting of sessions & experiments on disk lives in
# condor.agents.sessions_index (imported at the top), next to the journal
# code that owns the layout. This module keeps only HTTP concerns.


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


def _session_bot_base(strategy_dir: Path, default_config: dict | None, num: int) -> str:
    """Bot base name a session operates: per-session config, else strategy default.

    A non-empty per-session ``bot_name`` wins (so runtime-named bots that record
    their deployed name resolve), but an empty/absent one falls back to the
    strategy default — early sessions predating the config's ``bot_name`` saved it
    as ``''`` and must still map to the shared bot they operated. Empty string when
    neither is set (direct-executor strategies). Shared by the per-session PnL
    distribution and the operator's live-executor view so both resolve identically.
    """
    from condor.agents.config import load_full_config
    from condor.agents.sessions_index import find_session_dir

    default_base = (default_config or {}).get("bot_name", "") or ""
    sd = find_session_dir(strategy_dir, num)
    if not sd:
        return default_base
    return load_full_config(sd, default_config).get("bot_name", "") or default_base


def _session_start_epoch(strategy_dir: Path, num: int) -> float:
    """Session start time: config.yml is written once at start, so its mtime is stable."""
    from condor.agents.sessions_index import find_session_dir

    sd = find_session_dir(strategy_dir, num)
    if not sd:
        return 0.0
    cfg = sd / "config.yml"
    target = cfg if cfg.exists() else sd
    try:
        return os.path.getmtime(target)
    except OSError:
        return 0.0


async def _apply_bot_mode_pnl(
    real_sessions: list, strategy_dir: Path, default_config: dict | None, client: Any
) -> None:
    """Distribute each operated bot's PnL across session windows (bot-mode only).

    Realized PnL, volume and trade counts are attributed to whichever session was
    operating during each slice of the bot's cumulative history; live unrealized
    PnL and open positions go to the current operator (the latest session per bot
    base). Session windows tile the timeline ``[start_N, start_{N+1})`` (last → now)
    so every slice sums back to the bot's cumulative with no double counting.

    Works uniformly for single- and multi-controller bots (history sums controllers
    per instance) and for a strategy re-launched under several instances. Strategies
    whose sessions resolve NO bot_name (direct-executor agents) are left untouched —
    their per-session executor attribution already stands.
    """
    from condor.fetchers.bot_performance import (
        bot_executor_rows,
        fetch_all_bot_performance,
        fetch_instance_history,
        resolve_bot,
        resolve_bot_instances,
        slice_history,
    )

    if not client or not real_sessions:
        return
    session_base = {
        s.session_num: _session_bot_base(strategy_dir, default_config, s.session_num)
        for s in real_sessions
    }
    bases = {b for b in session_base.values() if b}
    if not bases:
        return  # direct-executor strategy — nothing to attribute

    try:
        all_perf = await fetch_all_bot_performance(client)
    except Exception as e:
        log.warning("bot perf fetch for %s failed: %s", strategy_dir.name, e)
        return

    instances_by_base = {b: resolve_bot_instances(all_perf, b) for b in bases}
    all_instances = sorted({i for lst in instances_by_base.values() for i in lst})
    MAX_INSTANCES = 24
    if len(all_instances) > MAX_INSTANCES:
        log.warning(
            "bot history for %s: %d instances, capping at %d newest "
            "(older sessions may under-report)",
            strategy_dir.name,
            len(all_instances),
            MAX_INSTANCES,
        )
        all_instances = all_instances[-MAX_INSTANCES:]
    history = {
        inst: await fetch_instance_history(client, inst) for inst in all_instances
    }

    # Distribute realized/volume/trades per session window.
    ordered = sorted(real_sessions, key=lambda s: s.session_num)
    now = time.time()
    for i, s in enumerate(ordered):
        base = session_base[s.session_num]
        if not base:
            continue
        start = _session_start_epoch(strategy_dir, s.session_num)
        end = (
            _session_start_epoch(strategy_dir, ordered[i + 1].session_num)
            if i + 1 < len(ordered)
            else now
        )
        insts = [history[k] for k in instances_by_base[base] if k in history]
        realized, volume, trades = slice_history(insts, start, end)
        s.realized_pnl += realized
        s.volume += volume
        s.trade_count += int(round(trades))
        s.total_pnl = s.realized_pnl + s.unrealized_pnl

    # Live unrealized + open positions → the current operator per base.
    for base in bases:
        bot = resolve_bot(all_perf, base)
        if not bot:
            continue
        ops = [s for s in real_sessions if session_base[s.session_num] == base]
        if not ops:
            continue
        operator = max(ops, key=lambda s: s.session_num)
        b_rows = bot_executor_rows(bot)
        operator.unrealized_pnl += float(bot.get("unrealized_pnl_quote", 0) or 0)
        operator.fees += float(bot.get("cum_fees_quote", 0) or 0)
        operator.open_count += sum(1 for r in b_rows if r["status"] == "RUNNING")
        operator.executors = list(operator.executors) + b_rows
        operator.total_pnl = operator.realized_pnl + operator.unrealized_pnl


async def _compute_strategy_performance(
    run_key: str, strategy_dir: Path, default_config: dict | None
):
    """Return list of AgentPerformanceModel plus rolled-up totals.

    The assembled rollup is cached ~30s (``_PERF_CACHE``); underneath, closed
    sessions/experiments are served from ``_CLOSED_PERF_CACHE`` so only active
    ids hit the backend after the TTL expires.
    """
    from condor.agents.performance import fetch_agent_performance_batch

    cached = _cache_get(f"perf:{run_key}")
    if cached is not None:
        return cached

    ids = enumerate_agent_ids(run_key, strategy_dir)
    client, _server = await _get_client_for_strategy(strategy_dir, default_config)

    # Per-session executor fetches stay bot-free (bot_names=None below) so closed
    # sessions can be frozen; bot-mode PnL is distributed per session afterward by
    # _apply_bot_mode_pnl from the controller history, which handles both fixed and
    # runtime-named (per-session config) bots.
    sessions: list[AgentPerformanceModel] = []
    if client and ids:
        from condor.agents.engine import get_all_engines

        # Split ids by state: closed sessions/experiments are immutable, so only
        # ids with a live engine (running/paused, incl. experiments) plus the
        # newest session — whose executors may still be closing out — are
        # re-fetched; everything else is served from the long-lived frozen cache.
        engine_ids = {e.agent_id for e in get_all_engines().values()}
        latest_session = max((n for _, n, k in ids if k == "session"), default=None)
        active_ids = {
            aid
            for aid, num, kind in ids
            if aid in engine_ids or (kind == "session" and num == latest_session)
        }
        # An id active again (e.g. restored engine) must not serve a stale
        # frozen value once it goes idle — evict so it gets one final fetch.
        for aid in active_ids:
            _CLOSED_PERF_CACHE.pop(aid, None)

        fetch_ids = [
            aid
            for aid, _, _ in ids
            if aid in active_ids or aid not in _CLOSED_PERF_CACHE
        ]

        perf_map: dict[str, Any] = {}
        failed_ids: set[str] = set()
        if fetch_ids:
            try:
                perf_map = await fetch_agent_performance_batch(
                    client, fetch_ids, None, failed_ids=failed_ids
                )
            except Exception as e:
                log.warning("fetch_agent_performance_batch(%s) failed: %s", run_key, e)
                perf_map = {}
                failed_ids = set(fetch_ids)

        for agent_id, num, kind in ids:
            perf = perf_map.get(agent_id)
            if perf is None:
                perf = _CLOSED_PERF_CACHE.get(agent_id)
            if perf is None:
                continue
            # Freeze immutable results: fetched fine, no engine, not the newest
            # session, and nothing still open whose unrealized PnL could move.
            # Per-session perf is bot-free (the shared bot is merged once below),
            # so it is immutable for a closed session even in controller mode.
            if (
                agent_id in perf_map
                and agent_id not in active_ids
                and agent_id not in failed_ids
                and perf.open_count == 0
            ):
                _CLOSED_PERF_CACHE[agent_id] = perf
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

    # Bot-mode: distribute each operated bot's PnL across the session windows that
    # produced it (realized/volume/trades per window; live unrealized/open on the
    # current operator). Direct-executor strategies are left untouched. Because the
    # bot is DISTRIBUTED — not duplicated — the totals below are a plain additive
    # sum of the rows and stay correct for both modes with no double counting.
    if client and real_sessions:
        await _apply_bot_mode_pnl(real_sessions, strategy_dir, default_config, client)

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
        disk_info = infer_latest_session_status(strategy_dir, run_key)
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
        session_count=count_sessions(strategy_dir),
        experiment_count=count_experiments(strategy_dir),
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


# ── Delegation status/list routes ──
# NOTE: Starlette matches routes in registration order, so these literal
# /delegations paths MUST be registered before the /{slug} catch-all below;
# otherwise GET /agents/delegations would match get_agent with
# slug="delegations" and 404.


@router.get("/delegations")
async def list_delegations(user: WebUser = Depends(get_current_user)):
    """List in-flight and finished delegations (this process).

    Returns the full record per task (status + result/error) so the dashboard can
    render an at-a-glance list without a follow-up fetch per row. The registry is
    in-memory and small (ephemeral, per-process), so the payload stays cheap.
    """
    from condor.agents.delegate import get_all_delegations

    return {"delegations": [dt.to_dict() for dt in get_all_delegations().values()]}


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
        server_name=agent.server_name,
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
        server_name=req.server_name,
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

    # Web callers always act as themselves (mirror consult): honoring
    # ``req.user_id`` here would let any authenticated session run a delegation
    # under another user's memory scope and server grants.
    dt = await start_delegation(
        agent_slug=slug,
        user_id=user.id,
        chat_id=req.chat_id,
        server_name=req.server_name,
        task=req.task,
        timeout_s=req.timeout_s,
    )
    return {"task_id": dt.task_id, "status": dt.status}


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
        disk_info = infer_latest_session_status(strategy_dir, run_key)
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
        sessions=[SessionInfo(**s) for s in list_sessions(strategy_dir)],
        experiments=[ExperimentInfo(**e) for e in list_experiments(strategy_dir)],
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
    # Bot-mode: the session operates a named bot whose executors live in the bot
    # container, not the agent_id-keyed table. Resolve the bot_name (per-session
    # config wins, so runtime-named bots that record their name are picked up)
    # and let fetch_agent_performance merge its live positions — but ONLY for the
    # current operator (the latest session that deployed the live bot instance).
    # A closed session shows only its own direct executors, never the live bot's
    # open positions, which belong to whoever is running the shared bot now.
    from condor.agents.sessions_index import enumerate_agent_ids

    session_nums = [
        n
        for _, n, k in enumerate_agent_ids(_runkey(slug, sslug), strategy.dir)
        if k == "session"
    ]
    is_operator = bool(session_nums) and session_num == max(session_nums)
    bot_name = (
        _session_bot_base(strategy.dir, strategy.default_config, session_num)
        if is_operator
        else ""
    )
    perf = await fetch_agent_performance(client, agent_id, bot_name=bot_name)
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

    # Web callers always act as themselves (mirror consult): honoring
    # ``req.user_id`` would let any authenticated session start the engine
    # under another user's memory scope and accessible-servers fallback.
    new_engine = TickEngine(
        agent=agent,
        strategy=strategy,
        config=config_dict,
        chat_id=req.chat_id,
        user_id=user.id,
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
    sessions = list_sessions(strategy.dir)
    return {"sessions": [SessionInfo(**s).model_dump() for s in sessions]}


@router.get("/{slug}/strategies/{sslug}/sessions/{session_num}/journal")
async def get_journal(
    slug: str,
    sslug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    """Read journal.md for a session."""
    strategy = _get_strategy(slug, sslug)
    session_dir = find_session_dir(strategy.dir, session_num)
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
    session_dir = find_session_dir(strategy.dir, session_num)
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
    session_dir = find_session_dir(strategy.dir, session_num)
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
    experiments = list_experiments(strategy.dir)
    return {"experiments": [ExperimentInfo(**e).model_dump() for e in experiments]}


@router.get("/{slug}/strategies/{sslug}/experiments/{exp_num}")
async def get_experiment(
    slug: str, sslug: str, exp_num: int, user: WebUser = Depends(get_current_user)
):
    """Read an experiment snapshot."""
    strategy = _get_strategy(slug, sslug)
    path = find_experiment_file(strategy.dir, exp_num)
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

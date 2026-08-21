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

import asyncio
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from condor.agents.attribution import (
    apply_bot_mode_pnl,
    current_owner_bases,
    session_ownership,
)
from condor.agents.sessions_index import (
    count_experiments,
    count_sessions,
    enumerate_agent_ids,
    find_experiment_file,
    find_session_dir,
    infer_latest_session_status,
    list_experiments,
    list_session_snapshots,
    list_sessions,
)
from condor.fsutil import atomic_write_text
from condor.web.auth import check_server_access, get_current_user
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
# Bounded LRU (same idiom as condor.web.routes.archived): each entry holds the
# full executor rows, so an unbounded dict would grow with every session ever
# run for the life of the process. Eviction is always safe — a miss just flows
# through the normal fetch path and re-freezes.
_CLOSED_PERF_CACHE_MAX = 256
_CLOSED_PERF_CACHE: OrderedDict[str, Any] = OrderedDict()


def _closed_perf_get(agent_id: str) -> Any | None:
    """Return the frozen entry, marking it most-recently-used."""
    perf = _CLOSED_PERF_CACHE.get(agent_id)
    if perf is not None:
        _CLOSED_PERF_CACHE.move_to_end(agent_id)
    return perf


def _closed_perf_put(agent_id: str, perf: Any) -> None:
    """Store a frozen entry, evicting the least-recently-used past the cap."""
    _CLOSED_PERF_CACHE[agent_id] = perf
    _CLOSED_PERF_CACHE.move_to_end(agent_id)
    while len(_CLOSED_PERF_CACHE) > _CLOSED_PERF_CACHE_MAX:
        _CLOSED_PERF_CACHE.popitem(last=False)


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
    win_rate: float | None = None
    server_name: str = ""
    total_amount_quote: float = 100.0
    trading_context: str = ""
    frequency_sec: int = 60
    tick_timeout_sec: int = 600
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
    # None = no closed executor rows to derive it from; see AgentPerformance.
    # Rendering it as 0% would report a bot-mode session as all-losses.
    win_rate: float | None = None
    open_count: int = 0
    closed_count: int = 0
    executors: list[dict[str, Any]] = []
    # ── Bot-mode attribution ────────────────────────────────────────────────
    # A session trading through bots earns nothing under its own agent_id: its
    # executors live inside the bot instance's own database and never reach the
    # agent_id-keyed table, and the only rows Condor can synthesize for them are
    # the positions open right now. A flat bot therefore renders as a session that
    # did nothing. These four fields carry what the aggregator already knew and
    # this model used to drop on the floor — which bots ran, which controllers,
    # what each closed, and whether the fee figure means anything.
    #
    # Populated by the per-session detail route, which resolves one session's
    # ownership exactly. The strategy rollup leaves them empty (it distributes a
    # bot's history across sessions rather than resolving instances per session)
    # and sets only ``fees_known``.
    bot_names: list[str] = []
    bot_instances: list[str] = []
    unresolved_bases: list[str] = []
    controllers: list[dict[str, Any]] = []
    close_type_counts: dict[str, int] = {}
    fees_known: bool = True


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


class AgentConfigRequest(BaseModel):
    """The few front-matter fields worth a control instead of a text editor.

    Every field is optional and ``None`` means "leave it alone", so a caller
    that only wants to move the server pin does not have to restate the rest.
    """

    server_required: bool | None = None
    server_name: str | None = Field(
        default=None,
        description="Pin the Agent to this Hummingbot API server. Empty string "
        "clears the pin and lets it follow the chat's ambient selection.",
    )
    agent_key: str | None = Field(
        default=None,
        description="The model this Agent answers on, everywhere it runs — "
        "chat, consult, delegate and loops. Empty string clears it, falling "
        "back to the chat's default model.",
    )


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


class SetStateRequest(BaseModel):
    """One scratch-KV write. The namespace comes from the URL, never the body."""

    key: str
    value: Any = None
    expires_in: int | None = None
    clear: bool = False


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
    # Canonical key of the session asking for the work (posted by the condor MCP
    # server from CONDOR_SESSION_KEY). Resolved to a conversation id below.
    session_key: str = ""
    # "notify" (push + transcript note) or "resume" (additionally wake the
    # asking conversation with the result). See FEAT-034.
    on_complete: str = "notify"


class NotifyRequest(BaseModel):
    text: str
    parse_mode: str = "Markdown"
    chat_id: int = 0  # Telegram chat to push to (0 = nothing to push to)
    user_id: int | None = None  # Accepted for compat but ignored (see handler)
    # Canonical key of the session announcing something (posted by the condor
    # MCP server from CONDOR_SESSION_KEY). Resolved to a conversation id below.
    session_key: str = ""


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
    from condor.runtime.loops import get_supervisor

    return get_supervisor().for_strategy(agent_slug, sslug)


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


# ── Session-ownership PnL attribution ──
# The whole engine — session ownership resolution, owner-window tiling, the
# slice-and-merge fee rules, and the current-owner rule for the live open book —
# lives in condor.agents.attribution ([[ARCH-191]]), shared with the agent's own
# view (condor.agents.performance) so the dashboard and the tick loop compute
# from one implementation. This module keeps only HTTP concerns and the caches.


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
    # apply_bot_mode_pnl from the controller history, which handles both fixed and
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

        # Snapshot the frozen entries up front (marking them recently-used) so
        # an LRU eviction during the await below can't drop a session from
        # this render.
        frozen: dict[str, Any] = {}
        for aid, _, _ in ids:
            if aid not in active_ids:
                perf = _closed_perf_get(aid)
                if perf is not None:
                    frozen[aid] = perf

        fetch_ids = [aid for aid, _, _ in ids if aid in active_ids or aid not in frozen]

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
                perf = frozen.get(agent_id)
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
                _closed_perf_put(agent_id, perf)
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
        await apply_bot_mode_pnl(real_sessions, strategy_dir, default_config, client)

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
        win_rate=p.win_rate if p else None,
        server_name=info.get("server_name", ""),
        total_amount_quote=info.get("total_amount_quote", 100),
        trading_context=info.get("trading_context", ""),
        frequency_sec=info.get("frequency_sec", 60),
        tick_timeout_sec=info.get("tick_timeout_sec", 600),
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

    summaries = await asyncio.gather(*coros, return_exceptions=True)

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
                when_to_consult=agent.consult_hint,
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


def _is_admin(user: WebUser) -> bool:
    from config_manager import get_config_manager

    return get_config_manager().is_admin(user.id)


def _can_see_delegation(record: dict, user: WebUser) -> bool:
    """Admins see everything; everyone else only what they started.

    A record whose ``user_id`` was never written (a transcript from before the
    status file carried one) belongs to nobody and is therefore admin-only —
    unowned must not mean unguarded.
    """
    return _is_admin(user) or (
        bool(record.get("user_id")) and record["user_id"] == user.id
    )


def _visible_record(task_id: str, user: WebUser) -> dict:
    """A delegation record: live if this process still holds it, else from disk.

    The fallback is what makes every read route work for a task that outlived
    the process that ran it (FEAT-035) — the registry stays the authority for
    anything running *now*, and history answers for everything else.

    Same idiom as ``_require_ownership`` in ``sessions.py``: the caller is
    compared against the record's own ``user_id``, which ``DelegateTask``
    carries from the moment it is started. 403 rather than 404 on a foreign
    task, matching ``conversations.py``.
    """
    from condor.agents.delegate import get_delegation
    from condor.agents.delegation_history import read_history

    dt = get_delegation(task_id)
    record = dt.to_dict() if dt is not None else read_history(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Delegation '{task_id}' not found")
    if not _can_see_delegation(record, user):
        raise HTTPException(status_code=403, detail="Not your delegation")
    return record


@router.get("/delegations")
async def list_delegations(user: WebUser = Depends(get_current_user)):
    """List in-flight and finished delegations (this process).

    Returns the full record per task (status + result/error) so the dashboard can
    render an at-a-glance list without a follow-up fetch per row. The registry is
    in-memory and small (ephemeral, per-process), so the payload stays cheap.

    Scoped to the caller's own delegations; admins get the whole registry.
    """
    from condor.agents.delegate import get_all_delegations

    visible = _is_admin(user)
    return {
        "delegations": [
            dt.to_dict()
            for dt in get_all_delegations().values()
            if visible or dt.user_id == user.id
        ]
    }


@router.get("/delegations/history")
async def list_delegation_history(
    agent: str | None = None,
    limit: int = 100,
    user: WebUser = Depends(get_current_user),
):
    """Every delegation ever recorded, newest first — across restarts (FEAT-035).

    Registered above ``/delegations/{task_id}`` so the literal path wins, for the
    same reason the whole block sits above ``/{slug}``.

    Returns *summary* rows: the bodies (``result``/``error``) are dropped, since
    a hundred rows must not ship a hundred answers — a row that gets opened
    fetches itself from ``/delegations/{task_id}``. Live tasks are included from
    the registry (and shadow their own on-disk copy), so this list is complete on
    its own rather than only telling half the story.
    """
    from condor.agents.delegate import get_all_delegations
    from condor.agents.delegation_history import list_history

    live = {
        dt.task_id: dt.to_dict()
        for dt in get_all_delegations().values()
        if agent in (None, dt.agent_slug)
    }
    records = [
        r
        for r in list_history(agent_slug=agent, limit=limit)
        if r["task_id"] not in live
    ]
    records.extend(live.values())
    records.sort(key=lambda r: r.get("started_at") or 0.0, reverse=True)

    return {
        "delegations": [
            {k: v for k, v in r.items() if k not in ("result", "error")}
            for r in records[:limit]
            if _can_see_delegation(r, user)
        ]
    }


@router.get("/delegations/{task_id}")
async def get_delegation_status(
    task_id: str, user: WebUser = Depends(get_current_user)
):
    """Get a delegation's status + result/error, live or from disk."""
    return _visible_record(task_id, user)


@router.get("/delegations/{task_id}/events")
async def get_delegation_events(
    task_id: str, user: WebUser = Depends(get_current_user)
):
    """Chronological session transcript for a delegation (this process).

    Split from ``get_delegation_status`` on purpose: that route feeds the MCP
    `delegate` tool -- an *agent* polling its own task, which must not be handed
    the whole reasoning stream -- while this one feeds a *human* watching the
    work happen. Same data, opposite appetites for verbosity.

    ``status`` rides along so a client knows when to stop polling without a
    second request.

    Once the process that ran it is gone the events come from the sidecar on
    disk, in the same projection — so a finished delegation renders exactly like
    a running one. Records older than that sidecar have only their markdown
    transcript, returned in ``markdown`` for the client to render instead.
    """
    from condor.agents.delegate import events_for_wire, get_delegation
    from condor.agents.delegation_history import read_history_events

    record = _visible_record(task_id, user)
    dt = get_delegation(task_id)
    if dt is not None:
        events, markdown = events_for_wire(dt.events), ""
    else:
        events, markdown = read_history_events(task_id)

    return {
        "task_id": task_id,
        "status": record["status"],
        "events": events,
        "markdown": markdown,
    }


@router.post("/delegations/{task_id}/stop")
async def stop_delegation_route(
    task_id: str, user: WebUser = Depends(get_current_user)
):
    """Cancel a running delegation (status -> stopped).

    Gated on the record rather than the live object so stopping something this
    process no longer holds answers ``stopped: false`` — the honest outcome —
    instead of a 404 that reads like the task never existed.
    """
    from condor.agents.delegate import stop_delegation

    _visible_record(task_id, user)
    stopped = await stop_delegation(task_id)
    return {"stopped": stopped}


@router.get("/{slug}", response_model=AgentDetail)
async def get_agent(slug: str, user: WebUser = Depends(get_current_user)):
    """Get Agent detail + its strategies."""
    agent = _get_agent(slug)
    strategies = _strategy_store().list(slug)
    summaries = await asyncio.gather(
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
        when_to_consult=agent.consult_hint,
        server_required=agent.server_required,
        server_name=agent.server_name,
        strategies=strat_summaries,
    )


@router.post("", response_model=AgentSummary)
async def create_agent(
    req: CreateAgentRequest, user: WebUser = Depends(get_current_user)
):
    """Create a new Agent (identity + brain; strategies are added separately)."""
    from condor.preferences import get_active_agent_key

    # Same rule as the Telegram/MCP path: an unspecified model inherits the
    # creator's active one rather than defaulting to a guess.
    agent = _agent_store().create(
        name=req.name,
        description=req.description,
        instructions=req.instructions,
        agent_key=req.agent_key or get_active_agent_key(user.id) or "",
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
        when_to_consult=agent.consult_hint,
        agent_key=agent.agent_key,
    )


@router.put("/{slug}")
async def update_agent_md(
    slug: str, req: UpdateAgentMdRequest, user: WebUser = Depends(get_current_user)
):
    """Update AGENT.md content."""
    agent = _get_agent(slug)
    atomic_write_text(agent.agent_dir / "AGENT.md", req.content)
    return {"updated": True}


@router.patch("/{slug}/config")
async def update_agent_config(
    slug: str, req: AgentConfigRequest, user: WebUser = Depends(get_current_user)
):
    """Set the Agent's server pin or model without hand-editing front matter.

    ``AgentStore.update`` re-renders the whole front matter, so this is the same
    write the MCP ``manage_trading_agent`` tool already performs — the web layer
    simply had no door to it, which is why the UI could only offer a text editor.
    """
    from condor.llm.options import AGENT_OPTIONS
    from config_manager import get_config_manager

    agent = _get_agent(slug)

    # A pin decides which account the Agent's tools trade on, so it is gated
    # like every other server-scoped write. An empty string clears the pin and
    # needs no access at all.
    if req.server_name:
        check_server_access(user.id, req.server_name)

    # Picker sentinels ("openrouter:", "custom:") are drill-downs that open a
    # model list, not startable models: stored here they would fail at every
    # session start, in every mode the Agent runs in.
    if req.agent_key and AGENT_OPTIONS.get(req.agent_key, {}).get("picker"):
        raise HTTPException(
            status_code=400, detail=f"'{req.agent_key}' is not a model, but a picker"
        )

    if req.server_name is not None:
        agent.server_name = req.server_name
    if req.server_required is not None:
        agent.server_required = req.server_required
    if req.agent_key is not None:
        agent.agent_key = req.agent_key
    _agent_store().update(agent)
    return {
        "updated": True,
        "server_name": agent.server_name,
        "server_required": agent.server_required,
        "agent_key": agent.agent_key,
    }


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


def _chat_member_status(member: Any) -> str | None:
    """Normalize the two shapes ``get_chat_member`` can answer with.

    A live python-telegram-bot returns a ``ChatMember`` with a ``status``
    attribute; ``_HttpBot`` hands back Telegram's raw envelope (or ``None``
    when it has no token). ``None`` means "could not verify".
    """
    if member is None:
        return None
    if isinstance(member, dict):
        if not member.get("ok"):
            return None
        return (member.get("result") or {}).get("status")
    return getattr(member, "status", None)


async def _check_chat_access(user_id: int, chat_id: int) -> None:
    """403 unless ``chat_id`` is a chat the caller actually belongs to (SEC-198).

    The routes below forward a body-supplied ``chat_id`` to outbound Telegram
    sends, so an unchecked value lets any authenticated session speak with the
    bot's identity into anyone's chat. A private chat's id *is* the Telegram
    user id, so the common case (and the MCP crossback for private sessions)
    costs nothing; any other id must be a group the caller is a member of,
    verified against Telegram itself through the same bot ladder that would
    deliver the message. Verification failure fails closed: an unverifiable
    target is a refused target. Admins are exempt, mirroring the delegation
    ownership gate (SEC-081).
    """
    if not chat_id or chat_id == user_id:
        return
    from config_manager import get_config_manager

    if get_config_manager().is_admin(user_id):
        return
    from condor.agents.delegate import resolve_bot

    status = None
    try:
        member = await resolve_bot().get_chat_member(chat_id=chat_id, user_id=user_id)
        status = _chat_member_status(member)
    except Exception:
        log.warning(
            "Could not verify membership of user %s in chat %s", user_id, chat_id
        )
    if status is None or status in ("left", "kicked", "banned"):
        raise HTTPException(
            status_code=403, detail="chat_id is not a chat you belong to"
        )


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
    if req.server_name:
        check_server_access(user.id, req.server_name)

    # The chat is where the consult's notifications land — same ownership rule
    # as the push target on /notify (SEC-198).
    await _check_chat_access(user.id, req.chat_id)

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


async def _conversation_for_session(session_key: str) -> str:
    """Resolve a session key to the conversation currently on that session.

    The resolution itself lives in ``condor.runtime.client`` — routine runs need
    the same answer (ARCH-089) and a second copy could drift from this one. Kept
    as a thin local name because the runtime import stays lazy here, as it does
    for the rest of this module's runtime touchpoints.
    """
    from condor.runtime import client

    return await client.conversation_for_session(session_key)


@router.post("/{slug}/delegate")
async def delegate_agent(
    slug: str, req: DelegateRequest, user: WebUser = Depends(get_current_user)
):
    """Delegate a one-off task to a detached background Agent instance.

    Returns immediately with a ``task_id``; the agent runs unattended (ACP
    auto-approve) until done, then notifies the user. The async sibling of
    ``/consult``.
    """
    from condor.agents.delegate import ON_COMPLETE_CHOICES, start_delegation
    from condor.runtime import wake
    from config_manager import get_config_manager

    _get_agent(slug)
    if not req.task:
        raise HTTPException(status_code=400, detail="task is required")
    if req.on_complete not in ON_COMPLETE_CHOICES:
        raise HTTPException(
            status_code=400,
            detail=f"on_complete must be one of {list(ON_COMPLETE_CHOICES)}",
        )

    # Same server-scope gate as consult: a delegate binds the agent's MCP toolset
    # to ``server_name``'s live credentials, so refuse a server the caller can't access.
    if req.server_name:
        check_server_access(user.id, req.server_name)

    # ``chat_id`` is where ``_notify_done`` will push the completion text, so a
    # foreign chat here would let the delegation's summary (driven by the
    # caller's task text) land in someone else's chat (SEC-198).
    await _check_chat_access(user.id, req.chat_id)

    conversation_id = await _conversation_for_session(req.session_key)

    # Depth 1, structurally. A delegate worker cannot delegate at all
    # (FEAT-032), and a delegation started from *inside* a wake turn is forced
    # back to "notify" here -- otherwise a chain of resumes could keep waking
    # itself. Between the two the recursion is bounded with no counter, no TTL
    # and no rate limiter.
    on_complete = req.on_complete
    if on_complete == "resume" and wake.is_waking(conversation_id):
        log.info(
            "Forcing on_complete=notify: conversation %s is already mid-wake",
            conversation_id,
        )
        on_complete = "notify"

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
        conversation_id=conversation_id,
        session_key=req.session_key,
        on_complete=on_complete,
    )
    return {"task_id": dt.task_id, "status": dt.status}


def _delivered(result: Any) -> bool:
    """Did a ``send_message`` on the resolved bot actually deliver?

    The ladder returns two different things: a live python-telegram-bot raises
    on failure and returns a ``Message``, while ``_HttpBot`` never raises and
    hands back Telegram's raw envelope (or ``None`` when it has no token). Only
    the envelope can say "no" quietly, so that is the case worth reading.
    """
    if result is None:
        return False
    if isinstance(result, dict):
        return bool(result.get("ok"))
    return True


async def _push_to_telegram(chat_id: int, text: str, parse_mode: str) -> bool:
    """Push a notification through the shared outbound-bot ladder.

    Retries once without ``parse_mode`` because the overwhelmingly common
    failure is an unescaped ``_`` or ``*`` in text a model wrote: the user must
    get the message, ugly, rather than not get it at all.
    """
    from condor.agents.delegate import resolve_bot

    bot = resolve_bot()
    if parse_mode:
        try:
            if _delivered(
                await bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=parse_mode
                )
            ):
                return True
        except Exception:
            log.debug("Notification rejected with parse_mode=%s", parse_mode)
    try:
        return _delivered(await bot.send_message(chat_id=chat_id, text=text))
    except Exception:
        log.warning("Could not deliver notification to chat %s", chat_id)
        return False


@router.post("/notify")
async def notify_user(req: NotifyRequest, user: WebUser = Depends(get_current_user)):
    """Announce something to the user, in the conversation *and* on Telegram.

    The MCP ``send_notification`` tool used to POST straight to Telegram, which
    made it the one user-visible tool that never crossed back into the main
    process — so it could not know where its caller lived, and the conversation
    that announced something kept no trace of it (ARCH-088). Crossing back also
    gets it the same bot ladder every other outbound message uses.

    The transcript note is the ``system`` turn ``_record_completion_turn``
    writes for a finished delegation, with ``kind="notification"``; a missing or
    dead ``session_key`` simply means there is no conversation behind this call,
    which is the truth for a routine- or tick-started agent.
    """
    if not req.text:
        raise HTTPException(status_code=400, detail="text is required")

    # The push target must belong to the caller — mirror the ``req.user_id``
    # rule below for the outbound address, and refuse before any side effect
    # (SEC-198).
    await _check_chat_access(user.id, req.chat_id)

    # The caller is the JWT, never ``req.user_id``: mirror consult/delegate so an
    # authenticated session cannot write into another user's transcript.
    conversation_id = await _conversation_for_session(req.session_key)
    recorded = False
    if conversation_id:
        try:
            from condor.runtime.conversations import record_system

            record_system(user.id, conversation_id, req.text, kind="notification")
            recorded = True
        except Exception:
            log.debug(
                "Could not note a notification in conversation %s",
                conversation_id,
                exc_info=True,
            )

    # The dashboard bell (FEAT-048), addressed to the caller themselves — never
    # to ``req.chat_id``, which may legitimately be a group they belong to but
    # which has no dashboard owner. This is what makes ``send_notification``
    # succeed on an install with no Telegram: the tool already counts
    # ``recorded`` as delivered.
    try:
        from condor.notifications import record

        if await record(user.id, req.text, kind="agent"):
            recorded = True
    except Exception:
        log.debug("Could not record a notification for user %s", user.id, exc_info=True)

    sent = False
    if req.chat_id:
        sent = await _push_to_telegram(req.chat_id, req.text, req.parse_mode)
    return {"sent": sent, "recorded": recorded}


# ── Strategy CRUD ──


@router.get("/{slug}/strategies", response_model=list[StrategySummary])
async def list_strategies(slug: str, user: WebUser = Depends(get_current_user)):
    """List strategies owned by an Agent with status/perf."""
    _get_agent(slug)
    strategies = _strategy_store().list(slug)
    summaries = await asyncio.gather(
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
        atomic_write_text(
            learnings_path,
            "# Learnings\n\n## Active Insights\n\n## Retired Insights\n",
        )

    return StrategySummary(
        slug=strategy.slug,
        name=strategy.name,
        description=strategy.description,
        status="idle",
    )


@router.post("/{slug}/strategies/default", response_model=StrategySummary)
async def create_default_strategy(slug: str, user: WebUser = Depends(get_current_user)):
    """Materialize this Agent's default playbook so it can be tuned and looped.

    Every Agent is loopable — this just brings the implicit default playbook on
    disk where the normal strategy UI (config, start, sessions) can reach it.
    Idempotent: returns the existing one when it is already there.
    """
    _get_agent(slug)
    strategy = _strategy_store().ensure_default(slug)
    if strategy is None:
        raise HTTPException(
            status_code=500, detail=f"Could not create a default loop for '{slug}'"
        )
    return await _build_strategy_summary(strategy)


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
    atomic_write_text(strategy.dir / "strategy.md", req.content)
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
    from condor.agents.performance import (
        fetch_agent_performance,
        fetch_agent_pnl_series,
    )

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
            "pnl_series": [],
        }
    # Bot-mode: the session operates named bots whose executors live in the bot
    # container, not the agent_id-keyed table. Merge the live positions of every
    # base this session CURRENTLY owns — the same last-owner-by-`since` rule
    # apply_bot_mode_pnl uses, so the two views never disagree. A session that
    # handed its bot over shows only its own direct executors; the live open book
    # belongs to whoever operates the bot now.
    session_nums = [
        n
        for _, n, k in enumerate_agent_ids(_runkey(slug, sslug), strategy.dir)
        if k == "session"
    ]
    bot_names = current_owner_bases(
        strategy.dir, strategy.default_config, session_nums, session_num
    )
    # Slice the bot to this session's window for the same reason the rollup does:
    # merging the lifetime aggregate here made the session detail disagree with
    # the session's own row in the strategy list.
    owned = session_ownership(strategy.dir, strategy.default_config, session_num)
    since = min((b.since for b in owned if b.since > 0), default=0.0)
    perf = await fetch_agent_performance(
        client, agent_id, bot_names=bot_names, since=since
    )
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
        bot_names=perf.bot_names,
        bot_instances=perf.bot_instances,
        unresolved_bases=perf.unresolved_bases,
        controllers=perf.controllers,
        # Base-lifetime, not window-sliced: the payload counts closes per
        # controller with no timestamp to slice on. Equal to the session's own
        # closes whenever the session deployed the bases it owns (the normal
        # case); a superset when it adopted a base another session had traded.
        # The UI labels it as the bots' breakdown for exactly that reason.
        close_type_counts=perf.close_type_counts,
        fees_known=perf.fees_known,
    )
    # The equity curve, sliced from the same ownership window as the figures
    # above. The journal's per-tick snapshots are only what the aggregator
    # believed at the time, so a session that ran while it was blind to its bots
    # has a permanently flat record; this is derived and therefore self-correcting.
    # A bot released mid-window stops the curve where the session stopped owning.
    released = max((b.until for b in owned if b.until > 0), default=0.0)
    try:
        pnl_series = await fetch_agent_pnl_series(
            client, bot_names or [b.base for b in owned], since, until=released
        )
    except Exception as e:
        log.warning("pnl series for %s failed: %s", agent_id, e)
        pnl_series = []
    return {
        "executors": perf.executors,
        "performance": model.model_dump(),
        "pnl_series": pnl_series,
    }


# ── Strategy lifecycle ──


@router.post("/{slug}/start")
async def start_agent_loop(
    slug: str,
    req: StartStrategyRequest,
    user: WebUser = Depends(get_current_user),
):
    """Start this Agent's loop without naming a strategy.

    Every Agent is loopable: one that owns a single playbook runs it, and one
    that owns none runs the default playbook built from its identity (created
    here on first start). Naming a strategy explicitly is the ``/{slug}/
    strategies/{sslug}/start`` route.
    """
    agent = _get_agent(slug)
    strategy = _strategy_store().resolve_for_loop(slug)
    if strategy is None:
        raise HTTPException(
            status_code=500, detail=f"Could not resolve a loop for agent '{slug}'"
        )
    return await _start(agent, strategy, req, user.id)


@router.post("/{slug}/strategies/{sslug}/start")
async def start_strategy(
    slug: str,
    sslug: str,
    req: StartStrategyRequest,
    user: WebUser = Depends(get_current_user),
):
    """Start a strategy (creates a new session under its Agent)."""
    return await _start(_get_agent(slug), _get_strategy(slug, sslug), req, user.id)


async def _start(agent, strategy, req: StartStrategyRequest, user_id: int) -> dict:
    """Spawn a TickEngine session for ``strategy`` under ``agent``."""
    from condor.agents.config import load_full_config
    from condor.agents.engine import TickEngine
    from config_manager import get_config_manager

    config_dict = load_full_config(strategy.dir, strategy.default_config)
    if req.config:
        config_dict.update(req.config)

    # The engine notifies ``chat_id`` on every tick, so the same ownership rule
    # as /notify applies to it (SEC-198).
    await _check_chat_access(user_id, req.chat_id)

    # ``TickEngine._resolve_server`` trades on ``config["server_name"]`` and the
    # request body is a free-form dict, so without this gate any authenticated
    # user could start a live loop on another user's stored credentials — the
    # same check the config pin and consult/delegate already apply. A name the
    # body asked for is held to it strictly; an inherited one (strategy default,
    # or the "local" that AgentConfig fills in) only matters when it resolves to
    # a real server, since otherwise the engine falls through to the caller's
    # own accessible servers, which is scoped already.
    cm = get_config_manager()
    server_name = config_dict.get("server_name")
    asked_for_it = bool(req.config and req.config.get("server_name"))
    if server_name and (asked_for_it or cm.get_server(server_name)):
        check_server_access(user_id, server_name)
        # A name the body asked for must also name something. An unknown one
        # used to be waved through on the reasoning that it borrows no
        # credentials, but ``has_server_access`` answers True for an admin on
        # any string, and the loop it starts outlives the check: the engine
        # would bind to whatever gets created under that name later (SEC-164).
        # Refused only *after* the access check, so a caller with no access
        # still gets the same "No access" a real server gives them and this
        # route never reveals which names exist.
        if not cm.get_server(server_name):
            raise HTTPException(status_code=404, detail="Server not found")

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
        user_id=user_id,
    )
    await new_engine.start()
    return {
        "started": True,
        "strategy": strategy.slug,
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
    atomic_write_text(strategy.dir / "learnings.md", req.content)
    return {"updated": True}


# ── Runtime state ──
#
# The scratch KV a loop uses for cursors and cooldowns. Distinct from memory
# (durable, curated, the agent's reasoning) and from the journal (append-only
# narrative). See condor/runtime/state.py.


@router.get("/{slug}/strategies/{sslug}/state")
async def get_strategy_state(
    slug: str, sslug: str, user: WebUser = Depends(get_current_user)
):
    """Every live key in this strategy's namespace."""
    from condor.runtime.state import list_state, namespace_for_session

    _get_strategy(slug, sslug)  # 404s if it does not exist
    return {"state": list_state(namespace_for_session(f"{slug}.{sslug}"))}


@router.post("/{slug}/strategies/{sslug}/state")
async def set_strategy_state(
    slug: str,
    sslug: str,
    req: SetStateRequest,
    user: WebUser = Depends(get_current_user),
):
    """Set or clear one key. The namespace is derived, never caller-supplied."""
    from condor.runtime.state import clear_state, namespace_for_session, set_state

    _get_strategy(slug, sslug)
    namespace = namespace_for_session(f"{slug}.{sslug}")

    if req.clear:
        return {"cleared": clear_state(namespace, req.key)}
    try:
        set_state(namespace, req.key, req.value, expires_in=req.expires_in)
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


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


@router.get("/{slug}/strategies/{sslug}/sessions/{session_num}/canvas")
async def get_session_canvas(
    slug: str,
    sslug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    """The agent's own thesis for this session, plus how it changed.

    The canvas is the only artifact in a session that says *why* — the numbers
    say what happened, the snapshots say what was called, and neither says what
    the agent believed. It was written on every tick and read by nothing outside
    the live report.

    ``sections`` is the current text keyed by section; ``revisions`` is every
    edit newest first, so a thesis can be read against the tick that changed it.
    """
    from condor.agents import canvas as canvas_mod

    strategy = _get_strategy(slug, sslug)
    session_dir = find_session_dir(strategy.dir, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")
    return {
        "sections": canvas_mod.read_sections(session_dir),
        "section_titles": canvas_mod.SECTION_TITLES,
        "section_order": list(canvas_mod.CANVAS_SECTIONS),
        "last_revised_tick": canvas_mod.last_revised_tick(session_dir),
        "revisions": canvas_mod.recent_revisions(session_dir, limit=50),
    }


@router.get("/{slug}/strategies/{sslug}/sessions/{session_num}/report")
async def get_session_report(
    slug: str,
    sslug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    """The live report ``SessionReport`` keeps for this session, if there is one.

    Matched on the ``{run_key}/session_{N}`` source name the report is saved
    under, which is the only handle tying a report to the session that produced
    it. Returns ``{"report": null}`` rather than 404 for a session whose loop
    predates the live report or never ticked — a missing report is a normal
    state, not an error the caller should have to distinguish.
    """
    _get_strategy(slug, sslug)
    from condor.reports import list_reports

    run_key = _runkey(slug, sslug)
    source = f"{run_key}/session_{session_num}"
    reports, _total = list_reports(source_type="routine", search=run_key, limit=100)
    matched = [r for r in reports if r.get("source_name", "") == source]
    return {"report": ReportSummary(**matched[0]).model_dump() if matched else None}


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

    snapshots = list_session_snapshots(session_dir)
    return {"snapshots": [SnapshotSummary(**s).model_dump() for s in snapshots]}


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

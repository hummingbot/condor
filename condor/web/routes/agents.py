"""Trading Agents API routes.

An **Agent** is the top-level unit: identity + shared brain (memory/skills) that
``condor`` can *consult*, plus ALL of its operational history (refactor-01b) —
sessions (tick loops), delegations, consults, learnings, and experiments all
live at ``agents/{slug}/``. Strategies are pure playbook templates the
agent selects at start time; which playbook a session ran is session metadata,
so per-playbook views are a filter, not a separate tree. Route shape::

    /agents                          -> list Agents (rollups + playbooks)
    /agents/{slug}                   -> Agent detail (sessions/experiments/learnings)
    /agents/{slug}/consult|delegate  -> run the Agent's brain
    /agents/{slug}/strategies...     -> playbook CRUD ({sslug}.md + default_config)
    /agents/{slug}/start|stop|...    -> session lifecycle
    /agents/{slug}/sessions/...      -> journals, snapshots, executors
    /agents/{slug}/delegation-files  -> flat delegation transcripts
    /agents/{slug}/performance       -> per-session perf + rollup (?strategy= filter)
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.agents.journal import find_delegation_file, read_session_meta
from condor.agents.sessions_index import (
    count_experiments,
    count_sessions,
    enumerate_run_ids,
    find_experiment_file,
    find_session_dir,
    infer_latest_session_status,
    list_delegations_on_disk,
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


# ── Request/Response Models ──


class RunningInstance(BaseModel):
    agent_id: str
    session_num: int
    status: str
    strategy: str = ""
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


class PlaybookSummary(BaseModel):
    """A strategy as what it now is — a playbook template, not a state owner."""

    slug: str
    name: str
    description: str
    agent_key: str | None = None
    default_config: dict[str, Any] = {}
    session_count: int = 0  # tick sessions tagged with this strategy


class AgentSummary(BaseModel):
    slug: str
    name: str
    description: str
    consultable: bool = False
    when_to_consult: str = ""
    agent_key: str = ""
    strategy_count: int = 0
    strategies: list[PlaybookSummary] = []
    # Agent-level rollups (all history lives at the agent now).
    status: str = "idle"  # "running" if any session is running
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
    strategy: str = ""
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
    strategy: str = ""
    status: str = ""
    snapshot_count: int = 0
    created_at: str = ""
    ended_at: str = ""
    has_journal: bool = False


class DelegationInfo(BaseModel):
    """One flat delegation transcript (delegations/{date}-dN.md)."""

    number: int
    task_id: str = ""
    status: str = ""
    task: str = ""
    created_at: str = ""
    ended_at: str = ""
    file: str = ""


class ExperimentInfo(BaseModel):
    number: int
    execution_mode: str = ""  # experiment | run_once | loop
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
    risk_limits: dict[str, Any] = {}
    strategies: list[PlaybookSummary] = []
    learnings: str = ""
    status: str = "idle"
    agent_id: str = ""
    sessions: list[SessionInfo] = []
    experiments: list[ExperimentInfo] = []
    delegations: list[DelegationInfo] = []
    instances: list[RunningInstance] = []


class PlaybookDetail(BaseModel):
    slug: str
    agent_slug: str
    name: str
    description: str
    strategy_md: str
    default_config: dict[str, Any] = {}
    default_trading_context: str = ""
    agent_key: str | None = None
    session_count: int = 0


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
    risk_limits: dict[str, Any] = {}


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
    # Telegram chat that approves mutating tool calls. 0 (web default) means
    # NO human gate is reachable: the consult still runs, but mutations are
    # denied fail-closed (policies.deny_gate) rather than silently allowed.
    chat_id: int = 0
    user_id: int | None = None
    server_name: str | None = None


class StartAgentRequest(BaseModel):
    strategy: str = ""  # playbook slug; optional when the agent has exactly one
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
    # Per-delegation risk caps override — REPLACES the agent's AGENT.md baseline
    # for this one run (trading agents only).
    risk_limits: dict[str, Any] | None = None


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


def _get_engines_for(agent_slug: str) -> list:
    """All engines (running or paused) for a given agent."""
    from condor.agents.engine import get_all_engines

    return [e for e in get_all_engines().values() if e.agent.slug == agent_slug]


# ── Disk lookups ──
# Enumeration/counting of sessions & experiments on disk lives in
# condor.agents.sessions_index (imported at the top), next to the journal
# code that owns the layout. This module keeps only HTTP concerns.


async def _get_client_for_agent(agent, strategies: list):
    """Resolve a Hummingbot API client for an agent's history views.

    Server resolution: the agent's pinned server, else the newest tick
    session's frozen config.yml, else the first strategy default_config that
    declares one.
    """
    from condor.agents.config import load_agent_config
    from config_manager import get_config_manager

    server_name = agent.server_name or ""
    if not server_name:
        agent_dir = agent.agent_dir
        sessions_dir = agent_dir / "sessions"
        if sessions_dir.exists():
            session_dirs = sorted(
                (
                    d
                    for d in sessions_dir.iterdir()
                    if d.is_dir() and (d / "config.yml").exists()
                ),
                key=lambda d: d.stat().st_mtime,
                reverse=True,
            )
            for d in session_dirs:
                try:
                    server_name = load_agent_config(d).server_name or ""
                except Exception:
                    server_name = ""
                if server_name:
                    break
    if not server_name:
        for s in strategies:
            server_name = (s.default_config or {}).get("server_name") or ""
            if server_name:
                break
    if not server_name:
        return None, ""
    cm = get_config_manager()
    try:
        client = await cm.get_client(server_name)
    except Exception as e:
        log.warning("get_client(%s) failed: %s", server_name, e)
        return None, server_name
    return client, server_name


def _session_bot_name(agent_dir: Path, num: int) -> str:
    """Controller-mode attribution: a session's frozen config.yml bot_name."""
    import yaml

    cfg_path = agent_dir / "sessions" / f"session_{num}" / "config.yml"
    if not cfg_path.exists():
        return ""
    try:
        return (yaml.safe_load(cfg_path.read_text()) or {}).get("bot_name", "") or ""
    except Exception:
        return ""


async def _compute_agent_performance(agent, strategies: list):
    """Return list of AgentPerformanceModel plus rolled-up totals.

    Covers every attributed run of the agent (tick sessions + experiments,
    delegations/consults excluded — they never tag ``controller_id``). The
    assembled rollup is cached ~30s (``_PERF_CACHE``); underneath, closed
    sessions/experiments are served from ``_CLOSED_PERF_CACHE`` so only active
    ids hit the backend after the TTL expires.
    """
    from condor.agents.performance import fetch_agent_performance_batch

    slug = agent.slug
    agent_dir = agent.agent_dir

    cached = _cache_get(f"perf:{slug}")
    if cached is not None:
        return cached

    runs = enumerate_run_ids(slug, agent_dir)
    client, _server = await _get_client_for_agent(agent, strategies)

    # Controller mode: a session with a bot_name in its frozen config attributes
    # that bot's PnL to it. Executor search uses the run's controller_id (the
    # legacy composite tag for migrated sessions, the agent_id otherwise).
    bot_names: dict[str, str] = {}
    for r in runs:
        if r["kind"] == "session":
            bn = _session_bot_name(agent_dir, r["num"])
            if bn:
                bot_names[r["controller_id"]] = bn

    sessions: list[AgentPerformanceModel] = []
    if client and runs:
        from condor.agents.engine import get_all_engines

        # Split ids by state: closed sessions/experiments are immutable, so only
        # ids with a live engine (running/paused, incl. experiments) plus the
        # newest session — whose executors may still be closing out — are
        # re-fetched; everything else is served from the long-lived frozen cache.
        engine_ids = {e.agent_id for e in get_all_engines().values()}
        latest_session = max(
            (r["num"] for r in runs if r["kind"] == "session"), default=None
        )
        active_ids = {
            r["agent_id"]
            for r in runs
            if r["agent_id"] in engine_ids
            or (r["kind"] == "session" and r["num"] == latest_session)
        }
        # An id active again (e.g. restored engine) must not serve a stale
        # frozen value once it goes idle — evict so it gets one final fetch.
        for aid in active_ids:
            _CLOSED_PERF_CACHE.pop(aid, None)

        by_controller = {r["controller_id"]: r for r in runs}
        if bot_names:
            # Controller mode attributes the bot's live aggregate to every
            # session, so no per-session result is immutable — fetch all.
            fetch_ids = [r["controller_id"] for r in runs]
        else:
            fetch_ids = [
                r["controller_id"]
                for r in runs
                if r["agent_id"] in active_ids
                or r["agent_id"] not in _CLOSED_PERF_CACHE
            ]

        perf_map: dict[str, Any] = {}
        failed_ids: set[str] = set()
        if fetch_ids:
            try:
                perf_map = await fetch_agent_performance_batch(
                    client, fetch_ids, bot_names or None, failed_ids=failed_ids
                )
            except Exception as e:
                log.warning("fetch_agent_performance_batch(%s) failed: %s", slug, e)
                perf_map = {}
                failed_ids = set(fetch_ids)

        for r in runs:
            agent_id, controller_id = r["agent_id"], r["controller_id"]
            perf = perf_map.get(controller_id)
            if perf is None:
                perf = _CLOSED_PERF_CACHE.get(agent_id)
            if perf is None:
                continue
            # Freeze immutable results: fetched fine, no engine, not the newest
            # session, and nothing still open whose unrealized PnL could move.
            if (
                not bot_names
                and controller_id in perf_map
                and agent_id not in active_ids
                and agent_id not in failed_ids
                and perf.open_count == 0
            ):
                _CLOSED_PERF_CACHE[agent_id] = perf
            if r["kind"] == "experiment" and perf.trade_count == 0:
                continue
            sessions.append(
                AgentPerformanceModel(
                    agent_id=agent_id,
                    session_num=r["num"],
                    kind=r["kind"],
                    strategy=r.get("strategy", ""),
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
    _cache_set(f"perf:{slug}", result)
    return result


def _instance_from_engine(engine, perf_by_id: dict) -> RunningInstance:
    info = engine.get_info()
    p = perf_by_id.get(info["agent_id"])
    return RunningInstance(
        agent_id=info["agent_id"],
        session_num=info["session_num"],
        status=info["status"],
        strategy=info.get("strategy_slug", ""),
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


def _playbook_summary(strategy, agent_dir: Path) -> PlaybookSummary:
    return PlaybookSummary(
        slug=strategy.slug,
        name=strategy.name,
        description=strategy.description,
        agent_key=strategy.agent_key,
        default_config=strategy.default_config or {},
        session_count=len(list_sessions(agent_dir, strategy=strategy.slug)),
    )


async def _build_agent_summary(agent, strategies: list) -> AgentSummary:
    """Roll up disk + engine + performance state for one agent."""
    agent_dir = agent.agent_dir

    try:
        sessions_perf, totals = await _compute_agent_performance(agent, strategies)
    except Exception as e:
        log.warning("compute_agent_performance(%s) failed: %s", agent.slug, e)
        sessions_perf, totals = [], {}
    perf_by_id = {p.agent_id: p for p in sessions_perf}

    engines = _get_engines_for(agent.slug)
    status = "idle"
    tick_count = 0
    instances: list[RunningInstance] = []
    for engine in engines:
        inst = _instance_from_engine(engine, perf_by_id)
        instances.append(inst)
        if status == "idle":
            status = inst.status
            tick_count = inst.tick_count

    if not engines:
        disk_info = infer_latest_session_status(agent_dir, agent.slug)
        if disk_info:
            status = disk_info["status"]
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

    return AgentSummary(
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        consultable=agent.consultable,
        when_to_consult=agent.when_to_consult,
        agent_key=agent.agent_key,
        strategy_count=len(strategies),
        strategies=[_playbook_summary(s, agent_dir) for s in strategies],
        status=status,
        session_count=count_sessions(agent_dir),
        experiment_count=count_experiments(agent_dir),
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
    """List all Agents with their playbooks, history rollups, and status."""
    import asyncio as _asyncio

    agents = _agent_store().list_all()
    store = _strategy_store()

    summaries = await _asyncio.gather(
        *[_build_agent_summary(a, store.list(a.slug)) for a in agents],
        return_exceptions=True,
    )
    return [s for s in summaries if isinstance(s, AgentSummary)]


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
    """Get a delegation's status + result/error.

    Live tasks come from the in-process registry; after a restart the flat
    transcript file (``agents/{slug}/delegations/{date}-dN.md``) still
    resolves, so a task_id never goes dark just because the process died.
    """
    import re as _re

    from condor.agents.delegate import get_delegation

    dt = get_delegation(task_id)
    if dt is not None:
        return dt.to_dict()

    m = _re.match(r"^(?P<slug>.+)-d(?P<num>\d+)$", task_id)
    if m:
        try:
            agent = _get_agent(m.group("slug"))
        except HTTPException:
            agent = None
        if agent is not None:
            path = find_delegation_file(agent.agent_dir, int(m.group("num")))
            if path:
                return {"task_id": task_id, "transcript": path.read_text()}
    raise HTTPException(status_code=404, detail=f"Delegation '{task_id}' not found")


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
    """Get Agent detail: identity, playbooks, and the full history envelope."""
    agent = _get_agent(slug)
    agent_dir = agent.agent_dir
    strategies = _strategy_store().list(slug)

    try:
        sessions_perf, _totals = await _compute_agent_performance(agent, strategies)
    except Exception as e:
        log.warning("compute_agent_performance(%s) failed: %s", slug, e)
        sessions_perf = []
    perf_by_id = {p.agent_id: p for p in sessions_perf}

    engines = _get_engines_for(slug)
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
        disk_info = infer_latest_session_status(agent_dir, slug)
        if disk_info:
            status = disk_info["status"]
            agent_id = disk_info["agent_id"]

    learnings_path = agent_dir / "learnings.md"
    learnings = learnings_path.read_text() if learnings_path.exists() else ""

    return AgentDetail(
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        agent_md=(
            (agent_dir / "AGENT.md").read_text()
            if (agent_dir / "AGENT.md").exists()
            else ""
        ),
        agent_key=agent.agent_key,
        tools=agent.tools,
        when_to_consult=agent.when_to_consult,
        consultable=agent.consultable,
        server_required=agent.server_required,
        server_name=agent.server_name,
        risk_limits=agent.risk_limits,
        strategies=[_playbook_summary(s, agent_dir) for s in strategies],
        learnings=learnings,
        status=status,
        agent_id=agent_id,
        sessions=[SessionInfo(**s) for s in list_sessions(agent_dir)],
        experiments=[ExperimentInfo(**e) for e in list_experiments(agent_dir)],
        delegations=[
            DelegationInfo(**d) for d in list_delegations_on_disk(agent_dir)
        ],
        instances=instances,
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
        risk_limits=req.risk_limits,
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
    """Delete an Agent. Refuses while it has a running session."""
    _get_agent(slug)
    running = [e for e in _get_engines_for(slug) if e.is_running]
    if running:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete an Agent with running sessions. Stop them first.",
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

    Returns immediately with a ``task_id``; the agent runs unattended until
    done, then notifies the user. Trading agents run under a zero-seeded risk
    gate (per-call ``risk_limits`` override replaces the AGENT.md baseline);
    serverless agents run with full auto-approve. The async sibling of
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
    try:
        dt = await start_delegation(
            agent_slug=slug,
            user_id=user.id,
            chat_id=req.chat_id,
            server_name=req.server_name,
            task=req.task,
            timeout_s=req.timeout_s,
            risk_limits=req.risk_limits,
        )
    except ValueError as e:
        # Loud policy error: trading delegation with neither an AGENT.md
        # baseline nor a per-call override.
        raise HTTPException(status_code=400, detail=str(e))
    return {"task_id": dt.task_id, "status": dt.status}


# ── Strategy (playbook) CRUD ──


@router.get("/{slug}/strategies", response_model=list[PlaybookSummary])
async def list_strategies(slug: str, user: WebUser = Depends(get_current_user)):
    """List the playbooks owned by an Agent."""
    agent = _get_agent(slug)
    return [
        _playbook_summary(s, agent.agent_dir) for s in _strategy_store().list(slug)
    ]


@router.post("/{slug}/strategies", response_model=PlaybookSummary)
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
    return PlaybookSummary(
        slug=strategy.slug,
        name=strategy.name,
        description=strategy.description,
        agent_key=strategy.agent_key,
        default_config=strategy.default_config or {},
    )


@router.get("/{slug}/strategies/{sslug}", response_model=PlaybookDetail)
async def get_strategy(
    slug: str, sslug: str, user: WebUser = Depends(get_current_user)
):
    """Get playbook detail (the template — history lives on the agent)."""
    agent = _get_agent(slug)
    strategy = _get_strategy(slug, sslug)

    strategy_md = strategy.path.read_text() if strategy.path.exists() else ""

    return PlaybookDetail(
        slug=sslug,
        agent_slug=slug,
        name=strategy.name,
        description=strategy.description,
        strategy_md=strategy_md,
        default_config=strategy.default_config or {},
        default_trading_context=strategy.default_trading_context,
        agent_key=strategy.agent_key,
        session_count=len(list_sessions(agent.agent_dir, strategy=sslug)),
    )


@router.put("/{slug}/strategies/{sslug}")
async def update_strategy_md(
    slug: str,
    sslug: str,
    req: UpdateStrategyMdRequest,
    user: WebUser = Depends(get_current_user),
):
    """Update the playbook's {sslug}.md content."""
    strategy = _get_strategy(slug, sslug)
    strategy.path.write_text(req.content)
    return {"updated": True}


@router.put("/{slug}/strategies/{sslug}/config")
async def update_strategy_config(
    slug: str,
    sslug: str,
    req: UpdateConfigRequest,
    user: WebUser = Depends(get_current_user),
):
    """Update a playbook's default_config (frontmatter — its only state)."""
    strategy = _get_strategy(slug, sslug)
    store = _strategy_store()
    merged = dict(strategy.default_config or {})
    merged.update(req.config)
    strategy.default_config = merged
    store.update(strategy)
    return {"updated": True, "config": merged}


@router.delete("/{slug}/strategies/{sslug}")
async def delete_strategy(
    slug: str, sslug: str, user: WebUser = Depends(get_current_user)
):
    """Delete a playbook. Refuses while a session is running it."""
    _get_strategy(slug, sslug)
    running = [
        e
        for e in _get_engines_for(slug)
        if e.is_running and e.strategy.slug == sslug
    ]
    if running:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a strategy with a running session. Stop it first.",
        )
    _strategy_store().delete(slug, sslug)
    return {"deleted": True}


# ── Performance ──


@router.get("/{slug}/performance", response_model=AgentPerformanceResponse)
async def get_agent_performance(
    slug: str,
    strategy: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Per-session performance + rollup totals for an agent.

    ``?strategy=`` filters to one playbook's sessions — the per-playbook track
    record is a metadata filter, not a separate tree.
    """
    agent = _get_agent(slug)
    strategies = _strategy_store().list(slug)
    sessions, totals = await _compute_agent_performance(agent, strategies)
    if strategy:
        sessions = [
            s for s in sessions if s.strategy == strategy and s.kind == "session"
        ]
        totals = {
            "total_pnl": sum(s.total_pnl for s in sessions),
            "realized_pnl": sum(s.realized_pnl for s in sessions),
            "unrealized_pnl": sum(s.unrealized_pnl for s in sessions),
            "volume": sum(s.volume for s in sessions),
            "fees": sum(s.fees for s in sessions),
            "open_positions": sum(s.open_count for s in sessions),
            "trade_count": float(sum(s.trade_count for s in sessions)),
        }
    running_ids = {e.agent_id for e in _get_engines_for(slug) if e.is_running}
    for s in sessions:
        s.status = "running" if s.agent_id in running_ids else "closed"
    return AgentPerformanceResponse(slug=slug, sessions=sessions, totals=totals)


@router.get("/{slug}/sessions/{session_num}/executors")
async def get_session_executors(
    slug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    """Return executors + performance for a single session."""
    from condor.agents.performance import fetch_agent_performance

    agent = _get_agent(slug)
    agent_id = f"{slug}_{session_num}"
    session_dir = find_session_dir(agent.agent_dir, session_num)
    # Migrated sessions keep their legacy composite controller_id in meta.yml.
    controller_id = agent_id
    if session_dir is not None:
        controller_id = read_session_meta(session_dir).get("controller_id") or agent_id
    strategies = _strategy_store().list(slug)
    client, _server = await _get_client_for_agent(agent, strategies)
    if client is None:
        return {
            "executors": [],
            "performance": AgentPerformanceModel(
                agent_id=agent_id, session_num=session_num
            ).model_dump(),
        }
    bot_name = _session_bot_name(agent.agent_dir, session_num)
    perf = await fetch_agent_performance(client, controller_id, bot_name=bot_name)
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
async def start_session(
    slug: str,
    req: StartAgentRequest,
    user: WebUser = Depends(get_current_user),
):
    """Start a session — the stateful unit of capital engagement — or, with
    ``execution_mode: "experiment"``, one simulated tick that leaves only a
    flat snapshot.

    ``strategy`` selects the playbook — optional when the agent has exactly
    one, required (400 listing the options) when it has several.
    """
    from condor.agents.config import normalize_config
    from condor.agents.engine import TickEngine

    agent = _get_agent(slug)
    strategies = _strategy_store().list(slug)

    if req.strategy:
        strategy = next((s for s in strategies if s.slug == req.strategy), None)
        if strategy is None:
            raise HTTPException(
                status_code=404,
                detail=f"Strategy '{req.strategy}' not found under agent '{slug}'. "
                f"Available: {[s.slug for s in strategies] or 'none'}",
            )
    elif len(strategies) == 1:
        strategy = strategies[0]
    elif not strategies:
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{slug}' has no strategies — create one first.",
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{slug}' has several strategies — pass strategy=<slug>. "
            f"Available: {[s.slug for s in strategies]}",
        )

    # Playbooks are pure templates: launch config = normalized default_config
    # (+ request overrides). No strategy-level config.yml exists anymore.
    # Risk resolution order: request config > strategy default_config > agent
    # baseline (AGENT.md risk_limits) > schema defaults. The baseline must be
    # seeded BEFORE normalize_config fills schema defaults, or the generic
    # 500/5 defaults would mask it.
    defaults = dict(strategy.default_config or {})
    if not defaults.get("risk_limits") and agent.risk_limits:
        defaults["risk_limits"] = dict(agent.risk_limits)
    config_dict = normalize_config(defaults)
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
        "strategy": strategy.slug,
    }


def _select_engines(slug: str, agent_id: str | None, running_only: bool = False):
    if agent_id:
        from condor.agents.engine import get_engine

        engine = get_engine(agent_id)
        if not engine or (running_only and not engine.is_running):
            raise HTTPException(
                status_code=404, detail=f"Agent '{agent_id}' not found"
            )
        return [engine]
    engines = _get_engines_for(slug)
    if running_only:
        engines = [e for e in engines if e.is_running]
    if not engines:
        raise HTTPException(status_code=404, detail="No running session found")
    return engines


@router.post("/{slug}/stop")
async def stop_agent(
    slug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Stop a running session. If agent_id given, stop that instance; else all."""
    for engine in _select_engines(slug, agent_id):
        await engine.stop()
    return {"stopped": True}


@router.post("/{slug}/shutdown")
async def shutdown_agent(
    slug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Emergency shutdown: wind down positions/executors per shutdown.md, then stop.

    Escalation above the plain (position-preserving) ``/stop``. If ``agent_id`` is
    given, only that instance is wound down; otherwise every running session of
    this agent is.
    """
    for engine in _select_engines(slug, agent_id):
        await engine._run_shutdown(reason="manual emergency stop")
    return {"shutdown": True}


@router.post("/{slug}/pause")
async def pause_agent(
    slug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Pause a running session."""
    engines = _select_engines(slug, agent_id, running_only=True)
    engines[0].pause()
    return {"paused": True}


@router.post("/{slug}/resume")
async def resume_agent(
    slug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Resume a paused session."""
    engines = _select_engines(slug, agent_id)
    engines[0].resume()
    return {"resumed": True}


# ── Learnings (agent-level) ──


@router.get("/{slug}/learnings")
async def get_learnings(slug: str, user: WebUser = Depends(get_current_user)):
    """Read the agent's learnings.md (all strategies, all run kinds)."""
    agent = _get_agent(slug)
    learnings_path = agent.agent_dir / "learnings.md"
    content = learnings_path.read_text() if learnings_path.exists() else ""
    return {"content": content}


@router.put("/{slug}/learnings")
async def update_learnings(
    slug: str,
    req: UpdateLearningsRequest,
    user: WebUser = Depends(get_current_user),
):
    """Update the agent's learnings.md."""
    agent = _get_agent(slug)
    (agent.agent_dir / "learnings.md").write_text(req.content)
    return {"updated": True}


# ── Sessions ──


@router.get("/{slug}/sessions")
async def list_agent_sessions(
    slug: str,
    strategy: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """List an agent's sessions, optionally filtered to one playbook."""
    agent = _get_agent(slug)
    sessions = list_sessions(agent.agent_dir, strategy=strategy)
    return {"sessions": [SessionInfo(**s).model_dump() for s in sessions]}


def _get_session_dir_or_404(slug: str, session_num: int) -> Path:
    agent = _get_agent(slug)
    session_dir = find_session_dir(agent.agent_dir, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")
    return session_dir


@router.get("/{slug}/sessions/{session_num}/journal")
async def get_journal(
    slug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    """Read journal.md for a (tick) session."""
    session_dir = _get_session_dir_or_404(slug, session_num)
    journal_path = session_dir / "journal.md"
    content = journal_path.read_text() if journal_path.exists() else ""
    return {"content": content}


@router.get("/{slug}/delegation-files")
async def list_agent_delegations(
    slug: str, user: WebUser = Depends(get_current_user)
):
    """List an agent's delegation transcripts (flat files, newest first)."""
    agent = _get_agent(slug)
    return {
        "delegations": [
            DelegationInfo(**d).model_dump()
            for d in list_delegations_on_disk(agent.agent_dir)
        ]
    }


@router.get("/{slug}/delegation-files/{num}")
async def get_agent_delegation(
    slug: str,
    num: int,
    user: WebUser = Depends(get_current_user),
):
    """Read one delegation transcript."""
    agent = _get_agent(slug)
    path = find_delegation_file(agent.agent_dir, num)
    if not path:
        raise HTTPException(status_code=404, detail=f"Delegation {num} not found")
    return {"content": path.read_text(), "file": path.name}


@router.get("/{slug}/sessions/{session_num}/snapshots")
async def list_snapshots(
    slug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    """List snapshots for a session."""
    session_dir = _get_session_dir_or_404(slug, session_num)

    snapshots = []
    snap_dir = session_dir / "snapshots"
    if snap_dir.exists():
        for f in sorted(
            snap_dir.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True
        ):
            m = re.match(r"snapshot_(\d+)\.md", f.name)
            if m:
                tick = int(m.group(1))
                content = f.read_text()
                ts_match = re.search(r"^# Snapshot #\d+ — (.+)$", content, re.MULTILINE)
                timestamp = ts_match.group(1) if ts_match else ""
                snapshots.append(
                    SnapshotSummary(tick=tick, timestamp=timestamp, file=f.name)
                )

    return {"snapshots": [s.model_dump() for s in snapshots]}


@router.get("/{slug}/sessions/{session_num}/snapshots/{tick}")
async def get_snapshot(
    slug: str,
    session_num: int,
    tick: int,
    user: WebUser = Depends(get_current_user),
):
    """Read a specific snapshot."""
    session_dir = _get_session_dir_or_404(slug, session_num)
    path = session_dir / "snapshots" / f"snapshot_{tick}.md"
    if path.exists():
        return {"content": path.read_text(), "tick": tick}
    raise HTTPException(status_code=404, detail=f"Snapshot {tick} not found")


# ── Experiments ──


@router.get("/{slug}/experiments")
async def list_agent_experiments(
    slug: str, user: WebUser = Depends(get_current_user)
):
    """List an agent's experiments."""
    agent = _get_agent(slug)
    experiments = list_experiments(agent.agent_dir)
    return {"experiments": [ExperimentInfo(**e).model_dump() for e in experiments]}


@router.get("/{slug}/experiments/{exp_num}")
async def get_experiment(
    slug: str, exp_num: int, user: WebUser = Depends(get_current_user)
):
    """Read an experiment snapshot."""
    agent = _get_agent(slug)
    path = find_experiment_file(agent.agent_dir, exp_num)
    if not path:
        raise HTTPException(status_code=404, detail=f"Experiment {exp_num} not found")
    return {"content": path.read_text(), "number": exp_num}


# ── Routines / reports ──


@router.get("/{slug}/routines")
async def get_agent_routines(slug: str, user: WebUser = Depends(get_current_user)):
    """List routines owned by this agent (``agents/{slug}/routines``)."""
    _get_agent(slug)
    from condor.routine_store import get_routine_store

    store = get_routine_store()
    all_routines = store.list_routines()
    prefix = f"{slug}/"
    return [r for r in all_routines if r.get("name", "").startswith(prefix)]


@router.get("/{slug}/reports")
async def get_agent_reports(
    slug: str,
    limit: int = 50,
    user: WebUser = Depends(get_current_user),
):
    """Get reports generated by this agent's routines."""
    _get_agent(slug)
    from condor.reports import list_reports

    prefix = f"{slug}/"
    reports, _total = list_reports(source_type="routine", search=slug, limit=limit)
    matched = [r for r in reports if r.get("source_name", "").startswith(prefix)]
    return {
        "reports": [ReportSummary(**r).model_dump() for r in matched],
        "total": len(matched),
    }

"""Trading Agents API routes — a THIN adapter over :class:`AgentService` (§5.2).

An **Agent** is the top-level unit: identity + strategy body + shared brain
(memory/skills), with ALL of its operational history at ``agents/{slug}/``.
Since the Agent + Strategy collapse (§5.3) the AGENT.md is the one spec —
there is no separate Strategy entity. Route shape::

    /agents                          -> list Agents (rollups)
    /agents/{slug}                   -> Agent detail (sessions/experiments/learnings)
    /agents/{slug}/consult|delegate  -> run the Agent's brain
    /agents/{slug}/start|stop|...    -> session lifecycle
    /agents/{slug}/sessions/...      -> journals, snapshots, executors
    /agents/{slug}/delegation-files  -> flat delegation transcripts
    /agents/{slug}/performance       -> per-session perf + rollup

All CRUD + lifecycle goes through ``AgentService``; the read-only history
endpoints below are dashboard projections over the on-disk journal layout.
Every ``LifecycleError`` maps to ``HTTPException(e.status, e.message)``.
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
from condor.agents.lifecycle import LifecycleError
from condor.agents.service import AgentService
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


def _svc() -> AgentService:
    return AgentService()


def _http(e: LifecycleError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.message)


def _get_agent(slug: str):
    """Resolve an Agent or 404 (LifecycleError -> HTTPException)."""
    try:
        return _svc().get(slug)
    except LifecycleError as e:
        raise _http(e)


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
    consultable: bool = False
    can_trade: bool = False
    when_to_consult: str = ""
    agent_key: str = ""
    denomination: str = ""
    default_config: dict[str, Any] = {}
    schedule: dict[str, Any] = {}
    # Agent-level rollups (all history lives at the agent).
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
    strategy: str = ""  # legacy session meta tag — kept tolerantly for old runs
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
    strategy: str = ""  # legacy meta field — old sessions still carry it
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
    can_trade: bool = False
    server_required: bool = True
    server_name: str = ""
    risk_limits: dict[str, Any] = {}
    denomination: str = ""
    default_config: dict[str, Any] = {}
    default_trading_context: str = ""
    schedule: dict[str, Any] = {}
    learnings: str = ""
    status: str = "idle"
    agent_id: str = ""
    sessions: list[SessionInfo] = []
    experiments: list[ExperimentInfo] = []
    delegations: list[DelegationInfo] = []
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
    risk_limits: dict[str, Any] = {}
    denomination: str = ""
    default_config: dict[str, Any] = {}
    default_trading_context: str = ""
    schedule: dict[str, Any] = {}


class UpdateAgentRequest(BaseModel):
    """Field patch for the agent spec. Only fields explicitly sent are applied.

    ``content`` is the legacy body-update key (the old endpoint wrote raw
    AGENT.md text) — it is treated as ``instructions`` (the body); frontmatter
    fields are patched via their own keys.
    """

    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    agent_key: str | None = None
    tools: list[str] | None = None
    when_to_consult: str | None = None
    server_required: bool | None = None
    server_name: str | None = None
    risk_limits: dict[str, Any] | None = None
    denomination: str | None = None
    default_config: dict[str, Any] | None = None
    default_trading_context: str | None = None
    schedule: dict[str, Any] | None = None
    content: str | None = None  # legacy alias for instructions


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
    # ``strategy`` is accepted but IGNORED (Agent+Strategy collapse §5.3) —
    # the dashboard may still send it briefly.
    strategy: str = ""
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


class DirectiveRequest(BaseModel):
    text: str
    agent_id: str | None = None


# ── Performance projection (dashboard rollups) ──
# Enumeration/counting of sessions & experiments on disk lives in
# condor.agents.sessions_index (imported at the top), next to the journal
# code that owns the layout. This module keeps only HTTP concerns.


async def _get_client_for_agent(agent, strategies: list | None = None):
    """Resolve a Hummingbot API client for an agent's history views.

    Server resolution: the agent's pinned server, else the newest tick
    session's frozen config.yml, else the agent's default_config.
    (``strategies`` is a legacy positional kept for signature compatibility.)
    """
    from condor.agents.config import load_agent_config
    from config_manager import get_config_manager

    server_name = agent.server_name or ""
    if not server_name:
        sessions_dir = agent.agent_dir / "sessions"
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
        server_name = (agent.default_config or {}).get("server_name") or ""
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


async def _compute_agent_performance(agent, strategies: list | None = None):
    """Return list of AgentPerformanceModel plus rolled-up totals.

    Covers every attributed run of the agent (tick sessions + experiments,
    delegations/consults excluded — they never tag ``controller_id``). The
    assembled rollup is cached ~30s (``_PERF_CACHE``); underneath, closed
    sessions/experiments are served from ``_CLOSED_PERF_CACHE`` so only active
    ids hit the backend after the TTL expires. (``strategies`` is a legacy
    positional kept for signature compatibility.)
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


def _instance_from_info(info: dict, perf_by_id: dict) -> RunningInstance:
    """Build a RunningInstance from an engine info dict (svc.list_runs row)."""
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


async def _build_agent_summary(agent) -> AgentSummary:
    """Roll up disk + engine + performance state for one agent."""
    agent_dir = agent.agent_dir

    try:
        sessions_perf, totals = await _compute_agent_performance(agent)
    except Exception as e:
        log.warning("compute_agent_performance(%s) failed: %s", agent.slug, e)
        sessions_perf, totals = [], {}
    perf_by_id = {p.agent_id: p for p in sessions_perf}

    infos = _svc().list_runs(agent.slug)
    status = "idle"
    tick_count = 0
    instances: list[RunningInstance] = []
    for info in infos:
        inst = _instance_from_info(info, perf_by_id)
        instances.append(inst)
        if status == "idle":
            status = inst.status
            tick_count = inst.tick_count

    if not infos:
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
        can_trade=agent.can_trade,
        when_to_consult=agent.when_to_consult,
        agent_key=agent.agent_key,
        denomination=agent.denomination,
        default_config=agent.default_config or {},
        schedule=agent.schedule or {},
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
    """List all Agents with history rollups and live status."""
    import asyncio as _asyncio

    agents = _svc().list()
    summaries = await _asyncio.gather(
        *[_build_agent_summary(a) for a in agents],
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
        agent = _svc().store.get(m.group("slug"))
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
    """Get Agent detail: identity, spec fields, and the full history envelope."""
    agent = _get_agent(slug)
    agent_dir = agent.agent_dir

    try:
        sessions_perf, _totals = await _compute_agent_performance(agent)
    except Exception as e:
        log.warning("compute_agent_performance(%s) failed: %s", slug, e)
        sessions_perf = []
    perf_by_id = {p.agent_id: p for p in sessions_perf}

    infos = _svc().list_runs(slug)
    status = "idle"
    agent_id = ""
    instances = []
    for info in infos:
        inst = _instance_from_info(info, perf_by_id)
        instances.append(inst)
        if not agent_id:
            status = inst.status
            agent_id = inst.agent_id

    if not infos:
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
        agent_md=_svc().store.source_text(slug),
        agent_key=agent.agent_key,
        tools=agent.tools,
        when_to_consult=agent.when_to_consult,
        consultable=agent.consultable,
        can_trade=agent.can_trade,
        server_required=agent.server_required,
        server_name=agent.server_name,
        risk_limits=agent.risk_limits,
        denomination=agent.denomination,
        default_config=agent.default_config or {},
        default_trading_context=agent.default_trading_context,
        schedule=agent.schedule or {},
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
    """Create a new Agent (identity + spec; AGENT.md is the one spec, §5.3)."""
    try:
        agent = _svc().create(
            name=req.name,
            description=req.description,
            instructions=req.instructions,
            agent_key=req.agent_key,
            tools=req.tools,
            when_to_consult=req.when_to_consult,
            server_required=req.server_required,
            server_name=req.server_name,
            risk_limits=req.risk_limits,
            denomination=req.denomination,
            default_config=req.default_config,
            default_trading_context=req.default_trading_context,
            schedule=req.schedule,
        )
    except LifecycleError as e:
        raise _http(e)
    return AgentSummary(
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        consultable=agent.consultable,
        can_trade=agent.can_trade,
        when_to_consult=agent.when_to_consult,
        agent_key=agent.agent_key,
        denomination=agent.denomination,
        default_config=agent.default_config or {},
        schedule=agent.schedule or {},
    )


@router.put("/{slug}")
async def update_agent(
    slug: str, req: UpdateAgentRequest, user: WebUser = Depends(get_current_user)
):
    """Patch the agent spec (AGENT.md body via ``instructions``, and/or fields)."""
    patch = req.model_dump(exclude_unset=True)
    content = patch.pop("content", None)
    if content is not None and "instructions" not in patch:
        patch["instructions"] = content
    if not patch:
        raise HTTPException(status_code=422, detail="no fields to update")
    try:
        _svc().update(slug, patch)
    except LifecycleError as e:
        raise _http(e)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"updated": True}


# Kept for import stability (the PUT handler was historically named this).
update_agent_md = update_agent


@router.delete("/{slug}")
async def delete_agent(slug: str, user: WebUser = Depends(get_current_user)):
    """Tombstone an Agent (§5.2): history preserved, slug reserved.

    Refuses (409) while it has running sessions or nonterminal executors.
    """
    try:
        return _svc().delete(slug)
    except LifecycleError as e:
        raise _http(e)


@router.post("/{slug}/consult")
async def consult_agent(
    slug: str, req: ConsultRequest, user: WebUser = Depends(get_current_user)
):
    """Run an Agent consult (its brain to completion) and return the answer."""
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
    try:
        answer = await _svc().consult(
            slug,
            task=req.task,
            context=req.context,
            user_id=user.id,
            chat_id=req.chat_id,
            server_name=req.server_name,
        )
    except LifecycleError as e:
        raise _http(e)
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
        d = await _svc().delegate(
            slug,
            task=req.task,
            user_id=user.id,
            chat_id=req.chat_id,
            server_name=req.server_name,
            risk_limits=req.risk_limits,
            timeout_s=req.timeout_s,
        )
    except LifecycleError as e:
        raise _http(e)
    except ValueError as e:
        # Loud policy error: trading delegation with neither an AGENT.md
        # baseline nor a per-call override.
        raise HTTPException(status_code=400, detail=str(e))
    return {"task_id": d["task_id"], "status": d["status"]}


# ── Performance ──


@router.get("/{slug}/performance", response_model=AgentPerformanceResponse)
async def get_agent_performance(
    slug: str,
    strategy: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Per-session performance + rollup totals for an agent.

    ``?strategy=`` filters on the legacy session-meta tag (old sessions only).
    """
    agent = _get_agent(slug)
    sessions, totals = await _compute_agent_performance(agent)
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
    running_ids = {
        i["agent_id"] for i in _svc().list_runs(slug) if i.get("status") == "running"
    }
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
    client, _server = await _get_client_for_agent(agent)
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

    The AGENT.md is the one spec (§5.3): launch config merges over its
    ``default_config``; a legacy ``strategy`` field in the request is ignored.
    """
    # Web callers always act as themselves (mirror consult): honoring
    # ``req.user_id`` would let any authenticated session start the engine
    # under another user's memory scope and accessible-servers fallback.
    try:
        return await _svc().run(
            slug,
            config=req.config,
            trading_context=req.trading_context,
            chat_id=req.chat_id,
            user_id=user.id,
        )
    except LifecycleError as e:
        raise _http(e)


async def _lifecycle_verb(slug: str, agent_id: str | None, verb: str) -> dict:
    try:
        return await _svc().control(slug, verb, agent_id=agent_id)
    except LifecycleError as e:
        raise _http(e)


@router.post("/{slug}/stop")
async def stop_agent(
    slug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Stop a running session. If agent_id given, stop that instance; else all."""
    return await _lifecycle_verb(slug, agent_id, "stop")


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
    return await _lifecycle_verb(slug, agent_id, "shutdown")


@router.post("/{slug}/pause")
async def pause_agent(
    slug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Pause a running session."""
    return await _lifecycle_verb(slug, agent_id, "pause")


@router.post("/{slug}/resume")
async def resume_agent(
    slug: str,
    agent_id: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Resume a paused session."""
    return await _lifecycle_verb(slug, agent_id, "resume")


@router.post("/{slug}/directive")
async def inject_directive(
    slug: str,
    req: DirectiveRequest,
    user: WebUser = Depends(get_current_user),
):
    """Queue an operator directive for the agent's running session(s)."""
    if not req.text:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        return _svc().inject_directive(slug, req.text, agent_id=req.agent_id)
    except LifecycleError as e:
        raise _http(e)


# ── Learnings (agent-level) ──


@router.get("/{slug}/learnings")
async def get_learnings(slug: str, user: WebUser = Depends(get_current_user)):
    """Read the agent's learnings.md (all run kinds)."""
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
    """List an agent's sessions (``?strategy=`` filters the legacy meta tag)."""
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

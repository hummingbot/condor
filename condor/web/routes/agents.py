"""Trading Agents API routes — a THIN adapter over :class:`AgentService` (§5.2).

An **Agent** is the top-level unit: identity + strategy body + shared brain
(memory/skills). ALL of its operational history is the RunStore (§7.1): one
append-only JSONL event stream per run at ``agents/{slug}/runs/{run_id}.jsonl``
with opaque ULID run ids. Route shape::

    /agents                          -> list Agents (rollups)
    /agents/delegations              -> live delegation registry
    /agents/runs/{run_id}            -> one run (meta; ?events=true)
    /agents/runs/{run_id}/export     -> generated markdown transcript
    /agents/runs/{run_id}/executors  -> executors + performance for one run
    /agents/{slug}                   -> Agent detail (spec + recent runs)
    /agents/{slug}/runs              -> run history (?kind=&limit=)
    /agents/{slug}/consult|delegate  -> run the Agent's brain
    /agents/{slug}/start|stop|...    -> session lifecycle
    /agents/{slug}/performance       -> per-run perf + rollup

All CRUD + lifecycle goes through ``AgentService``; the read-only history
endpoints below are dashboard projections over the RunStore. Every
``LifecycleError`` maps to ``HTTPException(e.status, e.message)``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from condor.agents.learnings import learnings_path, read_learnings
from condor.agents.lifecycle import LifecycleError
from condor.agents.runstore import get_run_store
from condor.agents.service import AgentService
from condor.web.models import ReportSummary

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
    account: str = ""
    account_label: str = ""
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
    # ``agent_id`` is the run id (executors tag controller_id == run_id).
    agent_id: str
    run_id: str = ""
    session_num: int = 0  # display_seq from the run stream
    kind: str = "session"  # session | experiment | scheduled
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
    risk_limits: dict[str, Any] = {}
    denomination: str = ""
    account: str = ""
    account_label: str = ""
    default_config: dict[str, Any] = {}
    default_trading_context: str = ""
    schedule: dict[str, Any] = {}
    learnings: str = ""
    status: str = "idle"
    agent_id: str = ""
    # RunStore metas, newest first (all kinds) — the one history list.
    runs: list[dict[str, Any]] = []
    instances: list[RunningInstance] = []


class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""
    agent_key: str = ""
    tools: list[str] = []
    when_to_consult: str = ""
    risk_limits: dict[str, Any] = {}
    denomination: str = ""
    account: str = ""
    account_label: str = ""
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
    risk_limits: dict[str, Any] | None = None
    denomination: str | None = None
    account: str | None = None
    account_label: str | None = None
    default_config: dict[str, Any] | None = None
    default_trading_context: str | None = None
    schedule: dict[str, Any] | None = None
    content: str | None = None  # legacy alias for instructions


class UpdateLearningsRequest(BaseModel):
    content: str


class ConsultRequest(BaseModel):
    task: str
    context: str = ""


class StartAgentRequest(BaseModel):
    # ``strategy`` is accepted but IGNORED (Agent+Strategy collapse §5.3) —
    # the dashboard may still send it briefly.
    strategy: str = ""
    config: dict[str, Any] = {}
    trading_context: str = ""


class DelegateRequest(BaseModel):
    task: str
    timeout_s: int = 900
    # Per-delegation risk caps override — REPLACES the agent's AGENT.md baseline
    # for this one run (trading agents only).
    risk_limits: dict[str, Any] | None = None


class DirectiveRequest(BaseModel):
    text: str
    agent_id: str | None = None


# ── Performance projection (dashboard rollups) ──
# Executors tag ``controller_id == run_id`` (§7.1), so the RunStore metas ARE
# the enumeration the rollup fetches against.

# Run kinds whose executors are attributed to the agent (delegations/consults
# never launch the tick engine, so they never tag a controller_id).
_PERF_KINDS = ("session", "experiment", "scheduled")
_HISTORY_LIMIT = 200  # max runs enumerated per agent for rollups


def _perf_runs(slug: str) -> list[dict]:
    """RunStore metas for the agent's trading-attributed runs, newest first."""
    return [
        m
        for m in get_run_store().list_runs(slug, limit=_HISTORY_LIMIT)
        if m.get("kind") in _PERF_KINDS
    ]


def _native_perf_rows(
    agent_slug: str | None = None, agent_id: str | None = None
) -> dict[str, dict]:
    """Native per-run performance rows keyed by run id.

    Rolls up the Condor executor store (executors tag ``agent_id == run_id``,
    §7.1) via :mod:`condor.executors.performance` — no external services.
    """
    from condor.executors import ops
    from condor.executors.service import get_executor_runtime

    result = ops.performance(
        get_executor_runtime(),
        group_by="run",
        agent_id=agent_id,
        agent_slug=agent_slug,
    )
    return {row["key"]: row for row in result.get("groups", [])}


def _perf_model(meta: dict, row: dict) -> AgentPerformanceModel:
    """Map a native GroupPerformance row onto the dashboard model.

    Realized PnL is net of costs already; unrealized/volume are not tracked
    by the native rollup and stay 0 (open exposure is ``open_count``).
    """
    run_id = meta["run_id"]
    realized = float(row.get("realized_pnl_quote") or 0.0)
    closed = int(row.get("closed_count") or 0)
    return AgentPerformanceModel(
        agent_id=run_id,
        run_id=run_id,
        session_num=meta.get("display_seq", 0),
        kind=meta.get("kind") or "session",
        status=meta.get("status", ""),
        realized_pnl=realized,
        unrealized_pnl=0.0,
        total_pnl=realized,
        volume=0.0,
        fees=float(row.get("costs_quote") or 0.0),
        trade_count=closed,
        win_rate=float(row.get("win_rate") or 0.0),
        open_count=int(row.get("open_count") or 0),
        closed_count=closed,
        executors=[],
    )


async def _compute_agent_performance(agent):
    """Return list of AgentPerformanceModel plus rolled-up totals.

    Covers every attributed run of the agent (RunStore kinds session /
    experiment / scheduled), computed from the native executor store.
    The assembled rollup is cached ~30s (``_PERF_CACHE``).
    """
    slug = agent.slug

    cached = _cache_get(f"perf:{slug}")
    if cached is not None:
        return cached

    runs = _perf_runs(slug)

    sessions: list[AgentPerformanceModel] = []
    if runs:
        try:
            rows = _native_perf_rows(agent_slug=slug)
        except Exception as e:
            log.warning("native performance rollup (%s) failed: %s", slug, e)
            rows = {}
        for m in runs:
            row = rows.get(m["run_id"])
            if row is None:
                continue
            if m["kind"] == "experiment" and not row.get("closed_count"):
                continue
            sessions.append(_perf_model(m, row))

    real_sessions = [s for s in sessions if s.kind != "experiment"]
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
    """Build a RunningInstance from an engine info dict (live_only row)."""
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
        total_amount_quote=info.get("total_amount_quote", 100),
        trading_context=info.get("trading_context", ""),
        frequency_sec=info.get("frequency_sec", 60),
        agent_key=info.get("agent_key", ""),
        execution_mode=info.get("execution_mode", "loop"),
        risk_limits=info.get("risk_limits", {}),
    )


async def _build_agent_summary(agent) -> AgentSummary:
    """Roll up RunStore + engine + performance state for one agent."""
    try:
        sessions_perf, totals = await _compute_agent_performance(agent)
    except Exception as e:
        log.warning("compute_agent_performance(%s) failed: %s", agent.slug, e)
        sessions_perf, totals = [], {}
    perf_by_id = {p.agent_id: p for p in sessions_perf}

    metas = get_run_store().list_runs(agent.slug, limit=_HISTORY_LIMIT)

    infos = _svc().list_runs(agent.slug, live_only=True)
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
        latest = next((m for m in metas if m.get("kind") in _PERF_KINDS), None)
        if latest:
            status = latest.get("status") or "idle"

    latest_run_pnl = 0.0
    if sessions_perf:
        latest_perf = next(
            (
                perf_by_id[m["run_id"]]
                for m in metas
                if m.get("kind") != "experiment" and m["run_id"] in perf_by_id
            ),
            None,
        )
        if latest_perf:
            latest_run_pnl = latest_perf.total_pnl

    return AgentSummary(
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        consultable=agent.consultable,
        can_trade=agent.can_trade,
        when_to_consult=agent.when_to_consult,
        agent_key=agent.agent_key,
        denomination=agent.denomination,
        account=agent.account,
        account_label=agent.account_label,
        default_config=agent.default_config or {},
        schedule=agent.schedule or {},
        status=status,
        session_count=sum(1 for m in metas if m.get("kind") == "session"),
        experiment_count=sum(1 for m in metas if m.get("kind") == "experiment"),
        tick_count=tick_count,
        daily_pnl=latest_run_pnl,
        total_pnl=float(totals.get("total_pnl", 0.0)),
        total_volume=float(totals.get("volume", 0.0)),
        open_positions=int(totals.get("open_positions", 0)),
        instances=instances,
    )


# ── Agent routes ──


@router.get("", response_model=list[AgentSummary])
async def list_agents():
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
# /delegations and /runs paths MUST be registered before the /{slug}
# catch-all below; otherwise GET /agents/delegations would match get_agent
# with slug="delegations" and 404.


@router.get("/delegations")
async def list_delegations():
    """List in-flight and finished delegations (this process).

    Returns the full record per task (status + result/error) so the dashboard can
    render an at-a-glance list without a follow-up fetch per row. The registry is
    in-memory and small (ephemeral, per-process), so the payload stays cheap.
    Historical delegations live in the RunStore (``/agents/{slug}/runs?kind=delegation``).
    """
    from condor.agents.delegate import get_all_delegations

    return {"delegations": [dt.to_dict() for dt in get_all_delegations().values()]}


@router.get("/delegations/{task_id}")
async def get_delegation_status(task_id: str):
    """Get a delegation's status + result/error.

    Live tasks come from the in-process registry; after a restart the
    RunStore stream (kind=delegation, ``run_id == task_id``) still resolves,
    so a task_id never goes dark just because the process died.
    """
    from condor.agents.delegate import get_delegation

    dt = get_delegation(task_id)
    if dt is not None:
        return dt.to_dict()

    store = get_run_store()
    path = store.find_run_path(task_id)
    if path is not None:
        slug = path.parent.parent.name
        meta = store.run_meta(slug, task_id)
        if meta.get("kind") == "delegation":
            return {
                "task_id": task_id,
                "run_id": task_id,
                "agent": slug,
                "status": meta.get("status", ""),
                "task": meta.get("task", ""),
                "started_at": meta.get("started_at"),
                "ended_at": meta.get("ended_at"),
                "transcript": _svc().export_run(task_id),
            }
    raise HTTPException(status_code=404, detail=f"Delegation '{task_id}' not found")


@router.post("/delegations/{task_id}/stop")
async def stop_delegation_route(task_id: str):
    """Cancel a running delegation (status -> stopped)."""
    from condor.agents.delegate import get_delegation, stop_delegation

    if get_delegation(task_id) is None:
        raise HTTPException(status_code=404, detail=f"Delegation '{task_id}' not found")
    stopped = await stop_delegation(task_id)
    return {"stopped": stopped}


# ── Runs (the one history API) ──


@router.get("/runs/{run_id}")
async def get_run_route(
    run_id: str,
    events: bool = False,
):
    """One run: RunStore meta (+ live engine overlay), ``?events=true`` inlines
    the full event envelope list."""
    try:
        return _svc().get_run(run_id, include_events=events)
    except LifecycleError as e:
        raise _http(e)


@router.get("/runs/{run_id}/export")
async def export_run_route(run_id: str):
    """Markdown transcript of one run (generated view, §7.1)."""
    try:
        return {"run_id": run_id, "markdown": _svc().export_run(run_id)}
    except LifecycleError as e:
        raise _http(e)


@router.get("/runs/{run_id}/executors")
async def get_run_executors(run_id: str):
    """Executors + performance for a single run (executors tag agent_id == run_id)."""
    from condor.executors import ops
    from condor.executors.service import get_executor_runtime

    store = get_run_store()
    path = store.find_run_path(run_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    slug = path.parent.parent.name
    meta = store.run_meta(slug, run_id)
    _get_agent(slug)

    executors = ops.list_(get_executor_runtime(), agent_id=run_id, limit=200).get(
        "executors", []
    )
    row = _native_perf_rows(agent_id=run_id).get(run_id)
    if row is None:
        model = AgentPerformanceModel(
            agent_id=run_id,
            run_id=run_id,
            session_num=meta.get("display_seq", 0),
            kind=meta.get("kind") or "session",
            status=meta.get("status", ""),
        )
    else:
        model = _perf_model(meta, row)
    model.executors = executors
    return {"executors": executors, "performance": model.model_dump()}


@router.get("/{slug}", response_model=AgentDetail)
async def get_agent(slug: str):
    """Get Agent detail: identity, spec fields, and the recent run history."""
    agent = _get_agent(slug)

    try:
        sessions_perf, _totals = await _compute_agent_performance(agent)
    except Exception as e:
        log.warning("compute_agent_performance(%s) failed: %s", slug, e)
        sessions_perf = []
    perf_by_id = {p.agent_id: p for p in sessions_perf}

    runs = _svc().list_runs(slug, limit=50)

    infos = _svc().list_runs(slug, live_only=True)
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
        latest = next((m for m in runs if m.get("kind") in _PERF_KINDS), None)
        if latest:
            status = latest.get("status") or "idle"
            agent_id = latest.get("run_id", "")

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
        risk_limits=agent.risk_limits,
        denomination=agent.denomination,
        account=agent.account,
        account_label=agent.account_label,
        default_config=agent.default_config or {},
        default_trading_context=agent.default_trading_context,
        schedule=agent.schedule or {},
        learnings=read_learnings(agent.agent_dir),
        status=status,
        agent_id=agent_id,
        runs=runs,
        instances=instances,
    )


@router.get("/{slug}/runs")
async def list_agent_runs(
    slug: str,
    kind: str | None = None,
    limit: int = 50,
):
    """Run history for one agent (RunStore metas, newest first, live overlay).

    ``?kind=`` filters (session | experiment | delegation | consult | scheduled).
    """
    _get_agent(slug)
    return {"runs": _svc().list_runs(slug, kind=kind, limit=limit)}


@router.post("", response_model=AgentSummary)
async def create_agent(req: CreateAgentRequest):
    """Create a new Agent (identity + spec; AGENT.md is the one spec, §5.3)."""
    try:
        agent = _svc().create(
            name=req.name,
            description=req.description,
            instructions=req.instructions,
            agent_key=req.agent_key,
            tools=req.tools,
            when_to_consult=req.when_to_consult,
            risk_limits=req.risk_limits,
            denomination=req.denomination,
            account=req.account,
            account_label=req.account_label,
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
        account=agent.account,
        account_label=agent.account_label,
        default_config=agent.default_config or {},
        schedule=agent.schedule or {},
    )


@router.put("/{slug}")
async def update_agent(slug: str, req: UpdateAgentRequest):
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


@router.delete("/{slug}")
async def delete_agent(slug: str):
    """Tombstone an Agent (§5.2): history preserved, slug reserved.

    Refuses (409) while it has running sessions or nonterminal executors.
    """
    try:
        return _svc().delete(slug)
    except LifecycleError as e:
        raise _http(e)


@router.post("/{slug}/consult")
async def consult_agent(slug: str, req: ConsultRequest):
    """Run an Agent consult (its brain to completion) and return the answer."""
    if not req.task:
        raise HTTPException(status_code=400, detail="task is required")

    try:
        answer = await _svc().consult(
            slug,
            task=req.task,
            context=req.context,
        )
    except LifecycleError as e:
        raise _http(e)
    return {"agent": slug, "answer": answer}


# ── Delegate (fire-and-forget background tasks) ──


@router.post("/{slug}/delegate")
async def delegate_agent(slug: str, req: DelegateRequest):
    """Delegate a one-off task to a detached background Agent instance.

    Returns immediately with a ``task_id`` (also the run id); the agent runs
    unattended until done, then notifies the user. Trading agents run under a
    zero-seeded risk gate (per-call ``risk_limits`` override replaces the
    AGENT.md baseline); non-trading agents run with full auto-approve. The
    async sibling of ``/consult``.
    """
    _get_agent(slug)
    if not req.task:
        raise HTTPException(status_code=400, detail="task is required")

    try:
        d = await _svc().delegate(
            slug,
            task=req.task,
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
):
    """Per-run performance + rollup totals for an agent."""
    agent = _get_agent(slug)
    sessions, totals = await _compute_agent_performance(agent)
    running_ids = {
        i["agent_id"]
        for i in _svc().list_runs(slug, live_only=True)
        if i.get("status") == "running"
    }
    for s in sessions:
        if s.agent_id in running_ids:
            s.status = "running"
        elif not s.status or s.status == "running":
            s.status = "closed"
    return AgentPerformanceResponse(slug=slug, sessions=sessions, totals=totals)


# ── Lifecycle ──


@router.post("/{slug}/start")
async def start_session(
    slug: str,
    req: StartAgentRequest,
):
    """Start a session — the stateful unit of capital engagement — or, with
    ``execution_mode: "experiment"``, one simulated tick.

    The AGENT.md is the one spec (§5.3): launch config merges over its
    ``default_config``; a legacy ``strategy`` field in the request is ignored.
    """
    try:
        return await _svc().run(
            slug,
            config=req.config,
            trading_context=req.trading_context,
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
):
    """Stop a running session. If agent_id given, stop that instance; else all."""
    return await _lifecycle_verb(slug, agent_id, "stop")


@router.post("/{slug}/shutdown")
async def shutdown_agent(
    slug: str,
    agent_id: str | None = None,
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
):
    """Pause a running session."""
    return await _lifecycle_verb(slug, agent_id, "pause")


@router.post("/{slug}/resume")
async def resume_agent(
    slug: str,
    agent_id: str | None = None,
):
    """Resume a paused session."""
    return await _lifecycle_verb(slug, agent_id, "resume")


@router.post("/{slug}/directive")
async def inject_directive(
    slug: str,
    req: DirectiveRequest,
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
async def get_learnings(slug: str):
    """Read the agent's learnings (curated agent memory, §7.1)."""
    agent = _get_agent(slug)
    return {"content": read_learnings(agent.agent_dir)}


@router.put("/{slug}/learnings")
async def update_learnings(
    slug: str,
    req: UpdateLearningsRequest,
):
    """Replace the agent's learnings.md (operator curation)."""
    agent = _get_agent(slug)
    learnings_path(agent.agent_dir).write_text(req.content)
    return {"updated": True}


# ── Routines / reports ──


@router.get("/{slug}/routines")
async def get_agent_routines(slug: str):
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

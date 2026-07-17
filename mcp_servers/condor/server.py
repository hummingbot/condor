"""Condor MCP Server -- exposes Condor capabilities to AI agents.

Thin wrapper layer: tool registration + docstrings only.
All business logic lives in mcp_servers.condor.tools.*
"""

from mcp.server.fastmcp import FastMCP

from mcp_servers.condor.middleware import handle_errors
from mcp_servers.condor.tools import consult as consult_tool
from mcp_servers.condor.tools import delegate as delegate_tool
from mcp_servers.condor.tools import executors as executors_tool
from mcp_servers.condor.tools import (
    memory,
    notification,
    routines,
    skills,
    trading_agent,
)


def _build_instructions() -> str:
    """Server-level instructions surfaced to the MCP host on connect.

    An external MCP client (Claude Code, Cursor, …) only receives a flat list of
    tool names — it never sees Condor's skills/agents indexes, which are injected
    only into the in-bot `/agent` brain prompt. Without this, the host reaches for
    whatever obvious tool is in scope (e.g. a raw `manage_executors`) instead of the
    matching Condor playbook. We embed the live indexes here so any host can route
    a request to the right skill/agent. Built once at import; cheap and read-only.
    """
    base = (
        "Condor exposes reusable **skills** (playbooks, some linked to a runnable "
        "routine) and consultable **domain agents** on top of these tools.\n\n"
        "ROUTING RULE — before handling a request with raw tools, apply this "
        "priority: (1) a matching SKILL, (2) a matching AGENT, (3) raw tools only "
        "if neither matches:\n"
        '- If a SKILL matches, call `manage_skill(action="read", name="<name>")` '
        'and follow its steps. When it links a routine (shown as "→ routine: X"), '
        'run that routine via `manage_routines(action="run", name="X", config={})` '
        "instead of reimplementing it by hand.\n"
        "- If a domain AGENT matches, route to it with "
        '`consult(agent="<slug>", task="...", context="...")` and summarize its answer '
        "(consult = attended, blocking, each trade goes to the user to approve). "
        'ONLY when the user explicitly detaches ("in the background", "ping me '
        'when done") use `delegate(agent="<slug>", task="...", risk_limits={…})` '
        "— unattended, budget-gated (pass caps for a trading agent); it returns a "
        "run_id you track with `get_run`/`control_run`, NOT a delegate poll. See "
        "the EXECUTION VERBS block below for run vs consult vs delegate.\n"
        "- ROUTINES ARE SPECIAL: any request to CREATE, EDIT, FIX, DEBUG, or "
        "design a routine MUST go through the `routine_builder` agent "
        '(`consult(agent="routine_builder", ...)` for inline work, '
        '`delegate(agent="routine_builder", ...)` for background). '
        "It is the single entry point for routine authoring — do NOT write routine "
        "code yourself and do NOT hand-roll it with raw `manage_routines` "
        "create_routine/edit_routine. (RUNNING an existing routine is not authoring "
        '— for that just call `manage_routines(action="run", name="...")`.)\n'
        "- Only fall back to raw tools when nothing matches.\n"
        "Anti-pattern: answering a domain request (deploy/tune an executor, analyze "
        "logs, author a routine) with a chain of raw `manage_*` "
        "calls when a skill or agent covers it.\n"
        'Discover more anytime with `manage_skill(action="list")`.'
    )

    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    cli = (
        "[OPS CLI — `condor`, for hosts with shell access]\n"
        "Operational/diagnostic work has NO MCP tool — it belongs to the `condor` "
        f"CLI (on PATH via ~/.local/bin/condor, else `uv run --directory {repo_root} "
        "condor …`). Run it via your shell for:\n"
        "- `condor doctor [--fix]` — health checks (socket, clock skew, stale MCP "
        'clients, venue probes, spec validation); ANY health/setup/"why is X '
        'failing" question starts here\n'
        "- `condor status` / `condor logs [-f]` / `condor runs [export <id>]` — "
        "server, live runs, server log, run history (file-backed; work with the "
        "server down)\n"
        "- `condor stop [run|slug] [--close]` — the escape hatch when MCP calls "
        "hang or the server is wedged\n"
        "- `condor accounts add/remove/default` — venue credential lifecycle "
        "(keys via stdin, never argv); `condor serve` — start the server\n"
        "Contract: compact Markdown out, `--json` for raw values, stable exit "
        "codes (0 ok · 2 not found · 3 server not running · 4 config error). "
        "Prefer the typed MCP tools for trading/agent domain work; prefer the CLI "
        "for ops, diagnostics, and anything that must survive a wedged server."
    )

    verbs = (
        "[EXECUTION VERBS — run vs consult vs delegate vs experiment]\n"
        "When the user wants an agent to DO something, pick the verb in order:\n"
        '1. WHOSE GOAL? The agent\'s own strategy — "run/start/launch <agent>", '
        '"let it trade", "on its schedule" → run_agent(slug): a SESSION running '
        "the agent's default_config loop under journal-seeded risk. Steering "
        '("focused on SOL", "10 ticks", "tighter stops") is trading_context/'
        "max_ticks/config on the SAME run_agent call, not a task. A one-off goal "
        "you specify → a TASK (step 3). Boundary: finishes by itself = task; runs "
        "until stopped / N ticks = the mandate = run_agent.\n"
        '2. REHEARSAL? "test/try it/dry run/simulate/paper/without real money" → '
        "run_agent(slug, dry_run=true): one simulated tick, all mutations blocked. "
        'Ambiguous "try it" → ASK before going live.\n'
        "3. TASK — attended → consult (blocks; each trade goes to the user to "
        'approve; DEFAULT for "have <agent> do X"). Detached (explicit "in the '
        'background"/"ping me when done") → delegate + risk_limits budget for a '
        "trading agent. Never delegate just to avoid waiting when the user could "
        "approve.\n"
        "MULTI-STAGE: status/stop/history of ANY run (session, experiment, consult, "
        "delegation — all share one run_id) go through get_run / list_runs / "
        "control_run(run_id, verb, close?) / search_history / "
        "manage_executors(performance). Don't invent delegate.get/stop — a "
        "delegation is a run; track it like one."
    )

    sections = [base, cli, verbs]
    try:
        from condor.memory import SkillStore
        from mcp_servers.condor.settings import settings

        # Scope to the launched assistant: an agent subprocess (--agent-slug) must
        # advertise ITS OWN skills here, not the chat condor's global library.
        skills_index = SkillStore(settings.agent_slug or None).list_index()
        if skills_index:
            sections.append(
                "[SKILLS — read the playbook before a matching flow]\n" + skills_index
            )
    except Exception:
        pass  # Advisory — never block server startup on index assembly.
    try:
        from condor.agents.agent import AgentStore

        agents_index = AgentStore().list_consultable_index()
        if agents_index:
            sections.append("[AGENTS — consult for domain work]\n" + agents_index)
    except Exception:
        pass

    return "\n\n".join(sections)


mcp = FastMCP("condor", instructions=_build_instructions())


@mcp.tool()
@handle_errors("consult agent")
async def consult(agent: str, task: str, context: str = "") -> dict:
    """Consult a specialized domain agent and get its answer.

    Use this to delegate domain work instead of doing it yourself: the agent runs
    with its own focused tools and domain memory, then returns an answer you can
    summarize for the user. Available agents are listed in your [AGENTS] section.
    The agent may execute actions (gated by the user's confirmation).

    Args:
        agent: Agent slug (e.g. "executor_manager").
        task: The question or task for the agent, in plain language.
        context: Optional extra context (relevant numbers, the user's intent).

    Returns:
        {"agent": "...", "answer": "..."} or {"error": "..."}.
    """
    return await consult_tool.consult(agent, task, context)


@mcp.tool()
@handle_errors("delegate task")
async def delegate(
    agent: str = "",
    task: str = "",
    risk_limits: dict | None = None,
) -> dict:
    """Start a DETACHED background task and return its run_id immediately.

    DELEGATE is the async, unattended sibling of CONSULT. Where ``consult`` blocks
    and returns an answer now (each trade human-gated), ``delegate`` hands a
    goal-oriented task to a detached agent that works autonomously until done, then
    notifies the user — while you stay free. Use it ONLY when the user explicitly
    detaches ("in the background", "ping me when done", "while I'm out") — e.g.
    "go build a routine that scans SOL pools and ping me". If the user is present
    and would approve trades, use ``consult`` instead.

    A delegation IS a run: this returns ``{"run_id", "status": "running",
    "next_steps"}`` — track it with ``get_run(run_id)`` and stop it with
    ``control_run(run_id, "stop")``. There is NO delegate get/list/stop.

    Authorization: a delegation to a TRADING agent (one that has an AGENT.md
    risk_limits baseline, is given caps here, or can reach manage_executors —
    declaring it, or declaring no tool scope at all) runs under a zero-seeded
    risk gate — tool calls auto-approve within caps, and the caps ARE the
    authorization (nobody approves each trade). The caps come from the optional
    risk_limits arg when given (it REPLACES the agent's AGENT.md baseline for
    this one run), else the baseline. A trading delegation with neither errors
    at start; "unbounded" is expressed by passing explicitly large caps.
    Non-trading specialists (e.g. routine_builder) run with full auto-approve.

    Args:
        agent: Agent slug to delegate to.
        task: The one-off task, in plain language.
        risk_limits: Per-delegation risk caps override (trading agents only).
            Keys: max_position_size_quote, max_open_executors,
            max_drawdown_pct, shutdown_drawdown_pct. Replaces the agent baseline.

    Returns:
        {"run_id", "status": "running", "next_steps"} or {"error": "..."}.
    """
    return await delegate_tool.delegate(agent, task, risk_limits)


@mcp.tool()
@handle_errors("send notification")
async def send_notification(
    text: str,
) -> dict:
    """Notify the user (recorded in the notification outbox).

    Args:
        text: Message text to send.

    Returns:
        {"sent": true} on success, {"error": "..."} on failure.
    """
    return await notification.send_notification(text)


@mcp.tool()
@handle_errors("get notifications")
async def get_notifications(
    limit: int = 30,
    since_ts: float | None = None,
    agent_id: str | None = None,
) -> dict:
    """Read recent Condor notifications from the outbox (oldest first).

    Every user-facing notification (session ticks, delegation results,
    agent pings) is recorded in the outbox. Use this to catch up on what
    agents reported — e.g. after
    starting a session or delegation from this harness — or to poll for
    new entries by passing the last seen ``ts`` as ``since_ts``.

    Args:
        limit: Max entries returned.
        since_ts: Only entries with ts strictly greater than this.
        agent_id: Filter to one run (session or delegation id).

    Returns:
        {"notifications": [{ts, agent_id, kind, text, ...}]}
    """
    return await notification.get_notifications(limit, since_ts, agent_id)


@mcp.tool()
@handle_errors("resolve approval")
async def resolve_approval(
    approval_id: str,
    decision: str,
    note: str = "",
) -> dict:
    """Resolve a pending trade/tool approval (approve or deny).

    Agent runs surface dangerous tool calls as ``kind=approval``
    notifications carrying an approval_id; the run blocks (default DENY
    after the timeout) until a human answers. Relay the question to the
    user, then call this with their decision. Resolution is idempotent —
    double-resolves and resolves after the run consumed the grant are
    no-ops reporting the recorded state.

    Args:
        approval_id: The id from the approval notification.
        decision: "approve" or "deny".
        note: Optional human note recorded with the decision.

    Returns:
        The approval record including the recorded decision and channel.
    """
    from mcp_servers.condor.condor_client import call_control

    return await call_control(
        "approval.resolve",
        {
            "approval_id": approval_id,
            "decision": decision,
            "note": note,
            "channel": "mcp",
        },
    )


@mcp.tool()
@handle_errors("list approvals")
async def list_approvals() -> dict:
    """List approvals still awaiting a human decision.

    Returns:
        {"approvals": [{approval_id, run_id, agent_slug, summary, ...}]}
    """
    from mcp_servers.condor.condor_client import call_control

    return await call_control("approval.list")


@mcp.tool()
@handle_errors("manage routines")
async def manage_routines(
    action: str,
    name: str | None = None,
    config: dict | None = None,
    agent_slug: str | None = None,
    code: str | None = None,
) -> dict:
    """Manage and run Condor routines (auto-discoverable Python scripts).

    Actions -- Discovery & Execution:
    - "list": List all available routines with name, description, type, and scope
    - "describe": Show config schema for a routine (requires name)
    - "run": Execute a one-shot routine in a disposable worker and return its
      result (requires name, optional config; hard 120s timeout)
    - "schedule_routine": Create a durable cron schedule for a routine
      (requires name; config carries "cron" (5-field) and optional "tz",
      remaining keys are the routine config). Missed fires are skipped.
    - "unschedule_routine": Remove a schedule (pass the schedule_id as name)
    - "list_schedules": List durable routine schedules

    Actions -- Agent-Local Routine CRUD (requires agent_slug or CONDOR_AGENT_SLUG):
    - "create_routine": Create a new agent-local routine (requires name, code)
    - "read_routine": Read source code of a routine (requires name)
    - "edit_routine": Update an agent-local routine (requires name, code)
    - "delete_routine": Delete an agent-local routine (requires name)

    Agent-local routines live in agents/{slug}/routines/ and are only visible
    to that agent. They follow the same pattern as global routines: a
    Config(BaseModel) class and an async run(config, context) function.

    Args:
        action: The action to perform.
        name: Routine name (required for all except list/list_schedules).
            For "unschedule_routine", pass the schedule_id as name.
        config: Config overrides for run/schedule (optional, merged with
            defaults; for schedule_routine also carries "cron"/"tz").
        agent_slug: Target agent for agent-local routine CRUD operations.
        code: Python source code for create_routine / edit_routine.

    Returns:
        Action-specific result dict.
    """
    return await routines.manage_routines(action, name, config, agent_slug, code)


@mcp.tool()
@handle_errors("manage executors")
async def manage_executors(
    action: str,
    executor_type: str | None = None,
    config: dict | None = None,
    executor_id: str | None = None,
    agent_id: str | None = None,
    keep_position: bool = True,
    group_by: str | None = None,
) -> dict | list:
    """Manage Condor-native executors (gateway-backed DEX execution).

    These run in the persistent Condor process against Hummingbot Gateway
    (keys never leave Gateway). Creates and stops are risk- and
    human-gated; experiments cancel them automatically.

    executor_type is a composite {kind}_{instrument}: kind ∈ {order (single
    leg, place-and-track), position (round-trip with a SL/trailing/TP/time
    barrier ladder)}, instrument ∈ {spot, perp, pred}. Six types: order_spot,
    order_perp, order_pred, position_spot, position_perp, position_pred. venue
    selects the exchange (spot→solana default; perp→hyperliquid; pred→
    polymarket|hyperliquid).

    Actions:
    - "create": start an executor. Requires executor_type + config.
      - "order_spot": config {chain_network, wallet_address, base_token,
        quote_token, amount, side (BUY|SELL), slippage_pct?, order_type?,
        notional_quote? (quote-unit value; auto-priced when omitted)}
      - "position_spot": config {chain_network, wallet_address, base_token,
        quote_token, amount_quote, slippage_pct?, take_profit_pct?,
        stop_loss_pct?, time_limit_s?, trailing_activation_pct?, trailing_delta_pct?}
      - "position_perp" (venue hyperliquid): config {coin, side (LONG|SHORT),
        notional_quote, leverage?, cross_margin?, entry?, limit_px?,
        liquidation_guard_pct?, native_triggers?, + barrier fields}
      - "position_pred" (venue polymarket|hyperliquid): config {market,
        position (LONG|SHORT), amount_quote, resolve_win_price?,
        resolve_loss_price?, + barrier fields}
      - "order_perp" / "order_pred": single-leg variants of the above.
    - "stop": stop an executor. keep_position=True (default) DETACHES —
      the on-chain position stays open and unmanaged; keep_position=False
      closes the position on-chain. Requires executor_id.
    - "get": fetch one executor's full state. Requires executor_id.
    - "list": list executors (optionally filtered by agent_id).
    - "performance": rolled-up scorecard — open/closed/failed counts,
      realized PnL, costs, win rate, close-type breakdown — grouped by
      group_by: "agent" (per agent, all its runs), "run" (per
      session/delegation), "strategy" (legacy records only — new executors
      carry no strategy), "venue", or "type". Use this to answer
      "how is agent X doing" in one call.

    Args:
        action: create | stop | get | list | performance
        executor_type: {order,position}_{spot,perp,pred} (create only)
        config: executor config dict (create only)
        executor_id: target executor (stop/get)
        agent_id: attribution/filter; defaults to this session's agent
        keep_position: on stop, keep net position (True) or swap back (False)
        group_by: performance grouping (default "agent")

    Returns:
        Action-specific result dict.
    """
    return await executors_tool.manage_executors(
        action, executor_type, config, executor_id, agent_id, keep_position, group_by
    )


@mcp.tool()
@handle_errors("list agents")
async def list_agents() -> dict:
    """List agent summaries (slug, name, description, capabilities).

    Summaries only — fetch the full editable spec with get_agent. Live runs
    are listed by list_runs; history by list_runs/get_run.
    """
    return await trading_agent.list_agents()


@mcp.tool()
@handle_errors("get agent")
async def get_agent(agent_slug: str) -> dict:
    """Get one agent's FULL editable spec (the AGENT.md identity, §5.3):
    name, description, instructions, model, tools, risk_limits,
    denomination, account, default_config, default_trading_context, schedule.
    """
    return await trading_agent.get_agent(agent_slug)


@mcp.tool()
@handle_errors("create agent")
async def create_agent(
    name: str,
    description: str = "",
    instructions: str = "",
    agent_key: str = "",
    tools: list[str] | None = None,
    when_to_consult: str = "",
    risk_limits: dict | None = None,
    denomination: str = "",
    account: str = "",
    account_label: str = "",
    default_config: dict | None = None,
    default_trading_context: str = "",
    schedule: dict | None = None,
) -> dict:
    """Create a new agent (writes agents/{slug}/AGENT.md — the ONE spec).

    Validation is service-owned: risk_limits require a denomination (the
    numeraire the caps are expressed in); a schedule requires a valid
    5-field cron + IANA tz AND a bounded duration (max_ticks > 0 in
    default_config); a tombstoned slug is reserved forever.

    Args:
        name: Display name; the slug is derived from it.
        description: One-line purpose.
        instructions: The strategy/playbook body (markdown).
        agent_key: Model key (default: the platform default).
        tools: Declared tool scope; empty = unrestricted (and therefore
            trading — an agent that can trade must declare risk_limits).
        when_to_consult: When the chat brain should consult this agent.
        risk_limits: {max_position_size_quote, max_open_executors,
            max_drawdown_pct?, shutdown_drawdown_pct?}.
        denomination: Numeraire for risk limits (e.g. "USDC", "SOL").
        account: Account display name or custody address. Names are resolved
            immediately and the canonical address is persisted.
        default_config: Launch defaults (frequency_sec, max_ticks, ...).
        default_trading_context: Default context string for runs.
        schedule: {cron: "m h dom mon dow", tz?: "UTC"} — unattended fires.
    """
    return await trading_agent.create_agent(
        name=name,
        description=description,
        instructions=instructions,
        agent_key=agent_key,
        tools=tools,
        when_to_consult=when_to_consult,
        risk_limits=risk_limits,
        denomination=denomination,
        account=account,
        account_label=account_label,
        default_config=default_config,
        default_trading_context=default_trading_context,
        schedule=schedule,
    )


@mcp.tool()
@handle_errors("update agent")
async def update_agent(
    agent_slug: str,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    agent_key: str | None = None,
    tools: list[str] | None = None,
    when_to_consult: str | None = None,
    risk_limits: dict | None = None,
    denomination: str | None = None,
    account: str | None = None,
    account_label: str | None = None,
    default_config: dict | None = None,
    default_trading_context: str | None = None,
    schedule: dict | None = None,
) -> dict:
    """Update fields of an agent's spec (only the fields you pass change).

    Same validation as create_agent; rejected for tombstoned slugs.
    """
    return await trading_agent.update_agent(
        agent_slug,
        name=name,
        description=description,
        instructions=instructions,
        agent_key=agent_key,
        tools=tools,
        when_to_consult=when_to_consult,
        risk_limits=risk_limits,
        denomination=denomination,
        account=account,
        account_label=account_label,
        default_config=default_config,
        default_trading_context=default_trading_context,
        schedule=schedule,
    )


@mcp.tool()
@handle_errors("delete agent")
async def delete_agent(agent_slug: str) -> dict:
    """Delete an agent — a TOMBSTONE, not an erase (§5.2).

    Rejected while the agent has running engines or nonterminal executors.
    Its history stays readable forever and the slug is reserved (a future
    create cannot re-acquire the old attribution).
    """
    return await trading_agent.delete_agent(agent_slug)


@mcp.tool()
@handle_errors("run agent")
async def run_agent(
    agent_slug: str,
    config: dict | None = None,
    dry_run: bool = False,
    trading_context: str = "",
) -> dict:
    """Launch a run of an agent. Returns {"agent_id": <run_id>} immediately.

    dry_run=True runs ONE experiment tick: the agent plans and records but
    every mutating action is cancelled — use it to preview behavior.
    Launch config overrides are limited (trading_context, duration knobs,
    dry-run) and risk overrides are STRICTER-ONLY — widening a baseline cap
    is rejected (§5.3).

    Args:
        agent_slug: Which agent to run.
        config: Launch overrides (max_ticks and stricter risk_limits only).
        dry_run: One read-only experiment tick instead of a live loop.
        trading_context: Context string for this run.
    """
    return await trading_agent.run_agent(
        agent_slug, config=config, dry_run=dry_run, trading_context=trading_context
    )


@mcp.tool()
@handle_errors("list runs")
async def list_runs(agent_slug: str = "", kind: str = "", limit: int = 20) -> dict:
    """Run history (newest first): sessions, experiments, delegations,
    consults, scheduled fires — with status and display seq. Running runs
    carry live engine info under "live".

    Args:
        agent_slug: Filter to one agent ("" = all).
        kind: session | experiment | delegation | consult | scheduled ("" = all).
        limit: Max runs returned.
    """
    return await trading_agent.list_runs(agent_slug, kind=kind, limit=limit)


@mcp.tool()
@handle_errors("get run")
async def get_run(run_id: str, include_events: bool = False) -> dict:
    """One run's status + metadata (live engine info while it runs; the
    durable RunStore record after). include_events=True returns the full
    event stream (ticks, tool calls, permissions, directives).
    """
    return await trading_agent.get_run(run_id, include_events=include_events)


@mcp.tool()
@handle_errors("control run")
async def control_run(run_id: str, verb: str, close: bool = False) -> dict:
    """Run-scoped control: pause | resume | stop.

    stop is position-preserving by default (executors detach; barriers on
    surviving executors keep managing). close=True additionally closes the
    run's remaining owned inventory (never external/manual positions —
    §6.2 owned_net_base). Works for interrupted/terminal runs' surviving
    financial scope too. Agent-wide emergency winddown is shutdown_agent.
    """
    return await trading_agent.control_run(run_id, verb, close=close)


@mcp.tool()
@handle_errors("shutdown agent")
async def shutdown_agent(agent_slug: str) -> dict:
    """AGENT-scoped emergency winddown (§6.2): stops every live run of the
    slug, cancels ALL of the agent's persisted orders (including native
    TP/SL triggers, never external ones), and closes its remaining owned
    inventory per its shutdown policy.
    """
    return await trading_agent.shutdown_agent(agent_slug)


@mcp.tool()
@handle_errors("manage memory")
async def manage_memory(
    action: str,
    name: str | None = None,
    content: str | None = None,
    description: str | None = None,
    type: str = "fact",
    query: str | None = None,
    max_entries: int = 30,
) -> dict:
    """Manage your persistent memory ABOUT THE USER (shared across sessions and agents).

    This is what you remember about the user: their preferences, stable facts,
    feedback they gave you, and reference pointers. It is keyed by the user (not
    the chat), so the /agent chat and the user's trading agents all share it.
    The index of your memories is auto-injected into your context as
    [USER MEMORY]; use "read" to pull the full body of a specific memory.

    WHEN TO WRITE:
    - Save something only when it is NEW and STABLE about the user — a standing
      preference ("always report in USD"), a fact ("default exchange is Binance"),
      a correction the user made, or a reference pointer. Do NOT save ephemeral
      conversation details. One memory = one fact. Keep `description` to one line.

    Actions:
    - "write": Create/overwrite a memory (requires name, content, description; optional type).
    - "read": Get the full body of a memory (requires name).
    - "search": Keyword search over your memories (requires query).
    - "list": Return the memory index (one line per memory).
    - "delete": Remove a memory (requires name).
    - "audit": Recent write/delete events (who changed what).

    Args:
        action: write | read | search | list | delete | audit
        name: Short kebab/snake name for the memory (e.g. "report-in-usd").
        content: The full fact/body (required for write).
        description: One-line summary shown in the index (required for write).
        type: preference | fact | feedback | reference (default "fact").
        query: Search string (for search).
        max_entries: Cap for search/audit results (default 30).

    Returns:
        Action-specific result dict.
    """
    return await memory.manage_memory(
        action, name, content, description, type, query, max_entries
    )


@mcp.tool()
@handle_errors("record learning")
async def record_learning(text: str, replaces: str | None = None) -> dict:
    """Record ONE durable operational learning for this agent (§7.1).

    Learnings are the agent's own operational memory — market behavior,
    execution quirks, venue timing — kept in ``agents/{slug}/learnings.md``
    and injected into every tick's prompt as [LEARNINGS]. Only record
    something genuinely NEW; to update or merge into an existing entry, pass
    ``replaces``.

    The list is capped at 40: when full, a plain append returns an error
    instead of silently evicting old knowledge — consolidate with
    ``replaces`` instead. This is distinct from user memory, which agents
    read but never write.

    Args:
        text: The learning — one factual sentence, no speculation.
        replaces: Case-insensitive substring identifying exactly ONE existing
            entry to rewrite (consolidation). Errors if it matches zero or
            several entries.

    Returns:
        {"recorded": <normalized text>, "replaced": bool, "total": <count>}
    """
    return await memory.record_learning(text, replaces=replaces)


@mcp.tool()
@handle_errors("manage skill")
async def manage_skill(
    action: str,
    name: str | None = None,
    description: str | None = None,
    body: str | None = None,
    references_routine: str | None = None,
    query: str | None = None,
    max_entries: int = 30,
    agent_slug: str | None = None,
    scope: str | None = None,
    file: str | None = None,
    content: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    changelog: str | None = None,
) -> dict:
    """Manage your SKILLS — playbooks (know-how) you can follow and refine.

    A skill is a markdown *playbook*: a reusable procedure with WHEN to apply it
    and the STEPS to take (e.g. "how to open a grid in a band-walk", "checklist
    before raising leverage"). Skills are GENERAL to the assistant — a shared
    library, the same for everyone using it — distinct from manage_memory, which
    is what YOU learn about a specific USER (per-user). The skills index is
    auto-injected as [SKILLS]; use "read" to pull a full playbook before following
    it, and "create"/"edit" to capture or improve a reusable procedure.

    A skill can REFERENCE a routine: "read" reports `routine_ok` — if false, the
    referenced routine no longer exists; do NOT invoke it. A playbook is advisory
    text; executing what it describes (a routine, an executor) still goes through
    the normal risk/confirmation controls. The skill is NOT a bypass.

    A skill can also BUNDLE companion files (e.g. config templates) beside its
    playbook. "read" lists them under `files`; pull one on demand with
    "read_file" (name + file). This is progressive disclosure — the index shows
    only the playbook, the companions stay out of context until you ask for one.
    Author or update a companion with "write_file" (name + file + content):
    prefer it over a raw filesystem write so the path resolves through the skill
    slug and stays inside the skill folder. ("write_file" only touches companion
    files — edit the playbook body itself with "edit".)

    Skills are scoped per-assistant: a launched agent reads/writes ONLY its own
    library. From the chat you can target a specific agent's local skill library
    with agent_slug — use this to author or inspect an agent's skills while
    building it. Without agent_slug the current assistant's library is used.

    PLACEMENT RULE (three tiers): the chat's own library is the repo-root
    skills/ dir, which is HOST-VISIBLE — any harness opened in the Condor repo
    (Claude Code, OpenClaw, Hermes) indexes it natively. Put only genuinely
    host-relevant playbooks there. Knowledge meant for ONE agent belongs in
    that agent's local tier (pass agent_slug). Knowledge EVERY domain agent
    should get (executor mechanics, venue quirks) belongs in the SHARED tier
    (pass scope="shared" — agents/_shared/skills): domain agents read it
    automatically (local overrides shared on a name clash) but can never
    write it — shared edits happen only here in the chat, with the user.
    Skills follow the agentskills.io format: hyphenated names, and the
    description states WHAT the skill does and WHEN to use it.

    Actions:
    - "read": Get a full playbook + routine validation + companion `files` (requires name).
    - "read_file": Get the contents of one bundled companion file (requires name + file).
    - "write_file": Create/overwrite one bundled companion file (requires name + file + content).
    - "search": Keyword search over the skills (requires query).
    - "list": Return the skills index (one line per skill).
    - "create": Add/overwrite a skill (requires name, description, body).
    - "patch": Delta-edit a skill BODY (requires name, old_string, new_string,
      changelog). old_string must match exactly once; provenance is stamped.
    - "edit": Replace fields wholesale (requires name + any of description/body/
      references_routine). Human-directed use; prefer "patch" for incremental
      improvements — full-body rewrites lose detail.
    - "delete": Remove a skill (requires name).

    Args:
        action: read | read_file | write_file | search | list | create | edit | delete
        name: Short kebab-case name (e.g. "grid-en-band-walk").
        description: Single line stating what the skill does AND when to use
            it (create/edit) — the routing trigger, ≤1024 chars.
        body: The steps / playbook text (create/edit).
        references_routine: Optional routine name to link; "" clears it (create/edit).
        query: Search string (for search).
        max_entries: Cap for search results (default 30).
        agent_slug: Target a specific agent's local skill library (chat-side
            authoring).
        scope: "shared" targets the shared tier read by every domain agent
            (chat-only; mutually exclusive with agent_slug).
        file: Bare name of a bundled companion file (for read_file/write_file).
        content: Full contents to write to the companion file (for write_file).
        old_string: Exact existing body text to replace (for patch; must be unique).
        new_string: Replacement text (for patch).
        changelog: One line — what changed and why (required for patch).

    Returns:
        Action-specific result dict.
    """
    return await skills.manage_skill(
        action,
        name=name,
        description=description,
        body=body,
        references_routine=references_routine,
        query=query,
        max_entries=max_entries,
        agent_slug=agent_slug,
        scope=scope,
        file=file,
        content=content,
        old_string=old_string,
        new_string=new_string,
        changelog=changelog,
    )


# ---------------------------------------------------------------------------
# Trading-agent journal tools — the canonical interface live tick prompts call
# directly (see condor/agents/prompts.py). Kept as dedicated top-level tools
# rather than manage_trading_agent actions so the agent's ergonomic, oft-used
# write path is a single named tool.
# ---------------------------------------------------------------------------

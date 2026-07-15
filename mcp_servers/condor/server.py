"""Condor MCP Server -- exposes Condor capabilities to AI agents.

Thin wrapper layer: tool registration + docstrings only.
All business logic lives in mcp_servers.condor.tools.*
"""

from mcp.server.fastmcp import FastMCP

from mcp_servers.condor.middleware import handle_errors
from mcp_servers.condor.tools import consult as consult_tool
from mcp_servers.condor.tools import context
from mcp_servers.condor.tools import delegate as delegate_tool
from mcp_servers.condor.tools import (
    executors as executors_tool,
    memory,
    notes,
    notification,
    routines,
    servers,
    skills,
    trading_agent,
)


def _build_instructions() -> str:
    """Server-level instructions surfaced to the MCP host on connect.

    An external MCP client (Claude Code, Cursor, …) only receives a flat list of
    tool names — it never sees Condor's skills/agents indexes, which are injected
    only into the in-bot `/agent` brain prompt. Without this, the host reaches for
    whatever obvious tool is in scope (e.g. a raw `manage_bots`) instead of the
    matching Condor playbook. We embed the live indexes here so any host can route
    a request to the right skill/agent. Built once at import; cheap and read-only.
    """
    base = (
        "Condor exposes reusable **skills** (playbooks, some linked to a runnable "
        "routine) and consultable **domain agents** on top of these tools.\n\n"
        "ROUTING RULE — before handling a request with raw tools (including tools "
        "from other connected MCP servers such as mcp-hummingbot), apply this "
        "priority: (1) a matching SKILL, (2) a matching AGENT, (3) raw tools only "
        "if neither matches:\n"
        '- If a SKILL matches, call `manage_skill(action="read", name="<name>")` '
        'and follow its steps. When it links a routine (shown as "→ routine: X"), '
        'run that routine via `manage_routines(action="run", name="X", config={})` '
        "instead of reimplementing it by hand.\n"
        "- If a domain AGENT matches, delegate with "
        '`consult(agent="<slug>", task="...", context="...")` and summarize its answer. '
        "For a long, one-off task you want run in the background until done (it pings "
        'the user when finished), use `delegate(action="start", agent="<slug>", '
        'task="...")` instead and poll with `delegate(action="get", task_id="...")`.\n'
        "- ROUTINES ARE SPECIAL: any request to CREATE, EDIT, FIX, DEBUG, or "
        "design a routine MUST go through the `routine_builder` agent "
        '(`consult(agent="routine_builder", ...)` for inline work, '
        '`delegate(action="start", agent="routine_builder", ...)` for background). '
        "It is the single entry point for routine authoring — do NOT write routine "
        "code yourself and do NOT hand-roll it with raw `manage_routines` "
        "create_routine/edit_routine. (RUNNING an existing routine is not authoring "
        '— for that just call `manage_routines(action="run", name="...")`.)\n'
        "- Only fall back to raw tools when nothing matches.\n"
        "Anti-pattern: answering a domain request (deploy/tune an executor, analyze "
        "logs, author a routine) with a chain of raw `mcp-hummingbot`/`manage_*` "
        "calls when a skill or agent covers it.\n"
        'Discover more anytime with `manage_skill(action="list")`.'
    )

    sections = [base]
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
    action: str,
    agent: str = "",
    task: str = "",
    task_id: str = "",
    risk_limits: dict | None = None,
) -> dict:
    """Delegate a one-off task to a background agent instance.

    DELEGATE is the async, unattended sibling of CONSULT. Where ``consult`` blocks
    and returns an answer now (mutations human-gated), ``delegate`` hands a
    goal-oriented task to a DETACHED agent that works autonomously until done, then
    notifies the user with the result — while you stay free to do other things. Use
    it for "go build/scan/produce X and ping me when finished" (e.g. "create a
    routine that scans SOL pools").

    Authorization: a delegation to a TRADING agent (one that needs a hummingbot
    server, has an AGENT.md risk_limits baseline, or is given caps here) runs under a
    zero-seeded risk gate — tool calls auto-approve within caps, uncapped bot
    deploys and place_order are blocked. The caps come from the optional
    risk_limits arg when given (it REPLACES the agent's AGENT.md baseline for
    this one run — what you pass is exactly what governs), else the baseline. A
    trading delegation with neither errors at start; "unbounded" is expressed by
    passing explicitly large caps. Serverless agents (e.g. routine_builder) run
    with full auto-approve.

    The user tracks a delegation in Telegram with the /delegations command (NOT
    "/task" — that does not exist) and is pinged automatically when it finishes.
    Never invent a status command; "start" returns a next_steps hint with the
    correct wording.

    Actions:
    - "start": Begin a delegation (requires agent, task). Returns immediately with
      {"task_id", "status": "running", "next_steps"} — does NOT wait for completion.
    - "list": List in-flight/finished delegations (task_id, agent, status).
    - "get": Get a delegation's status + result/error (requires task_id).
    - "stop": Cancel a running delegation (requires task_id).

    Args:
        action: start | list | get | stop.
        agent: Agent slug to delegate to (for start).
        task: The one-off task, in plain language (for start).
        task_id: Delegation id returned by start (for get/stop).
        risk_limits: Per-delegation risk caps override (for start; trading agents
            only). Keys: max_position_size_quote, max_open_executors,
            max_drawdown_pct, shutdown_drawdown_pct. Replaces the agent baseline.

    Returns:
        Action-specific result dict.
    """
    return await delegate_tool.delegate(action, agent, task, task_id, risk_limits)


@mcp.tool()
@handle_errors("send notification")
async def send_notification(
    text: str,
    parse_mode: str = "Markdown",
) -> dict:
    """Send a Telegram message to the user.

    Args:
        text: Message text to send.
        parse_mode: Telegram parse mode ("Markdown" or "HTML"). Default: "Markdown".

    Returns:
        {"sent": true} on success, {"error": "..."} on failure.
    """
    return await notification.send_notification(text, parse_mode)


@mcp.tool()
@handle_errors("get notifications")
async def get_notifications(
    limit: int = 30,
    since_ts: float | None = None,
    agent_id: str | None = None,
) -> dict:
    """Read recent Condor notifications from the outbox (oldest first).

    Every user-facing notification (session ticks, delegation results,
    agent pings) is recorded in the outbox regardless of Telegram
    delivery. Use this to catch up on what agents reported — e.g. after
    starting a session or delegation from this harness — or to poll for
    new entries by passing the last seen ``ts`` as ``since_ts``.

    Args:
        limit: Max entries returned.
        since_ts: Only entries with ts strictly greater than this.
        agent_id: Filter to one run (session or delegation id).

    Returns:
        {"notifications": [{ts, user_id, agent_id, kind, text, ...}]}
    """
    return await notification.get_notifications(limit, since_ts, agent_id)


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
    - "run": Execute a one-shot routine and return its result (requires name, optional config)
    - "start": Start a continuous routine as a background task (requires name, optional config)
    - "stop": Stop a running routine instance (requires name=instance_id)
    - "list_instances": List all running/scheduled routine instances

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
        name: Routine name (required for all except list/list_instances). For "stop", pass the instance_id as name.
        config: Config overrides for run/start (optional, merged with defaults).
        agent_slug: Target agent for agent-local routine CRUD operations.
        code: Python source code for create_routine / edit_routine.

    Returns:
        Action-specific result dict.
    """
    return await routines.manage_routines(action, name, config, agent_slug, code)


@mcp.tool()
@handle_errors("manage servers")
async def manage_servers(
    action: str,
    name: str | None = None,
) -> dict:
    """Manage Hummingbot API servers (list, check status).

    Actions:
    - "list": List all accessible servers with permissions and active status
    - "status": Check if a server is online (optional name, defaults to active server)

    Args:
        action: The action to perform (list, status)
        name: Server name (optional for status)

    Returns:
        Action-specific result dict.
    """
    return await servers.manage_servers(action, name)


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
@handle_errors("get user context")
async def get_user_context() -> dict:
    """Get the current user's context within Condor.

    Returns:
        A dict with:
        - active_server: Currently active Hummingbot server name
        - user_role: User's role (admin, user, pending, blocked)
        - is_admin: Whether the user is an admin
    """
    return await context.get_user_context()


@mcp.tool()
@handle_errors("manage trading agent")
async def manage_trading_agent(
    action: str,
    agent_id: str | None = None,
    agent_slug: str | None = None,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    agent_key: str | None = None,
    config: dict | None = None,
    tools: list[str] | None = None,
    when_to_consult: str | None = None,
    server_required: bool | None = None,
    server_name: str | None = None,
    risk_limits: dict | None = None,
    denomination: str | None = None,
    default_config: dict | None = None,
    default_trading_context: str | None = None,
    schedule: dict | None = None,
) -> dict:
    """Manage trading agents (definition, lifecycle, routines, monitoring).

    An *agent* (e.g. "executor_manager", "brigado") is an identity defined in
    agents/{slug}/AGENT.md — the ONE spec and the agent "brain": identity +
    strategy body (instructions) + launch defaults (default_config) + risk
    baseline (risk_limits + denomination) + optional schedule. It is distinct
    from a running *instance*. Capability is DERIVED, not flagged: an agent
    with ``when_to_consult`` is consultable (on any model); the same AGENT.md
    is what a session loops. There is NO separate strategy object.
    Running sessions are identified as "{agent_slug}_{N}" ("{agent_slug}_e{N}"
    for experiments); all history (sessions, learnings, experiments) lives at
    the agent level.

    Actions -- Agents (identities):
    - "list_agent_definitions": List all agents (AGENT.md identities) with their
      capabilities — consultable (can be used via the `consult` tool),
      when_to_consult, can_trade, denomination, schedule, agent_key, tools. Use
      this to answer "what agents exist?" — list_agents (instances) does NOT
      show idle or consult-only agents.
    - "create_agent": Create a new agent (AGENT.md identity + brain). Requires name.
      Optional: description, instructions (the AGENT.md body — identity + domain
      knowledge + strategy), agent_key, tools (tool-name allowlist for pydantic-ai
      consults), when_to_consult (set it to make the agent consultable —
      recommended for every agent), server_required, server_name, risk_limits +
      denomination, default_config, default_trading_context, schedule.
      NOTE: a server-backed agent (server_required, the default) or one whose
      tools include manage_executors MUST declare risk_limits, and risk_limits
      always require a denomination. Returns agent_slug.
    - "get_agent": Get full agent definition including the AGENT.md body (requires agent_slug)
    - "update_agent": Update an agent's AGENT.md / metadata (requires agent_slug, plus fields to change)
    - "delete_agent": Delete (tombstone) an agent (requires agent_slug). Refused
      while it has running sessions or open executors; its history stays
      readable and the slug is reserved forever.

    Actions -- Lifecycle:
    - "list_agents": List all running agent instances with status
    - "start_session": Start a new agent SESSION — the stateful unit of capital
      engagement: frozen config, journal, risk state, its own track-record entry
      (requires agent_slug; optional config overrides on top of the agent's
      default_config, e.g. execution_mode "run_once" for a single live tick)
    - "start_experiment": Run ONE simulated tick with every mutation blocked —
      a.k.a. a dry run — saved as a flat experiment snapshot, never a session
      (requires agent_slug; same config args as start_session)
    - "stop_agent": Stop a running agent, KEEPING its open positions (requires agent_id)
    - "shutdown_agent": Emergency stop that WINDS DOWN this session's positions/executors
      per its shutdown.md policy (closes perp, keeps spot by default) (requires agent_id)
    - "pause_agent": Pause a running agent (requires agent_id)
    - "resume_agent": Resume a paused agent (requires agent_id)

    Actions -- Routines (scoped to an agent):
    - "list_routines": List global + agent-local routines for an agent (requires agent_slug)
    - "run_routine": Execute a one-shot routine (requires agent_slug, name, optional config)

    Journal reads/writes are the dedicated trading_agent_journal_read /
    trading_agent_journal_write tools, not actions of this tool.

    Actions -- Monitoring:
    - "agent_tracker": Get the full tracker markdown (tick history, executor ledger, snapshots) (requires agent_id)
    - "agent_journal": Get recent journal entries and learnings (requires agent_id)

    Args:
        action: The action to perform.
        agent_id: Agent session ID "{agent_slug}_{N}" (for lifecycle/monitoring/journal actions).
        agent_slug: Agent slug — required for start_session/start_experiment,
            routine actions, and the agent CRUD actions get/update/delete_agent.
        name: Agent name (create/update_agent) or routine name (run_routine).
        description: Agent description (create/update_agent).
        instructions: AGENT.md body (create/update_agent) — identity + domain knowledge + strategy.
        agent_key: Default LLM. Examples: "claude-code", "gemini", "copilot", "ollama:llama3.1", "ollama:qwen3:32b", "groq:llama-3.3-70b-versatile". Any model can be consulted; a pydantic-ai key (e.g. "ollama:...") additionally enforces the tools allowlist on consult. Default "claude-code".
        config: Launch config overrides (start_session/start_experiment) or routine config (run_routine).
            For start_session, supports: agent_key (override agent default), model_base_url (for LM Studio/vLLM),
            execution_mode, frequency_sec, total_amount_quote, trading_context, risk_limits (stricter-only), server_name, max_ticks.
        tools: Tool-name allowlist for the agent (create/update_agent). Empty/None = unrestricted.
        when_to_consult: Trigger describing when to consult the agent (create/update_agent). Set it to make the agent consultable — recommended for every agent, on any model.
        server_required: Whether the agent needs a Hummingbot server (create/update_agent). Default True.
        server_name: Pin the agent to a specific hummingbot-api server (create/update_agent). When set, the agent's mcp-hummingbot subprocess and any session it runs use THIS server regardless of the chat's active server. Empty/None = follow the ambient chat server.
        risk_limits: Agent-level risk baseline dict (create/update_agent). Keys:
            max_position_size_quote, max_open_executors, max_drawdown_pct,
            shutdown_drawdown_pct. Governs unattended delegations and is the
            baseline for tick sessions (launch overrides may only tighten it).
            REQUIRED for server-backed/trading agents; use
            {"max_position_size_quote": 0, "max_open_executors": 0} for a
            read-only agent that must never trade.
        denomination: Numeraire the risk_limits are expressed in, e.g. "USDC",
            "SOL", "USD" (create/update_agent). REQUIRED whenever risk_limits
            are declared.
        default_config: Launch defaults baked into the AGENT.md (create/update_agent) —
            AgentConfig keys: frequency_sec, total_amount_quote, execution_mode, max_ticks, ...
        default_trading_context: Default trading context injected when a launch
            passes none (create/update_agent).
        schedule: Optional unattended schedule (create/update_agent), e.g.
            {"cron": "0 * * * *", "tz": "UTC"}.

    Returns:
        Action-specific result dict.
    """
    return await trading_agent.manage_trading_agent(
        action,
        agent_id=agent_id,
        agent_slug=agent_slug,
        name=name,
        description=description,
        instructions=instructions,
        agent_key=agent_key,
        config=config,
        tools=tools,
        when_to_consult=when_to_consult,
        server_required=server_required,
        server_name=server_name,
        risk_limits=risk_limits,
        denomination=denomination,
        default_config=default_config,
        default_trading_context=default_trading_context,
        schedule=schedule,
    )


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


@mcp.tool()
@handle_errors("manage notes")
async def manage_notes(
    action: str,
    key: str | None = None,
    value: str | None = None,
) -> dict:
    """DEPRECATED — use manage_memory instead.

    Thin alias kept for one release: "set"->write (type="reference"), "get"->read,
    "list"->list, "delete"->delete. New code should call manage_memory directly.

    Actions:
    - "list": List all saved notes
    - "get": Get a specific note (requires key)
    - "set": Save a note (requires key and value)
    - "delete": Delete a note (requires key)

    Args:
        action: The action to perform (list, get, set, delete)
        key: The note key (required for get, set, delete)
        value: The note value (required for set)

    Returns:
        Action-specific result dict.
    """
    return await notes.manage_notes(action, key, value)


# ---------------------------------------------------------------------------
# Trading-agent journal tools — the canonical interface live tick prompts call
# directly (see condor/agents/prompts.py). Kept as dedicated top-level tools
# rather than manage_trading_agent actions so the agent's ergonomic, oft-used
# write path is a single named tool.
# ---------------------------------------------------------------------------


@mcp.tool()
@handle_errors("journal read")
async def trading_agent_journal_read(
    agent_id: str,
    section: str = "recent",
    max_entries: int = 30,
) -> dict:
    """Read the trading agent's journal.

    Args:
        agent_id: The trading agent instance ID.
        section: What to read:
                 "recent" (last 10 decisions from run snapshots),
                 "learnings" (all learnings, max 20),
                 "summary" (current status one-liner),
                 "state" (alias for summary),
                 "full" (entire journal),
                 "runs" (list recent run snapshots),
                 "run:N" (read specific run snapshot, e.g. "run:3").
        max_entries: Max entries for recent/runs (default 30).

    Returns:
        {"content": "<journal text>"} or {"runs": [...]} for runs listing.
    """
    return trading_agent.journal_read(agent_id, section, max_entries)


@mcp.tool()
@handle_errors("journal write")
async def trading_agent_journal_write(
    agent_id: str,
    entry_type: str,
    text: str,
    reasoning: str = "",
    risk_note: str = "",
    tick: int = 0,
    category: str = "",
) -> dict:
    """Write to the trading agent's journal. Keep entries SHORT (one line).

    Args:
        agent_id: The trading agent instance ID ("{slug}_{N}"). For
            entry_type="promote_learning" the bare agent slug also works —
            learnings live at the agent level, no session handle needed.
        entry_type: "action", "learning", "state", or "promote_learning".
            - "action": What you did this tick (auto-trimmed to last 10).
            - "learning": A new insight. Duplicates are auto-filtered. Only write
              if this is genuinely new and not already in learnings (max 20).
            - "state": Overwrite the current state snapshot (e.g. price, position, grids).
            - "promote_learning": Move an existing learning to the Promoted
              section after folding it into a skill (text must match the line).
        text: The entry content. Keep it to ONE short line.
        reasoning: One-sentence reasoning (for actions only).
        risk_note: Optional risk note (for actions only).
        tick: Current tick number (for actions only).
        category: Learning category: "market" (observations, patterns, volatility)
            or "execution" (errors, fills, timing). Only used when entry_type="learning".
            Defaults to "market".

    Returns:
        {"written": true}
    """
    return trading_agent.journal_write(
        agent_id,
        entry_type,
        text,
        reasoning,
        risk_note,
        tick,
        category,
    )


if __name__ == "__main__":
    mcp.run()

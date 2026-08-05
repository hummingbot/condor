"""Condor MCP Server -- exposes Condor capabilities to AI agents.

Thin wrapper layer: tool registration + docstrings only.
All business logic lives in mcp_servers.condor.tools.*
"""

from mcp.server.fastmcp import FastMCP

from mcp_servers.condor.middleware import handle_errors
from mcp_servers.condor.tools import available_models as available_models_tool
from mcp_servers.condor.tools import consult as consult_tool
from mcp_servers.condor.tools import context
from mcp_servers.condor.tools import delegate as delegate_tool
from mcp_servers.condor.tools import (
    memory,
    notes,
    notification,
    routines,
    servers,
    skills,
    trading_agent,
)


_CHAT_ROUTINES_RULE = (
    "- ROUTINES ARE SPECIAL: any request to CREATE, EDIT, FIX, DEBUG, or "
    "design a routine MUST go to a background Condor worker — "
    '`delegate(action="start", agent="condor", task="build a routine that …")`. '
    "It returns a task_id immediately (you are NOT blocked), reads the "
    "`routine_cookbook` playbook, writes and TESTS the routine, and pings the "
    "user with the result. Tell the user it is running in the background. Do NOT "
    "write routine code yourself and do NOT hand-roll it with raw "
    "`manage_routines` create_routine/edit_routine. (RUNNING an existing routine "
    'is not authoring — for that just call `manage_routines(action="run", '
    'name="...")`.)\n'
)

# The worker IS Condor — same agent record, same tools — so the routing text is
# the coordinator's, with exactly one branch swapped: authoring is the reason it
# was started, so it does the work instead of handing it on (FEAT-032).
_WORKER_ROUTINES_RULE = (
    "- ROUTINE AUTHORING IS YOURS: creating, editing, fixing and debugging "
    "routines is the work you were started for — do NOT hand it to anyone else. "
    'Read `manage_skill(action="read", name="routine_cookbook")` FIRST (and the '
    "companion file for what the routine does), create it into the GLOBAL "
    'routine library with `manage_routines(action="create_routine", name="...", '
    'code="...")` — no `strategy_id`, so the user and the chat can see it — then '
    'TEST it with `manage_routines(action="run", name="...")` and fix it until '
    "the output is clean BEFORE reporting. Reporting an untested routine is a "
    "failed delegation. (RUNNING an existing routine is not authoring — for that "
    'just call `manage_routines(action="run", name="...")`.)\n'
)


def _chat_base() -> str:
    """Routing rules for the Condor coordinator — the unbound chat assistant."""
    return _coordinator_base(_CHAT_ROUTINES_RULE)


def _worker_base() -> str:
    """Routing rules for a BACKGROUND Condor worker (``--delegate-worker``).

    The chat and the worker resolve the same agent record (FEAT-033), so this is
    ``_chat_base()`` with the routines branch swapped for "authoring is yours",
    plus the framing that makes an unattended session behave: it has no user to
    ask, and it must not fan out into more delegations. The no-delegation line is
    a courtesy — the actual stop is in ``tools/delegate.py``, because a prompt is
    not a guard.
    """
    return (
        "You are a BACKGROUND WORKER instance of Condor: a detached session that "
        "`delegate` started to carry ONE task to completion, unattended. There is "
        "no user in the loop to ask — make the reasonable call, do the work "
        "yourself, and report what you actually did and verified.\n"
        "You must NEVER start another delegation: "
        '`delegate(action="start", ...)` is refused for you in code. Finish the '
        "task in this session (polling with "
        '`delegate(action="get"/"list")` is fine).\n\n'
    ) + _coordinator_base(_WORKER_ROUTINES_RULE)


def _coordinator_base(routines_rule: str) -> str:
    """The coordinator routing text, parameterized on its ROUTINES branch."""
    return (
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
        f"{routines_rule}"
        "- Only fall back to raw tools when nothing matches.\n"
        "Anti-pattern: answering a domain request (deploy/tune an executor, analyze "
        "logs, author a routine) with a chain of raw `mcp-hummingbot`/`manage_*` "
        "calls when a skill or agent covers it.\n"
        'Discover more anytime with `manage_skill(action="list")`.'
    )


def _agent_base(slug: str, name: str) -> str:
    """Routing rules for a subprocess launched AS an Agent (``--agent-slug``).

    Same three-tier priority, read from the specialist's seat: its skills are
    its own playbooks (plus the ones Condor publishes), routine authoring is its
    own work, and consulting is for work outside its domain — never for itself.
    """
    from condor.agents.agent import identity_header

    # FEAT-031: authoring is the agent's own work now that `routine_cookbook` is
    # inherited from Condor's library — the knowledge that used to justify a
    # round-trip to a dedicated builder agent travels with the agent itself. The
    # chat reaches the same playbook the other way, through a background worker
    # (FEAT-032), so nothing routes to a builder any more.
    routines_rule = (
        "- ROUTINE AUTHORING IS YOURS: creating, editing, fixing and debugging "
        "your own routines is your work — do NOT hand it to another agent. Read "
        '`manage_skill(action="read", name="routine_cookbook")` FIRST (and the '
        "companion file for what the routine does), create it into your own dir "
        'with `manage_routines(action="create_routine", name="...", code="...")`, '
        'then TEST it with `manage_routines(action="run", name="...")` and fix '
        "until the output is clean before reporting.\n"
    )
    return (
        f"{identity_header(slug, name)}\n\n"
        "ROUTING RULE — you are the specialist, so domain work is yours to do. "
        "Before reaching for raw tools (including tools from other connected MCP "
        "servers such as mcp-hummingbot):\n"
        "- Your own SKILLS below are playbooks YOU follow: read one with "
        '`manage_skill(action="read", name="<name>")` and follow its steps. When '
        'it links a routine (shown as "→ routine: X"), run that routine via '
        '`manage_routines(action="run", name="X", config={})` instead of '
        "reimplementing it by hand.\n"
        f"{routines_rule}"
        "- You MAY consult a PEER agent listed below for work outside your own "
        'domain (`consult(agent="<slug>", task="...", context="...")`, or '
        '`delegate(action="start", agent="<slug>", task="...")` for a long '
        "background task). Say plainly that you are handing it over rather than "
        "answering outside your competence.\n"
        "- Only fall back to raw tools when nothing matches.\n"
        'Discover your own playbooks anytime with `manage_skill(action="list")`.'
    )


def _build_instructions() -> str:
    """Server-level instructions surfaced to the MCP host on connect.

    An external MCP client (Claude Code, Cursor, …) only receives a flat list of
    tool names — it never sees Condor's skills/agents indexes, which are injected
    only into the in-bot `/agent` brain prompt. Without this, the host reaches for
    whatever obvious tool is in scope (e.g. a raw `manage_bots`) instead of the
    matching Condor playbook. We embed the live indexes here so any host can route
    a request to the right skill/agent. Built once at import; cheap and read-only.

    One rule governs all three sections: **this text describes the assistant this
    subprocess belongs to**. Under ACP v1 there is no system-prompt channel
    (`session/new` carries only cwd + mcpServers), so these instructions are the
    strongest frame Condor controls — which is why a subprocess launched with
    ``--agent-slug`` must read its own identity here and not the coordinator's
    (FEAT-025).

    The chat's own subprocess now carries ``--agent-slug condor`` (FEAT-033), so
    the branch reads ``specialist_slug``: keyed on the raw slug it would serve
    Condor the specialist framing — including ``identity_header``'s "You are NOT
    Condor", which would be false — and nothing would error, the answers would
    just quietly get worse.

    A third seat exists for the same record: ``--delegate-worker`` (FEAT-032)
    marks the detached Condor that ``delegate`` starts to author a routine. It
    reads ``_worker_base`` — the coordinator text with authoring made its own job
    and delegation closed off.
    """
    from mcp_servers.condor.settings import settings

    slug = settings.specialist_slug
    agent = None
    if slug:
        try:
            from condor.agents.agent import AgentStore

            agent = AgentStore().get(slug)
        except Exception:
            pass  # Unknown/unreadable slug degrades to the coordinator text.

    if agent:
        base = _agent_base(slug, agent.name)
    elif settings.delegate_worker:
        # Same record as the chat, different seat (FEAT-032): a specialist already
        # reads `_agent_base`, so the flag only ever re-frames Condor itself.
        base = _worker_base()
    else:
        base = _chat_base()
    sections = [base]
    try:
        from condor.memory import SkillStore

        # Scope to the launched assistant: an agent subprocess (--agent-slug) must
        # advertise ITS OWN skills here, not the chat condor's global library.
        skills_index = SkillStore(slug or None).list_index()
        if skills_index:
            sections.append(
                "[SKILLS — read the playbook before a matching flow]\n" + skills_index
            )
    except Exception:
        pass  # Advisory — never block server startup on index assembly.
    try:
        from condor.agents.agent import AgentStore
        from condor.memory.paths import CHAT_SLUG

        # Never a peer of itself (the bug this fixes), and never a peer of the
        # coordinator: Condor is in the registry now (FEAT-033), and a
        # specialist offered it could consult back into the chat.
        exclude = {CHAT_SLUG} | ({agent.slug} if agent else set())
        agents_index = AgentStore().list_index(exclude=exclude)
        if agents_index:
            header = (
                "[PEER AGENTS — consult for work outside your domain]"
                if agent
                else "[AGENTS — consult for domain work]"
            )
            sections.append(f"{header}\n{agents_index}")
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
) -> dict:
    """Delegate a one-off task to a background agent instance.

    DELEGATE is the async, unattended sibling of CONSULT. Where ``consult`` blocks
    and returns an answer now (mutations human-gated), ``delegate`` hands a
    goal-oriented task to a DETACHED agent that works autonomously until done, then
    notifies the user with the result — while you stay free to do other things. Use
    it for "go build/scan/produce X and ping me when finished" (e.g. "create a
    routine that scans SOL pools"). The agent runs unrestricted with full
    auto-approve, so delegate only to trusted agents/tasks.

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

    Returns:
        Action-specific result dict.
    """
    return await delegate_tool.delegate(action, agent, task, task_id)


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
@handle_errors("manage routines")
async def manage_routines(
    action: str,
    name: str | None = None,
    config: dict | None = None,
    strategy_id: str | None = None,
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

    Actions -- Agent-Local Routine CRUD (requires strategy_id or CONDOR_AGENT_SLUG):
    - "create_routine": Create a new agent-local routine (requires name, code)
    - "read_routine": Read source code of a routine (requires name)
    - "edit_routine": Update an agent-local routine (requires name, code)
    - "delete_routine": Delete an agent-local routine (requires name)

    Agent-local routines live in agents/{slug}/routines/ and are only
    visible to that strategy's agent. They follow the same pattern as global
    routines: a Config(BaseModel) class and an async run(config, context) function.

    Args:
        action: The action to perform.
        name: Routine name (required for all except list/list_instances). For "stop", pass the instance_id as name.
        config: Config overrides for run/start (optional, merged with defaults).
        strategy_id: Strategy ID for agent-local routine CRUD operations.
        code: Python source code for create_routine / edit_routine.

    Returns:
        Action-specific result dict.
    """
    return await routines.manage_routines(action, name, config, strategy_id, code)


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
@handle_errors("get available models")
async def get_available_models(
    openrouter_query: str = "", openrouter_limit: int = 20
) -> dict:
    """List every model/provider currently usable for a trading agent's ``agent_key``.

    Use this when helping the user pick a model for an agent they are building
    (agent_builder skill) — recommend from what is ACTUALLY configured, not a
    hardcoded default. Read-only; never returns key values.

    Args:
        openrouter_query: Optional substring to filter the OpenRouter catalog by
            slug or name (e.g. "claude", "deepseek", "free"). Empty = no filter.
        openrouter_limit: Max OpenRouter models to return (default 20, cheapest
            input price first). ``total_matching`` reports how many matched.

    Returns a dict with:
      - acp_clis: subscription/CLI bridges (claude-code, gemini, copilot, …),
        each with ``agent_key`` and ``available`` — the CLI is installed and
        launchable. ``available`` does NOT mean signed in: every bridge needs its
        own interactive login (Claude/Google/GitHub/OpenAI) that cannot be probed
        from here. Treat a bridge as a candidate to CONFIRM with the user, and
        prefer a credential you can verify (a set ``cloud_keys`` provider, or a
        loaded local model) when recommending unprompted.
      - cloud_keys: {provider: bool} — whether OPENROUTER/OPENAI/ANTHROPIC/GROQ/
        GOOGLE keys are set in the environment.
      - custom_endpoints: the user's own saved OpenAI-compatible endpoints, each
        with ``name``, ``base_url``, ``reachable`` and the chat ``models`` it
        serves (``agent_key`` set to ``custom@<endpoint>:<model-id>``). The
        strongest signal here — the user added these deliberately and they are
        re-validated on every call — so prefer a reachable one when it fits.
      - local: {ollama, lmstudio} each with ``base_url``, ``reachable``, and the
        ``models`` currently loaded on that server (empty if not running).
      - openrouter: tool-capable catalog (``models`` with ``agent_key`` set to
        ``openrouter:<slug>``, plus in/out $/Mtok and context). The catalog is
        public so this is ALWAYS present — no key needed to recommend. Check
        ``key_present``: false means these are options that need OPENROUTER_API_KEY
        added (Settings) before they can run; recommend a runnable option first.

    Guidance for choosing: correctness-critical or high-capital agents → a strong
    model (a capable OpenRouter model when its key is set, or a subscription ACP
    bridge the user confirms is signed in); simple
    report/watch loops or privacy/offline needs → a loaded local model or a cheap
    OpenRouter one. Only pydantic-ai keys (openrouter:/ollama:/lmstudio:/openai:/
    groq:) enforce an agent's ``tools`` allowlist; ACP bridges run unrestricted.
    """
    return await available_models_tool.get_available_models(
        openrouter_query, openrouter_limit
    )


@mcp.tool()
@handle_errors("manage trading agent")
async def manage_trading_agent(
    action: str,
    agent_id: str | None = None,
    strategy_id: str | None = None,
    agent_slug: str | None = None,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    agent_key: str | None = None,
    skills: list[str] | None = None,
    config: dict | None = None,
    tools: list[str] | None = None,
    when_to_consult: str | None = None,
    server_required: bool | None = None,
    server_name: str | None = None,
) -> dict:
    """Manage trading agents and strategies.

    An *agent* (e.g. "executor_manager", "brigado") is an identity defined in
    agents/{slug}/AGENT.md — the primary artifact and the agent "brain". It is
    distinct from a *strategy* (a looping playbook it owns) and from a running
    *instance*. EVERY agent can be consulted (`consult`), delegated to (`delegate`)
    and looped (`start_agent`) from the moment it is created — there is no
    capability flag and nothing to enable. An agent that owns no strategy loops a
    default playbook built from its own identity. Create the agent FIRST, then add
    its routines and (optionally) a bespoke strategy. ``strategy_id`` is the opaque
    key returned by list_strategies/create_strategy (form "agent_slug.strategy_slug").

    Actions -- Agents (identities):
    - "list_agent_definitions": List all agents (AGENT.md identities) with their
      when_to_consult hint, owned strategies, agent_key and tools. Use this to
      answer "what agents exist?" — list_strategies and list_agents (instances) do
      NOT show agents that own no loop strategy.
    - "create_agent": Create a new agent (AGENT.md identity + brain). Requires name.
      Optional: description, instructions (the AGENT.md body — identity + domain
      knowledge), agent_key, tools (tool-name allowlist for pydantic-ai consults),
      when_to_consult (routing hint), server_required. Returns agent_slug — use it
      for routines/strategies.
    - "get_agent": Get full agent definition including the AGENT.md body (requires agent_slug)
    - "update_agent": Update an agent's AGENT.md / metadata (requires agent_slug, plus fields to change)
    - "delete_agent": Delete an agent (requires agent_slug; refuses if it still owns strategies)

    Actions -- Strategies:
    - "list_strategies": List all strategies (across agents)
    - "get_strategy": Get full strategy details including instructions (requires strategy_id)
    - "create_strategy": Create a new strategy under an Agent (requires agent_slug, name, instructions)
    - "update_strategy": Update an existing strategy (requires strategy_id, plus fields to update)
    - "delete_strategy": Delete a strategy (requires strategy_id)

    Actions -- Lifecycle:
    - "list_agents": List all running agent instances with status
    - "start_agent": Start a new agent session (requires strategy_id, optional config
      overrides). strategy_id may also be a BARE AGENT SLUG — the agent then loops its
      only strategy, or a default playbook created from its identity on first start.
    - "stop_agent": Stop a running agent, KEEPING its open positions (requires agent_id)
    - "shutdown_agent": Emergency stop that WINDS DOWN this session's positions/executors
      per its shutdown.md policy (closes perp, keeps spot by default) (requires agent_id)
    - "pause_agent": Pause a running agent (requires agent_id)
    - "resume_agent": Resume a paused agent (requires agent_id)

    Actions -- Routines (scoped to a strategy):
    - "list_routines": List global + agent-local routines for a strategy (requires strategy_id)
    - "run_routine": Execute a one-shot routine (requires strategy_id, name, optional config)

    Journal reads/writes are the dedicated trading_agent_journal_read /
    trading_agent_journal_write tools, not actions of this tool.

    Actions -- Monitoring:
    - "agent_tracker": Get the full tracker markdown (tick history, executor ledger, snapshots) (requires agent_id)
    - "agent_journal": Get recent journal entries and learnings (requires agent_id)

    Args:
        action: The action to perform.
        agent_id: Agent instance ID (for lifecycle/monitoring/journal actions).
        strategy_id: Strategy key "agent_slug.strategy_slug" (for strategy/routine/start
            actions). For start_agent a bare agent slug also works — see start_agent.
        agent_slug: Owning Agent slug — required for create_strategy and for the
            agent CRUD actions get_agent/update_agent/delete_agent.
        name: Agent name (create_agent), strategy name (create/update_strategy), or routine name (run_routine).
        description: Agent or strategy description (for create/update).
        instructions: AGENT.md body (create/update_agent) or strategy instructions text (create/update_strategy).
        agent_key: Default LLM. Examples: "claude-code", "gemini", "copilot", "ollama:llama3.1", "ollama:qwen3:32b", "groq:llama-3.3-70b-versatile". Any model can be consulted; a pydantic-ai key (e.g. "ollama:...") additionally enforces the tools allowlist on consult. Default "claude-code".
        skills: List of optional skill names to enable (for create/update_strategy).
        config: Agent config overrides (for create/update_strategy/start) or routine config (for run_routine).
            For start_agent, supports: agent_key (override strategy default), model_base_url (for LM Studio/vLLM),
            execution_mode, frequency_sec, total_amount_quote, trading_context, risk_limits, server_name, max_ticks.
        tools: Tool-name allowlist for the agent (create/update_agent). Empty/None = unrestricted.
        when_to_consult: One-line hint describing when to route work to this agent (create/update_agent). Purely for routing — every agent is consultable with or without it; it falls back to the description.
        server_required: Whether the agent needs a Hummingbot server (create/update_agent). Default True.
        server_name: Pin the agent to a specific hummingbot-api server (create/update_agent). When set, the agent's mcp-hummingbot subprocess and any strategy it deploys use THIS server regardless of the chat's active server. Empty/None = follow the ambient chat server.

    Returns:
        Action-specific result dict.
    """
    return await trading_agent.manage_trading_agent(
        action,
        agent_id,
        strategy_id,
        agent_slug,
        name,
        description,
        instructions,
        agent_key,
        skills,
        config,
        tools=tools,
        when_to_consult=when_to_consult,
        server_required=server_required,
        server_name=server_name,
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
    when_to_use: str | None = None,
    body: str | None = None,
    references_routine: str | None = None,
    query: str | None = None,
    max_entries: int = 30,
    strategy_id: str | None = None,
    file: str | None = None,
    content: str | None = None,
    shared: bool | None = None,
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

    Skills are scoped per-assistant: a launched agent WRITES only to its own
    library. From the chat you can target a specific agent's local skill library
    with strategy_id (an "agent_slug.strategy_slug" key, or a bare agent slug) —
    use this to author or inspect an agent's skills while building it. Without
    strategy_id the current assistant's library is used.

    An agent also READS the playbooks Condor publishes: a chat skill created with
    shared=True appears in every agent's index as if it were its own (marked
    `inherited` on read) — that is how a playbook like `routine_cookbook` reaches
    every agent. Inherited skills are read-only: to specialize one, "create" a
    local skill with the SAME name and it shadows the published one. Publish
    deliberately — a shared skill lands in every agent's context.

    Actions:
    - "read": Get a full playbook + routine validation + companion `files` (requires name).
    - "read_file": Get the contents of one bundled companion file (requires name + file).
    - "write_file": Create/overwrite one bundled companion file (requires name + file + content).
    - "search": Keyword search over the skills (requires query).
    - "list": Return the skills index (one line per skill).
    - "create": Add/overwrite a skill (requires name, description, when_to_use, body).
    - "edit": Patch fields of a skill (requires name + any of description/when_to_use/body/references_routine).
    - "delete": Remove a skill (requires name).

    Args:
        action: read | read_file | write_file | search | list | create | edit | delete
        name: Short kebab/snake name (e.g. "grid-en-band-walk").
        description: One-line summary (create/edit).
        when_to_use: The trigger/condition for the playbook (create/edit).
        body: The steps / playbook text (create/edit).
        references_routine: Optional routine name to link; "" clears it (create/edit).
        query: Search string (for search).
        max_entries: Cap for search results (default 30).
        strategy_id: Target a specific agent's local skill library (chat-side
            authoring). Composite "agent_slug.strategy_slug" key or bare agent slug.
        file: Bare name of a bundled companion file (for read_file/write_file).
        content: Full contents to write to the companion file (for write_file).
        shared: Publish this playbook to every agent (create/edit). Only honored
            in Condor's own library — ignored when targeting an agent's. Omit to
            leave publication unchanged.

    Returns:
        Action-specific result dict.
    """
    return await skills.manage_skill(
        action,
        name=name,
        description=description,
        when_to_use=when_to_use,
        body=body,
        references_routine=references_routine,
        query=query,
        max_entries=max_entries,
        strategy_id=strategy_id,
        file=file,
        content=content,
        shared=shared,
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
        agent_id: The trading agent instance ID.
        entry_type: "action", "learning", or "state".
            - "action": What you did this tick (auto-trimmed to last 10).
            - "learning": A new insight. Duplicates are auto-filtered. Only write
              if this is genuinely new and not already in learnings (max 20).
            - "state": Overwrite the current state snapshot (e.g. price, position, grids).
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

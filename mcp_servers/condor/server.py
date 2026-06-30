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
        "from other connected MCP servers such as mcp-hummingbot), check whether a "
        "Condor skill or agent matches it:\n"
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
) -> dict:
    """Delegate a one-off task to a background agent instance.

    DELEGATE is the async, unattended sibling of CONSULT. Where ``consult`` blocks
    and returns an answer now (mutations human-gated), ``delegate`` hands a
    goal-oriented task to a DETACHED agent that works autonomously until done, then
    notifies the user with the result — while you stay free to do other things. Use
    it for "go build/scan/produce X and ping me when finished" (e.g. "create a
    routine that scans SOL pools"). The agent runs unrestricted with full
    auto-approve, so delegate only to trusted agents/tasks.

    Actions:
    - "start": Begin a delegation (requires agent, task). Returns immediately with
      {"task_id", "status": "running"} — does NOT wait for completion.
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
) -> dict:
    """Manage trading agents and strategies.

    An *agent* (e.g. "executor_manager", "brigado") is an identity defined in
    agents/{slug}/AGENT.md — the primary artifact and the agent "brain". It is
    distinct from a *strategy* (a looping playbook it owns) and from a running
    *instance*. Capability is DERIVED, not flagged: an agent with ``when_to_consult``
    is consultable (on any model); an agent that owns ≥1 strategy is loopeable; it can
    be both. Create the agent FIRST, then add its routines and (optionally) a strategy.
    ``strategy_id`` is the opaque key returned by list_strategies/create_strategy
    (form "agent_slug.strategy_slug").

    Actions -- Agents (identities):
    - "list_agent_definitions": List all agents (AGENT.md identities) with their
      capabilities — consultable (can be used via the `consult` tool),
      when_to_consult, loopable, owned strategies, agent_key, tools. Use this to
      answer "what agents exist?" — list_strategies and list_agents (instances) do
      NOT show consult-only agents (those that own no loop strategy).
    - "create_agent": Create a new agent (AGENT.md identity + brain). Requires name.
      Optional: description, instructions (the AGENT.md body — identity + domain
      knowledge), agent_key, tools (tool-name allowlist for pydantic-ai consults),
      when_to_consult (set it to make the agent consultable — recommended for every
      agent), server_required. Returns agent_slug — use it for routines/strategies.
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
    - "start_agent": Start a new agent session (requires strategy_id, optional config overrides)
    - "stop_agent": Stop a running agent (requires agent_id)
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
        strategy_id: Strategy key "agent_slug.strategy_slug" (for strategy/routine/start actions).
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
        when_to_consult: Trigger describing when to consult the agent (create/update_agent). Set it to make the agent consultable — recommended for every agent, on any model.
        server_required: Whether the agent needs a Hummingbot server (create/update_agent). Default True.

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

    Skills are scoped per-assistant: a launched agent reads/writes ONLY its own
    library. From the chat you can target a specific agent's local skill library
    with strategy_id (an "agent_slug.strategy_slug" key, or a bare agent slug) —
    use this to author or inspect an agent's skills while building it. Without
    strategy_id the current assistant's library is used.

    Actions:
    - "read": Get a full playbook + routine validation (requires name).
    - "search": Keyword search over the skills (requires query).
    - "list": Return the skills index (one line per skill).
    - "create": Add/overwrite a skill (requires name, description, when_to_use, body).
    - "edit": Patch fields of a skill (requires name + any of description/when_to_use/body/references_routine).
    - "delete": Remove a skill (requires name).

    Args:
        action: read | search | list | create | edit | delete
        name: Short kebab/snake name (e.g. "grid-en-band-walk").
        description: One-line summary (create/edit).
        when_to_use: The trigger/condition for the playbook (create/edit).
        body: The steps / playbook text (create/edit).
        references_routine: Optional routine name to link; "" clears it (create/edit).
        query: Search string (for search).
        max_entries: Cap for search results (default 30).
        strategy_id: Target a specific agent's local skill library (chat-side
            authoring). Composite "agent_slug.strategy_slug" key or bare agent slug.

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

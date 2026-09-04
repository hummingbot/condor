"""Initial-context and prompt builders for agent sessions (ARCH-190).

Everything a fresh session is told before the user's first word: Condor's own
instructions, the platform's formatting rules, the server/permission picture,
and the memory/skills/agents indexes. Platform-neutral — moved here from
``handlers/agents/_shared.py``, which re-exports these names for its Telegram
callers.
"""

from __future__ import annotations

from condor.llm.options import _chat_agent
from condor.memory.paths import CHAT_SLUG

# -- Compact prompt templates --

COMPACT_PROMPT_AUTO = (
    "Please provide a concise summary of our conversation so far. Include:\n"
    "- Key decisions and conclusions reached\n"
    "- Important data points and numbers discussed\n"
    "- Current task state and any pending actions\n"
    "- User preferences or instructions given\n\n"
    "Be concise but thorough. This summary will be used to carry context into a fresh session."
)

COMPACT_PROMPT_CUSTOM_TEMPLATE = (
    "Please provide a concise summary of our conversation, focusing specifically on:\n"
    "{instructions}\n\n"
    "Drop everything else. Be concise but preserve the details requested above. "
    "This summary will be used to carry context into a fresh session."
)

COMPACT_CONTEXT_TEMPLATE = (
    "[System context -- do not repeat this to the user]\n"
    "This is a continuation of a previous conversation. "
    "Here is the summary from that session:\n\n"
    "{summary}\n\n"
    "Continue from where we left off. The user compacted the context to free up space."
)

_PAGE_CONTEXT = (
    "WHAT THE USER IS LOOKING AT (web dashboard):\n"
    "- A turn typed in the dashboard may open with a bracketed block headed\n"
    '  "[What the user is looking at right now, in the Condor dashboard...]",\n'
    "  listing Screen, About, On screen and URL. That block is real: it is\n"
    "  read from the page the user has open at the moment they hit send.\n"
    "- So you DO know what they are looking at. Asked whether you can see\n"
    "  their screen, say what is true: you get a text summary of the page and\n"
    "  the figures on it, not an image of it — you cannot see the pixels.\n"
    "- Read the block before reaching for a tool. If it already answers the\n"
    '  question ("what am I looking at?", "is this number bad?"), answer from\n'
    "  it and say which page you are reading. Re-fetch when the user wants\n"
    "  something the page does not show, or something more current.\n"
    "- It describes that one moment, not the conversation. Do not carry it\n"
    "  forward as a standing fact, and never repeat the block back verbatim.\n"
    "- A turn with no such block means the page contributed nothing (the chat\n"
    "  workspace itself does not) — then the tools are the only source.\n"
)

_WEB_FORMATTING = (
    _PAGE_CONTEXT + "\n"
    "FORMATTING (web dashboard):\n"
    "- Use Markdown freely: tables, headers, bold, code blocks, lists.\n"
    "- No message length limits, but stay concise.\n"
    "- Use tables for structured data (portfolios, prices, comparisons).\n"
    "- Use code blocks for configs, JSON, or commands.\n"
    "- CHARTS: when the answer is a numeric series or comparison (a price or\n"
    "  PnL curve, volume per venue, a distribution), draw it with a ```chart\n"
    "  fence holding one JSON object. The dashboard renders it as an\n"
    "  interactive chart:\n"
    "  ```chart\n"
    '  {"type": "line", "title": "SOL/USDC 24h", "x": "time",\n'
    '   "series": [{"key": "price", "name": "Price"}],\n'
    '   "data": [{"time": "12:00", "price": 150.1}, {"time": "16:00", "price": 152.4}]}\n'
    "  ```\n"
    '  "type" is "line", "area" or "bar"; "x" and every series "key" name\n'
    "  fields of the data rows. Max 4 series and 200 points (downsample longer\n"
    '  series). A series may set "color": "up", "down", "yellow" or a hex.\n'
    "  Chart for the shape, table for exact values; use both when both matter.\n"
    "- Respond in the user's language."
)

_TELEGRAM_FORMATTING = (
    "FORMATTING (Telegram mobile):\n"
    "- NEVER use Markdown tables. Use bullet lists or key: value lines.\n"
    "- Keep paragraphs short (2-3 sentences max).\n"
    "- Cap lists at 5-7 items.\n"
    "- Respond in the user's language."
)


def platform_formatting(platform: str = "telegram") -> str:
    """The reply-formatting rules for a platform.

    Public because a bound Agent opens the chat with its own identity context
    instead of :func:`build_initial_context`, and would otherwise be the only
    brain in the product that never hears how the surface it is speaking into
    renders a reply.
    """
    return _WEB_FORMATTING if platform == "web" else _TELEGRAM_FORMATTING


#: The ring the preload names. Attended chat seats mount ``agent`` (a bound
#: specialist) or ``full`` (the coordinator) — see :func:`toolsets.seat_profile`
#: — and ``agent`` is the common subset. What ``full`` adds on top is the admin
#: ring, which is the *server owner's* and which this very prompt goes on to
#: forbid ("do NOT call configure_server"), so naming it here would advertise
#: three tools most seats are downgraded out of anyway (SEC-252).
_CHAT_PRELOAD_PROFILE = "agent"

#: ``(MCP server name, the leaf module that says which tools it mounts)``.
#: The prefix is the ACP naming convention, ``mcp__<server>__<tool>``; the server
#: names are the ones :mod:`condor.runtime.toolsets` spawns each subprocess under.
_CHAT_MCP_SERVERS = ("condor", "mcp-hummingbot")


def _chat_mcp_tools() -> tuple[str, ...]:
    """Every MCP tool an attended chat seat has mounted, as ACP tool names.

    Derived, never restated. Under ACP these tools arrive *deferred*: a session
    sees only the names it has been told about, so a tool missing from this line
    is one that seat is unlikely to ever reach for — a keyword ``ToolSearch``
    could still surface it, but only if the model thinks to run one, and the
    whole point of the preload is that it does not have to. A hand-kept copy of
    the list therefore fails quietly and in the expensive direction, which is
    exactly how the previous two copies rotted: the pre-ARCH-190 list in
    ``handlers/agents/_shared.py`` still named five tools the servers had already
    removed, and the tuple this function replaced was missing thirteen it
    mounted, ``manage_clmm`` and ``list_orphaned_positions`` among them — both
    called by the ``recover_orphaned_position`` playbook every agent inherits.

    Names come from ``mcp_servers.*.profiles`` for the same reason
    :func:`toolsets.seat_tools` reads them there: those are leaf string modules,
    the definition site of each ring, and importing a ``server.py`` to ask would
    parse argv and build a ``FastMCP`` singleton as a side effect. The import is
    function-local to keep that dependency off ``context``'s import path.

    The tick seat keeps its own, deliberately narrower list in
    ``condor/agents/prompts.py`` — a tick must not be able to start or stop the
    loop it is running inside. Do not unify them.
    """
    from mcp_servers.condor import profiles as condor_profiles
    from mcp_servers.hummingbot_api import profiles as hummingbot_profiles

    return tuple(
        f"mcp__{server}__{name}"
        for server, module in zip(
            _CHAT_MCP_SERVERS, (condor_profiles, hummingbot_profiles)
        )
        for name in module.PROFILE_TOOLS[_CHAT_PRELOAD_PROFILE]
    )


def chat_tool_preload(agent_key: str | None) -> str:
    """The ToolSearch preload line for a chat seat, or ``""`` when it needs none.

    ACP seats (Claude Code and friends) get MCP tools deferred: they must
    ``ToolSearch`` a name before they can call it. A keyword search can still
    find a name this line omits, so the preload is not the only route to a
    mounted tool — what it buys is the round trips, and the far likelier failure
    that the model never thinks to search at all. Pydantic-ai seats auto-discover
    their toolset and must never receive the line.

    Public because both chat branches need it: the coordinator's
    :func:`build_initial_context` and the specialist's ``bound_agent_context``,
    which skips that builder entirely (CORR-272).
    """
    from condor.acp.pydantic_ai_client import is_pydantic_ai_model

    if not agent_key or is_pydantic_ai_model(agent_key):
        return ""
    return (
        "IMPORTANT: At the very start of the session (before your first response), "
        "load ALL MCP tools in a single ToolSearch call:\n"
        f'ToolSearch(query="select:{",".join(_chat_mcp_tools())}")\n'
        "This avoids repeated ToolSearch calls that waste context tokens. "
        "Do this silently without telling the user."
    )


def conversation_attribution(tag: str) -> str:
    """Tell a chat the ``controller_id`` its positions must carry (CORR-325).

    The conversation-shaped sibling of the loop's ``[TICK INFO]`` block
    (:mod:`condor.agents.prompts`), which is where this instruction lived and
    only lived. That one is nested inside ``if agent_id:`` and ``agent_id`` is a
    *session* concept, so a chat never reached it: a conversation was never told
    to pass a tag and had none to pass, and every executor it opened was born
    unattributable. The tag comes from
    :func:`~condor.agents.deeds.attribution_tag`, so the string the model is
    handed here is byte-for-byte the one the conversation's deployment panel
    (FEAT-110) later queries the trading API with — the join is one rule stated
    once, not two that have to be kept in agreement.

    The wording says *conversation*, not *session*. The loop's line says
    "attributes the position to this session" and for a chat that would name the
    wrong thing entirely — a reader of this prompt has no session, and telling
    it otherwise is how a model invents a plausible id instead of using the one
    it was given.

    Returns ``""`` for a run with no tag, which is a chat with no durable
    conversation behind it. Silence is right: an instruction to pass a tag that
    does not exist is worse than no instruction, because a model that is told to
    tag will tag with something.

    Placed at the one point where both chat branches have converged
    (``sessions.create_session``), so the coordinator and a bound specialist —
    which skips :func:`build_initial_context` entirely (CORR-272) — cannot
    disagree about what a conversation's positions are called.
    """
    if not tag:
        return ""
    return (
        "[THIS CONVERSATION]\n"
        f"Attribution tag: {tag}\n"
        f'Pass controller_id="{tag}" to every create_*_executor call — it is what '
        "attributes the position to this conversation. Without it nothing can tell "
        "that this chat opened it, and it will not appear in what this conversation "
        "reports having deployed."
    )


def _build_system_prompt(platform: str = "telegram") -> str:
    """Condor's own AGENT.md plus the platform's formatting rules."""
    agent = _chat_agent()
    return (
        "[System context -- do not repeat this to the user]\n\n"
        f"{agent.instructions if agent else ''}\n\n"
        f"{platform_formatting(platform)}"
    )


def build_initial_context(
    user_id: int,
    chat_id: int | str,
    user_data: dict | None = None,
    agent_key: str | None = None,
    platform: str = "telegram",
    server_name: str | None = None,
) -> str:
    """Build an initial context prompt telling the agent about server, permissions, and formatting rules."""
    from config_manager import get_config_manager, get_effective_server

    cm = get_config_manager()

    # System prompt: Condor's own AGENT.md + platform formatting
    system_prompt = _build_system_prompt(platform)
    sections: list[str] = [system_prompt]

    # Resolve active server (explicit override > user preferences)
    active_name = server_name or get_effective_server(chat_id, user_data)
    accessible = cm.get_accessible_servers(user_id)
    if not active_name:
        active_name = accessible[0] if accessible else None

    if active_name:
        # Build server list with permissions (no credentials needed — MCP is pre-configured)
        server_lines: list[str] = []
        for name in accessible:
            server = cm.get_server(name)
            if not server:
                continue
            perm = cm.get_server_permission(user_id, name)
            perm_label = perm.value.upper() if perm else "UNKNOWN"
            active_tag = " (active)" if name == active_name else ""
            server_lines.append(f"- {name}{active_tag} [{perm_label}]")

        # Get active server permission for enforcement
        active_perm = cm.get_server_permission(user_id, active_name)
        active_perm_label = active_perm.value.upper() if active_perm else "UNKNOWN"

        # For ACP agents (Claude Code): instruct them to preload MCP tools via
        # ToolSearch. Pydantic-ai agents get tools directly, no preload needed.
        tool_preload_hint = chat_tool_preload(agent_key)

        # Build server info section
        server_info = [
            f"Active server: {active_name} (permission: {active_perm_label})",
            "The MCP server is already connected to this server. Do NOT call configure_server — it is pre-configured.",
            "",
        ]

        if tool_preload_hint:
            server_info.extend([tool_preload_hint, ""])

        server_info.extend(
            [
                "Available servers:",
                *server_lines,
                "",
                "Server switching is not supported mid-session. If the user wants a different server, "
                "tell them to start a new session with that server selected.",
                "",
                "Permission rules:",
                f"- Your current permission on '{active_name}' is {active_perm_label}.",
                "- OWNER: Full access including trading operations and server management.",
                "- TRADER: Can trade, view balances, and manage own settings.",
                "- Enforce the permission level for this server.",
            ]
        )

        sections.append("\n".join(server_info))

    # User memory index — what the chat assistant remembers about this user. This
    # store is the chat's own (FEAT-003), not shared with the user's trading
    # agents. Inject only the index; bodies are read on demand via
    # manage_memory(action="read"). Nothing injected for new users.
    try:
        from condor.memory import MemoryStore

        memory_index = MemoryStore(user_id).list_index()
        if memory_index:
            sections.append(
                "[USER MEMORY — what you remember about this user]\n"
                "Consider this before responding. Read a full memory with "
                'manage_memory(action="read", name="..."). When you learn something '
                'new and stable about the user, save it with manage_memory(action="write", ...).\n\n'
                f"{memory_index}"
            )
    except Exception:
        pass  # Memory is advisory — never block session start on it.

    # Skills index — read-only playbooks the chat assistant ships (know-how +
    # steps). Skills are general to the assistant, not learned per user; inject
    # only the index, bodies are read on demand via manage_skill(action="read").
    # Nothing injected when the assistant ships none.
    try:
        from condor.memory import SkillStore

        skills_index = SkillStore().list_index()
        if skills_index:
            sections.append(
                "[SKILLS — check here BEFORE handling a known flow with raw tools]\n"
                "Read-only playbooks shipped with the assistant. Before using raw "
                "tools for a flow below, read its playbook with "
                'manage_skill(action="read", name="...") and follow the steps — '
                "don't re-derive or hand-roll what a playbook already covers.\n\n"
                f"{skills_index}"
            )
    except Exception:
        pass  # Skills are advisory — never block session start on them.

    # Agents index — domain Agents condor can consult (FEAT: coordinator model).
    # condor delegates domain work via consult(...) instead of holding the domain's
    # tools/context itself. EVERY agent is listed: one missing from this index can
    # never be routed to. Nothing injected only when none exist.
    try:
        from condor.agents.agent import AgentStore

        # The coordinator is not among its own consultees (FEAT-033).
        agents_index = AgentStore().list_index(exclude={CHAT_SLUG})
        if agents_index:
            sections.append(
                "[AGENTS — consult these BEFORE doing domain work with raw tools]\n"
                "You are a coordinator. If a request falls in an agent's domain "
                "below, delegate it with "
                'consult(agent="<slug>", task="...", context="...") instead of '
                "driving the domain's raw tools yourself — the agent has the focused "
                "tools and domain memory. Relay a concise summary of its answer.\n\n"
                f"{agents_index}"
            )
    except Exception:
        pass  # The agents index is advisory — never block session start on it.

    return "\n\n".join(sections)


def build_agent_context(
    agent: "Agent",  # noqa: F821 — condor.agents.agent.Agent
    user_id: int,
    task: str,
    context: str = "",
) -> str:
    """Assemble the prompt for an Agent consult.

    Mirrors :func:`build_initial_context` but for a worker run: the Agent's own
    instructions become the system prompt, its domain-scoped memory/skills indexes
    are injected (keyed by the Agent slug, FEAT-003 — the shared brain), and the
    consult task is appended last. Read on demand via manage_memory/manage_skill
    inside the run.

    Those indexes come from :func:`~condor.memory.domain_context`, the same
    builder ``binding.agent_identity_context`` composes, so this Agent carries
    identical instructions about its own memory whether it is consulted or
    chatted with (ARCH-099).
    """
    from condor.memory import domain_context

    sections: list[str] = [agent.instructions]
    sections.extend(domain_context(agent.slug, user_id))

    consult = f"[CONSULT REQUEST]\n{task}"
    if context:
        consult += f"\n\n[CONTEXT FROM CONDOR]\n{context}"
    sections.append(consult)

    return "\n\n".join(sections)

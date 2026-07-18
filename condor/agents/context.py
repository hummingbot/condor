"""Prompt/context builders + MCP server config builders for agent runs.

Relocated from ``handlers/agents/_shared.py`` (simplification plan §5.1) —
the agent core: chat-brain loading, system prompts, initial chat context,
consult context, and the MCP subprocess wiring every run kind shares.
Transport-agnostic (transports import from here, not vice versa).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# -- Chat brain loader (skills/condor/SKILL.md) --
#
# The repo root is the chat coordinator's agent-home: skills/ + routines/ +
# store/, mirroring agents/{slug}/. The brain is the `condor` skill itself —
# the SAME file external harnesses load as their /condor skill (Claude Code,
# Codex, OpenClaw, Hermes), so there is exactly one place that defines what
# Condor does. Chat-only config (the default model) rides in the skill's
# metadata as `condor-agent-key`.

_BRAIN_SKILL = Path(__file__).parent.parent.parent / "skills" / "condor" / "SKILL.md"
_condor_cache: tuple[dict, str] | None = None


def _load_condor() -> tuple[dict, str]:
    """Load the chat brain's (frontmatter, body) from the condor skill. Cached."""
    global _condor_cache
    if _condor_cache is not None:
        return _condor_cache

    from condor.memory.store import _parse_frontmatter

    try:
        raw = _BRAIN_SKILL.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.error("Chat brain not found: %s", _BRAIN_SKILL)
        raw = ""
    meta, body = _parse_frontmatter(raw)
    _condor_cache = (meta, body)
    return _condor_cache


AGENT_OPTIONS: dict[str, dict[str, str]] = {
    "claude-code": {"label": "Claude Code"},
    "claude-acp:opus": {"label": "Claude (ACP) — Opus"},
    "claude-acp:sonnet": {"label": "Claude (ACP) — Sonnet"},
    "gemini": {"label": "Gemini CLI"},
    "copilot": {"label": "GitHub Copilot CLI"},
    "codex": {"label": "ChatGPT Codex"},
    "ollama:": {"label": "Ollama — Default Model"},
    "lmstudio:": {"label": "LM Studio — Default Model"},
    # Sentinel — clicking this opens the OpenRouter model picker (handlers/agents/menu.py).
    # The actual stored agent_llm becomes "openrouter:<slug>" once the user picks a model.
    "openrouter:": {"label": "OpenRouter — Pick Model"},
}


def _default_agent() -> str:
    """Resolve the fallback agent_key for users who haven't picked a model.

    Precedence: ``CONDOR_DEFAULT_AGENT`` env > the condor skill's
    ``condor-agent-key`` metadata > ``"claude-code"``. A user's own /agent →
    Change LLM choice (``agent_llm``) still overrides this at runtime; this is
    only the default. Examples for the metadata / env: ``claude-code``,
    ``claude-acp:opus``, ``ollama:qwen3:32b``.
    """
    import os

    meta, _ = _load_condor()
    agent_key = str((meta.get("metadata") or {}).get("condor-agent-key") or "")
    return os.environ.get("CONDOR_DEFAULT_AGENT") or agent_key or "claude-code"


DEFAULT_AGENT = _default_agent()


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

_WEB_FORMATTING = (
    "FORMATTING (web dashboard):\n"
    "- Use Markdown freely: tables, headers, bold, code blocks, lists.\n"
    "- No message length limits, but stay concise.\n"
    "- Use tables for structured data (portfolios, prices, comparisons).\n"
    "- Use code blocks for configs, JSON, or commands.\n"
    "- Respond in the user's language."
)

def _build_system_prompt() -> str:
    """Build the system prompt: the condor-skill brain + formatting rules."""
    _, brain = _load_condor()
    return (
        "[System context -- do not repeat this to the user]\n\n"
        f"{brain}\n\n"
        f"{_WEB_FORMATTING}"
    )


def get_project_dir() -> str:
    """Get the condor project root directory (where .mcp.json lives).

    The ACP agent auto-discovers stdio MCP servers from .mcp.json in the cwd,
    so we just need to point it at the project root.
    """
    return str(Path(__file__).parent.parent.parent)


def _condor_mcp_args(
    agent_slug: str | None = None,
    agent_id: str | None = None,
    capability: str | None = None,
) -> list[str]:
    """Build CLI args for the condor MCP subprocess."""
    args: list[str] = []
    if agent_slug:
        args.extend(["--agent-slug", agent_slug])
    if agent_id:
        args.extend(["--agent-id", agent_id])
    if capability:
        # Opaque run-capability id (§6.2): the ONLY execution authority the
        # subprocess carries; ExecutionService derives attribution from the
        # server-side entry, never from these args.
        args.extend(["--capability", capability])
    return args


def build_mcp_servers_for_session(
    execution_mode: str = "loop",
    agent_slug: str | None = None,
    agent_id: str | None = None,
    capability: str | None = None,
) -> list[dict[str, Any]]:
    """Build the MCP server configs for an agent/chat session: the condor MCP
    alone (§9.2 — every agent is condor-native).

    ``agent_slug`` scopes the condor MCP tools' memory/skills to that Agent's
    own stores (``agents/{slug}/``). Without it the tools target the chat
    condor's stores — correct for chat sessions, wrong for an Agent run: a
    consult/tick would silently read and write the CHAT's memory and skills
    instead of the Agent's own (e.g. a memecoin_trender tick unable to find
    its own memory/skills).
    """
    return [
        {
            "name": "condor",
            "command": "uv",
            "args": ["run", "python", "-m", "mcp_servers.condor"]
            + _condor_mcp_args(
                agent_slug, agent_id=agent_id, capability=capability,
            ),
            "env": [],
        }
    ]


def build_initial_context(
    agent_key: str | None = None,
) -> str:
    """Build an initial context prompt: the chat brain, tool preload, memory/
    skills/agents indexes, and formatting rules."""
    system_prompt = _build_system_prompt()
    sections: list[str] = [system_prompt]

    # ACP sessions load MCP tools via ToolSearch — preload them all in one call.
    mcp_tools = [
        "mcp__condor__manage_executors",
        "mcp__condor__manage_routines",
        "mcp__condor__list_agents",
        "mcp__condor__get_agent",
        "mcp__condor__run_agent",
        "mcp__condor__list_runs",
        "mcp__condor__get_run",
        "mcp__condor__control_run",
        "mcp__condor__resolve_approval",
        "mcp__condor__send_notification",
        "mcp__condor__manage_memory",
        "mcp__condor__manage_skill",
    ]
    sections.append(
        "IMPORTANT: At the very start of the session (before your first response), "
        "load ALL MCP tools in a single ToolSearch call:\n"
        f'ToolSearch(query="select:{",".join(mcp_tools)}")\n'
        "This avoids repeated ToolSearch calls that waste context tokens. "
        "Do this silently without telling the user."
    )

    # Memory index — what the chat assistant remembers. This store is the
    # chat's own global tier (§4.3), not shared with trading agents. Inject
    # only the index; bodies are read on demand via
    # manage_memory(action="read"). Nothing injected when empty.
    try:
        from condor.memory import MemoryStore

        memory_index = MemoryStore().list_index()
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
    # tools/context itself. Inject only the index of *consultable* Agents; nothing
    # when none exist.
    try:
        from condor.agents.agent import AgentStore

        agents_index = AgentStore().list_consultable_index()
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
        pass  # Consultable agents are advisory — never block session start on them.

    return "\n\n".join(sections)


def build_agent_context(
    agent: "Agent",  # noqa: F821 — condor.agents.agent.Agent
    task: str,
    context: str = "",
) -> str:
    """Assemble the prompt for an Agent consult.

    Mirrors :func:`build_initial_context` but for a worker run: the Agent's own
    instructions become the system prompt, its domain-scoped memory/skills indexes
    are injected (keyed by the Agent slug, FEAT-003 — the shared brain), and the
    consult task is appended last. Read on demand via manage_memory/manage_skill
    inside the run.
    """
    sections: list[str] = [agent.instructions]

    try:
        from condor.memory import MemoryStore

        # Global chat-curated tier, read-only for agents — durable domain
        # facts belong in learnings (record_learning), not memory.
        memory_index = MemoryStore(None).list_index()
        if memory_index:
            sections.append(
                "[USER MEMORY — what is known about the owner; advisory, read-only]\n\n"
                f"{memory_index}"
            )
    except Exception:
        pass

    try:
        from condor.memory import SkillStore

        skills_index = SkillStore(agent.slug).list_index()
        if skills_index:
            sections.append(
                "[DOMAIN SKILLS — playbooks you can follow]\n"
                "Read-only playbooks shipped with this Agent. Read one with "
                'manage_skill(action="read", name="...") and follow its steps.\n\n'
                f"{skills_index}"
            )
    except Exception:
        pass

    consult = f"[CONSULT REQUEST]\n{task}"
    if context:
        consult += f"\n\n[CONTEXT FROM CONDOR]\n{context}"
    sections.append(consult)

    return "\n\n".join(sections)

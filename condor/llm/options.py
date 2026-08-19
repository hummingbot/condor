"""The agent/model catalog and the resolved default (ARCH-190).

One catalog (``AGENT_OPTIONS``) for every surface that offers a model — the
Telegram menu, the web dashboard's dropdown, the setup wizard. Moved here from
``handlers/agents/_shared.py``, which re-exports these names for its Telegram
callers.
"""

from __future__ import annotations

import os
from typing import Any

from condor.memory.paths import CHAT_SLUG


def _chat_agent():
    """The default agent's record — Condor's ``agents/condor/AGENT.md``.

    There is one loader and one frontmatter schema (FEAT-033): the chat is read
    by ``AgentStore`` exactly like a specialist is. Returns ``None`` only if the
    file is missing or unreadable, which callers treat as "no instructions"
    rather than as a startup failure.
    """
    from condor.agents.agent import AgentStore

    return AgentStore().get(CHAT_SLUG)


AGENT_OPTIONS: dict[str, dict[str, Any]] = {
    "claude-code": {"label": "Claude Code"},
    "claude-acp:opus": {"label": "Claude (ACP) — Opus"},
    "claude-acp:sonnet": {"label": "Claude (ACP) — Sonnet"},
    "gemini": {"label": "Gemini CLI"},
    "copilot": {"label": "GitHub Copilot CLI"},
    "codex": {"label": "ChatGPT Codex"},
    "ollama:": {"label": "Ollama — Default Model"},
    "lmstudio:": {"label": "LM Studio — Default Model"},
    # Sentinels — these open a picker instead of being a selectable model, so
    # they are NOT valid agent keys. `picker` marks them for surfaces that
    # render AGENT_OPTIONS as a flat list of choices (the web dashboard's model
    # dropdown), which would otherwise offer a key that fails at session start.
    #
    # OpenRouter: stored agent_llm becomes "openrouter:<slug>".
    "openrouter:": {"label": "OpenRouter — Pick Model", "picker": True},
    # Custom endpoints: the user's saved OpenAI-compatible endpoints live in
    # preferences (condor/preferences.py, shared with the web dashboard) and
    # the stored agent_llm becomes "custom@<endpoint>:<model-id>".
    "custom:": {"label": "Custom — OpenAI-compatible API", "picker": True},
}


# What a fresh install should pick unless it has a reason not to: the ACP bridge
# is the best-supported path, and Sonnet is the balance of capability and cost
# most users want. Surfaces that rank the catalog (the `make pick-model` wizard)
# float this key to the top and mark it; it is deliberately NOT part of the
# `label`, which is reused as a plain display name all over the bot.
RECOMMENDED_AGENT = "claude-acp:sonnet"


def selectable_agent_options() -> dict[str, dict[str, Any]]:
    """AGENT_OPTIONS minus the picker sentinels — every key here is startable."""
    return {k: v for k, v in AGENT_OPTIONS.items() if not v.get("picker")}


def _default_agent() -> str:
    """Resolve the fallback agent_key for users who haven't picked a model.

    Precedence: ``CONDOR_DEFAULT_AGENT`` env > Condor's ``AGENT.md`` frontmatter
    ``agent_key`` > ``"claude-code"``. A user's own /agent → Change LLM choice
    (``agent_llm``) still overrides this at runtime; this is only the default.
    Examples for the frontmatter / env: ``claude-code``, ``claude-acp:opus``,
    ``ollama:qwen3:32b``.
    """
    agent = _chat_agent()
    return (
        os.environ.get("CONDOR_DEFAULT_AGENT")
        or (agent.agent_key if agent else "")
        or "claude-code"
    )


DEFAULT_AGENT = _default_agent()

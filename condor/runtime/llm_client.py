"""One factory for the PydanticAI-vs-ACP client split (ARCH-192).

Every surface that talks to a model — chat sessions, consult, the trading-agent
engine — needs the same decision: a pydantic-ai key (ollama/lmstudio/openai/
custom/...) gets an in-process :class:`PydanticAIClient`, anything else gets an
:class:`ACPClient` subprocess. That branch used to live in three drifted copies
(``runtime/sessions.py``, ``agents/consult.py``, ``agents/engine.py``); it now
lives here, and each surface passes only its own specifics:

- chat sessions: ``extra_env`` (CONDOR_* ids), the bound-Agent ``system_prompt``,
  ``strict_custom_endpoint=True`` (loud, actionable error for a missing saved
  endpoint) and the LM Studio pref as ``default_base_url``.
- consult: nothing extra — it healthchecks the backend *before* calling this.
- engine: the run config's ``model_base_url`` as ``base_url_override``.

The client classes are read as module attributes at call time so tests keep
patching the defining modules (``condor.acp.client.ACPClient`` /
``condor.acp.pydantic_ai_client.PydanticAIClient``) and intercept every surface
at once.
"""

from __future__ import annotations

import os
from typing import Any

from condor.acp import client as acp_client
from condor.acp import pydantic_ai_client as pydantic_ai
from condor.preferences import resolve_custom_endpoint
from condor.runtime.toolsets import get_project_dir


def build_llm_client(
    agent_key: str,
    *,
    mcp_servers: list[dict[str, Any]] | None = None,
    permission_callback: acp_client.PermissionCallback | None = None,
    allowed_tools: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    system_prompt: str = "",
    user_data: dict | None = None,
    user_id: int | None = None,
    base_url_override: str | None = None,
    default_base_url: str | None = None,
    tool_filter_mode: str | None = None,
    strict_custom_endpoint: bool = False,
) -> acp_client.ACPClient | pydantic_ai.PydanticAIClient:
    """Build (but do not start) the right client for ``agent_key``.

    Custom-endpoint keys (``custom@<endpoint>:<model>``) resolve through
    :func:`condor.preferences.resolve_custom_endpoint` — pass ``user_data``
    when you have it (Telegram) or ``user_id`` when you don't (web, MCP,
    background runs). ``strict_custom_endpoint=True`` keeps the chat path's
    guided RuntimeError for a key naming an unsaved endpoint; the lenient
    default falls back to the ``CUSTOM_LLM_*`` env vars.

    Base-URL precedence: ``base_url_override`` (an explicit config value, e.g.
    the engine's ``model_base_url``) beats the resolved custom endpoint, which
    beats ``default_base_url`` (a generic preference, e.g. the saved LM Studio
    URL — deliberately last so it cannot shadow a named custom endpoint).

    ``tool_filter_mode`` resolves as: explicit value (a user/config pref) >
    ``PYDANTIC_AI_TOOL_FILTER`` env > ``None`` (auto-detect by model size).

    ``extra_env``, ``system_prompt`` and ``allowed_tools`` are forwarded to
    whichever client understands them: the ACP subprocess takes the env and the
    system prompt but cannot enforce an allowlist; PydanticAI enforces the
    allowlist (and takes the env for its MCP subprocesses).
    """
    if pydantic_ai.is_pydantic_ai_model(agent_key):
        custom_url, api_key = resolve_custom_endpoint(
            agent_key,
            user_data=user_data,
            user_id=user_id,
            strict=strict_custom_endpoint,
        )
        return pydantic_ai.PydanticAIClient(
            model=agent_key,
            mcp_servers=mcp_servers,
            permission_callback=permission_callback,
            extra_env=extra_env,
            base_url=base_url_override or custom_url or default_base_url or None,
            api_key=api_key,
            tool_filter_mode=(
                tool_filter_mode or os.environ.get("PYDANTIC_AI_TOOL_FILTER") or None
            ),
            allowed_tools=allowed_tools,
        )

    # ACP subprocess models: claude-code, gemini, codex. A Claude model can be
    # pinned via a suffix ("claude-acp:opus"); ACPClient selects it over the
    # protocol (session/set_model) after handshake — the bridge ignores
    # ANTHROPIC_MODEL.
    command, model_env, model_pref = acp_client.resolve_acp(agent_key)
    merged_env = {**(extra_env or {}), **model_env}
    return acp_client.ACPClient(
        command=command,
        working_dir=get_project_dir(),
        mcp_servers=mcp_servers,
        permission_callback=permission_callback,
        extra_env=merged_env or None,
        model=model_pref or None,
        system_prompt=system_prompt,
    )

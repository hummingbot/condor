"""One factory for the PydanticAI-vs-ACP client split (ARCH-192).

Every surface that talks to a model — chat sessions, delegations, the trading-agent
engine — needs the same decision: a pydantic-ai key (ollama/lmstudio/openai/
custom/...) gets an in-process :class:`PydanticAIClient`, anything else gets an
:class:`ACPClient` subprocess. That branch used to live in three drifted copies
(``runtime/sessions.py``, ``agents/agent_run.py``, ``agents/engine.py``); it now
lives here, and each surface passes only its own specifics:

- chat sessions: ``extra_env`` (CONDOR_* ids), the bound-Agent ``system_prompt``,
  ``strict_custom_endpoint=True`` (loud, actionable error for a missing saved
  endpoint) and the LM Studio pref as ``default_base_url``.
- delegate: nothing extra — it healthchecks the backend *before* calling this.
- engine: the run config's ``model_base_url`` as ``base_url_override``.

The client classes are read as module attributes at call time so tests keep
patching the defining modules (``condor.acp.client.ACPClient`` /
``condor.acp.pydantic_ai_client.PydanticAIClient``) and intercept every surface
at once.
"""

from __future__ import annotations

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

    ``extra_env``, ``system_prompt`` and ``allowed_tools`` are forwarded to
    whichever client understands them. Both clients take the env and the system
    prompt — each over its own system-level channel (``_meta.systemPrompt`` for
    ACP, ``instructions`` for pydantic-ai), so a bound Agent keeps its identity
    on either backend (ARCH-331). Only PydanticAI takes ``allowed_tools`` as a
    client filter; ACP filters nothing, so for both the allowlist is enforced
    where the MCP servers are built — :func:`condor.runtime.toolsets.seat_mutes`
    keeps what it leaves out from ever being mounted.
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
            allowed_tools=allowed_tools,
            system_prompt=system_prompt,
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


async def agent_key_error(
    agent_key: str,
    *,
    user_id: int | None = None,
    base_url_override: str | None = None,
) -> str | None:
    """Why ``agent_key`` cannot run here, or ``None`` when nothing says so yet.

    For a caller about to hand the key to something unattended — the loop
    engine above all — that would rather refuse up front than start a run which
    fails where nobody is watching. Only what is certain before a request goes
    out counts: a provider no client knows, a key with no model id, a custom key
    naming an endpoint this user never saved, a provider with no API key or base
    URL. Those are exactly what a tick raises from its first ``start()``, and
    they are raised here by the same code, so the two cannot disagree. A local
    server that is down, or a model the provider does not serve, still fails at
    run time: telling those apart takes a network call, and a server that is off
    now may well be on by the first tick.
    """
    key = (agent_key or "").strip()
    if not key:
        return None  # no key = the ACP default, always addressable
    if not pydantic_ai.is_pydantic_ai_model(key):
        base = key.split(":", 1)[0]
        if base.split("@", 1)[0] in pydantic_ai.PYDANTIC_AI_PREFIXES:
            return f"no model id — use '{base}:<model-id>'"
        if base not in acp_client.ACP_COMMANDS:
            # resolve_acp would quietly run Claude Code in its place.
            known = sorted(acp_client.ACP_COMMANDS) + sorted(
                pydantic_ai.PYDANTIC_AI_PREFIXES
            )
            return f"unknown model provider '{base}' (known: {', '.join(known)})"
        return None

    from condor.llm.readiness import LOCAL_PREFIXES

    try:
        client = build_llm_client(
            key,
            user_id=user_id,
            base_url_override=base_url_override,
            # An explicit base URL is the endpoint, so the saved name is moot.
            strict_custom_endpoint=not base_url_override,
        )
        # A bare local key ("ollama:") asks the server which model to use.
        if (
            pydantic_ai.model_prefix(key) in LOCAL_PREFIXES
            and not key.partition(":")[2]
        ):
            return None
        await client._build_model()
    except Exception as e:
        return str(e) or type(e).__name__
    return None

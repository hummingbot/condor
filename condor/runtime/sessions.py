"""Chat session registry and lifecycle.

This is the single owner of chat-session state for every frontend. It lives
under ``condor/`` — never under ``handlers/`` — because ``reload_handlers()``
re-executes handler modules on every file change, and re-executing the module
that holds the registry silently orphans every live agent subprocess.

Nothing outside ``condor/runtime/`` should import this module directly; go
through ``condor.runtime.client`` instead.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from telegram import Bot

from condor.acp import ACPClient, PermissionCallback, PromptDone, resolve_acp
from condor.acp.pydantic_ai_client import (
    PydanticAIClient,
    is_pydantic_ai_model,
    model_prefix,
)
from condor.runtime import binding
from condor.runtime.keys import SessionKey
from condor.runtime.models import SessionInfo, SessionSpec
from condor.runtime.timeouts import TIMEOUTS
from handlers.agents._shared import build_initial_context, get_project_dir

log = logging.getLogger(__name__)

# Deadlines come from the shared policy (condor.runtime.timeouts) so changing
# one is a single edit that every surface honors. Kept as module names because
# they read better at the call sites below.
PROMPT_LOCK_TIMEOUT = TIMEOUTS.prompt_lock
PROMPT_OVERALL_TIMEOUT = TIMEOUTS.prompt_overall

# Maximum concurrent live sessions per user, across every surface. Each session
# is an agent subprocess, so this is a real resource bound. Enforced here rather
# than in a frontend so Telegram and the web dashboard share one budget.
MAX_SESSIONS_PER_USER = 5

# Module-level session storage keyed by str(SessionKey).
# Not persisted -- subprocesses can't survive restarts.
_sessions: dict[str, "AgentSession"] = {}

# Health monitor state
_health_task: asyncio.Task | None = None
_health_bot: Bot | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AgentSession:
    key: SessionKey
    agent_key: str  # "claude-code", "gemini", "codex", "copilot", "ollama:model", "lmstudio:model", etc.
    client: ACPClient | PydanticAIClient
    mode: str = "condor"  # "condor", "agent_builder"
    server_name: str | None = None  # Which Condor server this session uses
    user_id: int | None = None
    agent_slug: str = ""
    label: str = "Condor"  # who is answering, for both UIs' headers
    is_busy: bool = False
    pending_context: str | None = None  # Lazy context: injected on first prompt
    created_at: datetime = field(default_factory=_utcnow)
    last_prompt_at: datetime | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _abort_event: asyncio.Event = field(default_factory=asyncio.Event)

    def info(self) -> SessionInfo:
        """Serializable view of this session."""
        return SessionInfo(
            key=str(self.key),
            agent_key=self.agent_key,
            mode=self.mode,
            user_id=self.user_id,
            surface=self.key.surface,
            slot=self.key.slot,
            server_name=self.server_name,
            is_busy=self.is_busy,
            alive=bool(self.client.alive),
            created_at=self.created_at,
            last_prompt_at=self.last_prompt_at,
            agent_slug=self.agent_slug,
            label=self.label,
        )

    async def prompt_stream(self, text: str):
        """Stream a prompt, managing the busy flag and lock.

        Includes a lock-acquisition timeout (PROMPT_LOCK_TIMEOUT) to avoid
        waiting forever when a previous prompt is stuck, and an overall
        wall-clock timeout (PROMPT_OVERALL_TIMEOUT) to kill runaway prompts.
        """
        # Consume pending context on first prompt (lazy injection)
        if self.pending_context:
            ctx = self.pending_context
            self.pending_context = None
            text = f"{ctx}\n\n---\n\nUser message:\n{text}"

        # Clear abort flag for this new prompt
        self._abort_event.clear()

        # Acquire lock with timeout -- prevents infinite wait when previous prompt is stuck
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=PROMPT_LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("Lock acquisition timed out for session %s", self.key)
            # Force-clear busy flag if subprocess is dead (stuck state recovery)
            if not self.client.alive:
                self.is_busy = False
            raise RuntimeError(
                "Agent is busy and not responding. Try /agent → New Session."
            )

        self.is_busy = True
        self.last_prompt_at = _utcnow()
        try:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + PROMPT_OVERALL_TIMEOUT
            async for event in self.client.prompt_stream(text):
                # Check if abort was requested between events
                if self._abort_event.is_set():
                    yield PromptDone(stop_reason="cancelled")
                    break
                yield event
                if isinstance(event, PromptDone):
                    break
                if loop.time() > deadline:
                    log.warning(
                        "Prompt overall timeout (%ds) for session %s",
                        PROMPT_OVERALL_TIMEOUT,
                        self.key,
                    )
                    yield PromptDone(stop_reason="timeout")
                    break
        finally:
            self.is_busy = False
            self._lock.release()

    def abort(self) -> None:
        """Abort the current in-flight prompt.

        Sets the abort event so prompt_stream breaks out on the next iteration,
        which triggers its finally block to properly release the lock.
        Also cancels the ACP-level request future and drains the event queue
        so the next prompt starts clean.
        """
        # Signal prompt_stream to stop iterating
        self._abort_event.set()
        if isinstance(self.client, ACPClient):
            self.client.abort_prompt()
        log.info("Session %s: prompt aborted", self.key)


class SessionLimitReached(RuntimeError):
    """Raised when a user already holds MAX_SESSIONS_PER_USER live sessions."""


def _enforce_session_budget(user_id: int) -> None:
    """Reap this user's dead sessions, then refuse if still at the cap."""
    for raw_key, session in list(_sessions.items()):
        if session.user_id == user_id and not session.client.alive:
            _sessions.pop(raw_key, None)

    live = sum(1 for s in _sessions.values() if s.user_id == user_id)
    if live >= MAX_SESSIONS_PER_USER:
        raise SessionLimitReached(
            f"Max {MAX_SESSIONS_PER_USER} concurrent sessions reached. "
            "Close one before starting another."
        )


async def get_or_create_session(
    spec: SessionSpec,
    permission_callback: PermissionCallback | None = None,
    user_data: dict | None = None,
) -> AgentSession:
    """Get the existing session for ``spec.key`` or create a new one.

    ``permission_callback`` and ``user_data`` stay outside ``SessionSpec``:
    they are process objects, not wire data.

    When ``spec.user_id`` is provided, MCP servers are configured dynamically
    from the user's Condor server permissions instead of static .mcp.json.
    """
    key = SessionKey.parse(spec.key)
    raw_key = str(key)
    session = _sessions.get(raw_key)

    # Reuse existing session only if the same brain is still on the other end:
    # a different model OR a different bound Agent means a different session.
    # An empty spec.agent_key means "whatever the binding resolves", so it must
    # not count as a mismatch — otherwise every bound call would respawn.
    same_model = not spec.agent_key or session and session.agent_key == spec.agent_key
    if (
        session
        and same_model
        and session.agent_slug == spec.agent_slug
        and session.client.alive
    ):
        return session

    # Destroy old session if exists
    if session:
        await _destroy_session_internal(key)
    elif spec.user_id:
        # Only a genuinely new key counts against the budget — replacing the
        # session behind an existing key is a swap, not an extra subprocess.
        _enforce_session_budget(spec.user_id)

    # MCP subprocess env expects a numeric chat id; surfaces without a chat
    # (web, mcp) fall back to the user id.
    effective_chat_id = (
        spec.chat_id if spec.chat_id is not None else (spec.user_id or 0)
    )
    extra_env = {
        "CONDOR_CHAT_ID": str(effective_chat_id),
        "CONDOR_USER_ID": str(spec.user_id or effective_chat_id),
    }

    # Who is answering: an assistant persona, or a bound domain Agent with its
    # own model, tool allowlist, server pin and memory scope. Raises UnknownAgent
    # before anything is spawned, so a bad slug cannot orphan a subprocess.
    bound = binding.resolve(spec, user_data)
    extra_env.update(bound.mcp_env)
    mcp_servers = bound.mcp_servers
    agent_key = bound.agent_key or spec.agent_key

    # Check if agent_key requires PydanticAI client (ollama, lmstudio, openai, etc.)
    use_pydantic_ai = is_pydantic_ai_model(agent_key)

    if use_pydantic_ai:
        # For Pydantic AI models: auto-detect or use configured filter mode
        import os

        from condor.preferences import get_agent_prefs

        # Priority: user preference > env variable > auto-detect (None)
        agent_prefs = get_agent_prefs(user_data) if user_data else {}
        tool_filter_mode = (
            agent_prefs.get("tool_filter_mode")
            or os.environ.get("PYDANTIC_AI_TOOL_FILTER")
            or None  # None triggers auto-detection based on model size
        )

        base_url = (
            agent_prefs.get("base_url") or os.environ.get("LMSTUDIO_BASE_URL") or None
        )

        api_key = None
        if model_prefix(agent_key) == "custom":
            # Custom OpenAI-compatible provider. The agent key names one of the
            # user's saved endpoints ("custom@venice:..."); those live in the
            # shared preference store so Telegram and the web dashboard resolve
            # them identically. CUSTOM_LLM_* env vars cover headless deploys.
            from condor.preferences import find_custom_provider, parse_custom_agent_key

            provider_name, _ = parse_custom_agent_key(agent_key)
            provider = (
                find_custom_provider(user_data, provider_name) if user_data else None
            )
            if provider is None and provider_name:
                raise RuntimeError(
                    f"No saved endpoint named '{provider_name}'. Add it via "
                    "/agent → Change LLM → Custom endpoint, or Settings → "
                    "AI Providers on the web dashboard."
                )
            provider = provider or {}
            base_url = provider.get("base_url") or os.environ.get("CUSTOM_LLM_BASE_URL")
            api_key = provider.get("api_key") or os.environ.get("CUSTOM_LLM_API_KEY")

        client = PydanticAIClient(
            model=agent_key,
            mcp_servers=mcp_servers,
            permission_callback=permission_callback,
            extra_env=extra_env,
            tool_filter_mode=tool_filter_mode,  # Auto-detects if None
            base_url=base_url,
            api_key=api_key,
            # A bound Agent's allowlist is enforced here exactly as it is on
            # consult and loop, so an Agent has the same reach in every mode.
            allowed_tools=bound.tools or None,
        )
    else:
        # For ACP subprocess models: claude-code, gemini, codex.
        # A Claude model can be pinned via a suffix, e.g. "claude-acp:opus" /
        # "claude-acp:sonnet"; ACPClient selects it via session/set_model after
        # handshake (the bridge ignores ANTHROPIC_MODEL). Bare key = agent default.
        command, model_env, model_pref = resolve_acp(agent_key)
        client = ACPClient(
            command=command,
            working_dir=get_project_dir(),
            mcp_servers=mcp_servers,
            permission_callback=permission_callback,
            extra_env={**extra_env, **model_env},
            model=model_pref,
        )

    await client.start()

    try:
        # Build initial context about server and permissions. A bound Agent
        # opens with its OWN identity and domain memory instead of the chat
        # assistant's — that is what makes it a different brain, not a skin.
        initial_context = ""
        if bound.is_agent and spec.user_id:
            initial_context = binding.agent_identity_context(
                bound.agent_slug, spec.user_id, bound.instructions
            )
        elif spec.user_id:
            initial_context = build_initial_context(
                spec.user_id,
                spec.chat_id,
                user_data,
                agent_key=agent_key,
                platform=spec.platform,
                server_name=spec.server_name,
            )
        # Caller-supplied context (e.g. mode-specific assistant instructions)
        # rides along as spec data, so no caller needs the live session object.
        if spec.extra_context:
            initial_context = f"{initial_context}\n\n{spec.extra_context}".strip()

        # Resolve the server name that was actually used for this session
        resolved_server = spec.server_name
        if not resolved_server and spec.user_id:
            from config_manager import get_config_manager, get_effective_server

            resolved_server = get_effective_server(spec.chat_id, user_data)
            if not resolved_server:
                cm = get_config_manager()
                accessible = cm.get_accessible_servers(spec.user_id)
                resolved_server = accessible[0] if accessible else None

        if initial_context and not spec.lazy_context:
            # Eager: send context now (blocks until agent processes it)
            try:
                await client.prompt(initial_context)
            except Exception:
                log.warning("Failed to send initial context for session %s", raw_key)
            initial_context = ""  # Already sent

        session = AgentSession(
            key=key,
            agent_key=agent_key,
            client=client,
            mode=spec.mode,
            server_name=bound.server_name or resolved_server,
            user_id=spec.user_id,
            agent_slug=bound.agent_slug,
            label=bound.label,
            pending_context=initial_context or None,
        )
    except Exception:
        # Something failed after start -- stop client to prevent orphan subprocess
        await client.stop()
        raise

    _sessions[raw_key] = session
    log.info("Created agent session %s: %s (%s)", raw_key, agent_key, bound.label)
    return session


def get_session(key: SessionKey) -> AgentSession | None:
    """Get the live session object for a key, or None."""
    return _sessions.get(str(key))


def list_sessions(user_id: int | None = None) -> list[SessionInfo]:
    """List every registered session, optionally filtered by owning user."""
    infos: list[SessionInfo] = []
    for session in list(_sessions.values()):
        if user_id is not None and session.user_id != user_id:
            continue
        infos.append(session.info())
    return infos


async def destroy_session(key: SessionKey) -> bool:
    """Destroy the session for a key. Returns True if a session existed."""
    return await _destroy_session_internal(key)


async def _destroy_session_internal(key: SessionKey) -> bool:
    raw_key = str(key)
    session = _sessions.pop(raw_key, None)
    if session:
        try:
            await session.client.stop()
        except Exception:
            log.exception("Error stopping agent session %s", raw_key)
        log.info("Destroyed agent session %s", raw_key)
        return True
    return False


async def destroy_all_sessions() -> None:
    """Destroy all active sessions. Called on bot shutdown."""
    keys = [session.key for session in list(_sessions.values())]
    for key in keys:
        await _destroy_session_internal(key)
    log.info("Destroyed all %d agent session(s)", len(keys))


# --- Background health monitor ---


async def start_health_monitor(bot: Bot) -> None:
    """Start periodic background check for dead sessions."""
    global _health_task, _health_bot
    _health_bot = bot
    _health_task = asyncio.create_task(_health_check_loop())
    log.info("Agent health monitor started")


async def stop_health_monitor() -> None:
    """Cancel the health monitor task."""
    global _health_task, _health_bot
    if _health_task and not _health_task.done():
        _health_task.cancel()
        try:
            await _health_task
        except asyncio.CancelledError:
            pass
    _health_task = None
    _health_bot = None
    log.info("Agent health monitor stopped")


async def _health_check_loop() -> None:
    """Every 15s, check for dead sessions (including stuck ones with is_busy=True)."""
    try:
        while True:
            await asyncio.sleep(15)
            dead_keys: list[SessionKey] = []
            for raw_key, session in list(_sessions.items()):
                if not session.client.alive:
                    if session.is_busy:
                        # Force-clear stuck busy flag on dead sessions
                        session.is_busy = False
                        log.warning(
                            "Health monitor: force-cleared is_busy for dead session %s",
                            raw_key,
                        )
                    dead_keys.append(session.key)

            for key in dead_keys:
                log.warning("Health monitor: dead session %s, cleaning up", key)
                await _destroy_session_internal(key)
                # Only Telegram sessions have a chat to notify.
                chat_id = key.telegram_chat_id
                if _health_bot and chat_id is not None:
                    try:
                        await _health_bot.send_message(
                            chat_id=chat_id,
                            text="Agent session ended unexpectedly. Send a message to start a new session.",
                        )
                    except Exception:
                        log.warning(
                            "Failed to notify chat %s about dead session", chat_id
                        )
    except asyncio.CancelledError:
        pass

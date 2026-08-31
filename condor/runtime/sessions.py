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
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from telegram import Bot

from condor.acp import ACPClient, PermissionCallback, PromptDone
from condor.acp.pydantic_ai_client import PydanticAIClient
from condor.agents.agent import identity_header as agent_identity_header
from condor.runtime import binding, conversations
from condor.runtime.confirmations import get_registry as get_confirmation_registry
from condor.runtime.context import build_initial_context, platform_formatting
from condor.runtime.keys import SessionKey
from condor.runtime.models import SessionInfo, SessionSpec
from condor.runtime.timeouts import TIMEOUTS

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

# The slots whose subprocess went away but whose conversation did not (CORR-265),
# keyed like ``_sessions``; the value is the last SessionInfo that session
# reported, with ``alive`` false.
#
# "Reaped" and "destroyed" are different answers to "where did my chat go", and
# only the registry can tell them apart. An idle detach (PERF-226), an LRU
# eviction and the health monitor's sweep of a dead subprocess all keep the
# conversation and reattach on the next message, so the slot is remembered here
# and a frontend can still list it. An explicit destroy -- Telegram /new,
# ``destroy_session``, the REST endpoint -- means the user asked for it to be
# gone, so it leaves nothing behind and disappears from every roster.
#
# Only slots with a durable conversation to come back to are remembered: without
# one there is nothing a reattach could resume.
_detached: dict[str, SessionInfo] = {}

# How many detached slots to remember, per user per surface. The same bound as
# the live cap, so the registry never remembers more retired slots than it
# allows running ones -- a long-lived process cannot grow an unbounded tab
# strip, and a busy Telegram user cannot evict a web tab's memory of itself.
MAX_DETACHED_PER_USER = MAX_SESSIONS_PER_USER

# Creation serialization (CORR-187). get_or_create_session is a check-then-
# create that spans many awaits (subprocess spawn, eager context prompt), so
# without a lock two concurrent calls for the same key both spawn a full
# client and the second registration overwrites the first, whose process tree
# is never stopped -- teardown only walks the registry. Locks are per session
# key (creations under different keys stay concurrent) and refcounted so the
# dict does not grow with every key ever seen.
_creation_locks: dict[str, asyncio.Lock] = {}
_creation_lock_refs: dict[str, int] = {}

# Session keys currently being created, per user. A creation in flight is not
# in ``_sessions`` yet but will register one session, so the budget check has
# to count it or N concurrent creates of distinct keys all pass the cap.
_pending_creates: dict[int, set[str]] = {}


@asynccontextmanager
async def _creation_lock(name: str):
    """Hold the named creation lock; drop it when the last holder leaves.

    Refcounted rather than popped on release: popping while another task still
    waits on the lock object would hand a *fresh* lock to the next caller and
    reopen the race between the waiter and the newcomer.
    """
    lock = _creation_locks.setdefault(name, asyncio.Lock())
    _creation_lock_refs[name] = _creation_lock_refs.get(name, 0) + 1
    try:
        async with lock:
            yield
    finally:
        remaining = _creation_lock_refs[name] - 1
        if remaining:
            _creation_lock_refs[name] = remaining
        else:
            del _creation_lock_refs[name]
            _creation_locks.pop(name, None)


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
    server_name: str | None = None  # Which Condor server this session uses
    # ...and whether the bound Agent chose it. A pinned server is not the
    # chat's to change; an ambient one is.
    server_pinned: bool = False
    user_id: int | None = None
    agent_slug: str = ""
    label: str = "Condor"  # who is answering, for both UIs' headers
    # The durable conversation this session answers into. The subprocess dies
    # with the process; the transcript behind this id does not.
    conversation_id: str = ""
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
            user_id=self.user_id,
            surface=self.key.surface,
            slot=self.key.slot,
            server_name=self.server_name,
            server_pinned=self.server_pinned,
            is_busy=self.is_busy,
            alive=bool(self.client.alive),
            created_at=self.created_at,
            last_prompt_at=self.last_prompt_at,
            agent_slug=self.agent_slug,
            label=self.label,
            conversation_id=self.conversation_id,
        )

    async def prompt_stream(self, text: str, *, lock_timeout: float | None = None):
        """Stream a prompt, managing the busy flag and lock.

        The lock *is* the queue: ``asyncio.Lock`` is FIFO, so several prompts
        on one session are answered in the order they were sent. Includes a
        lock-acquisition timeout to avoid waiting forever when a previous
        prompt is stuck, and an overall wall-clock timeout
        (PROMPT_OVERALL_TIMEOUT) to kill runaway prompts.

        ``lock_timeout`` defaults to PROMPT_LOCK_TIMEOUT, which guards against a
        *stuck* prompt. A caller that is deliberately waiting its turn passes a
        longer one (``TIMEOUTS.prompt_queue``), or a message queued behind a
        long answer would be failed as "not responding" for doing exactly what
        it was asked to do.
        """
        # Consume pending context on first prompt (lazy injection)
        if self.pending_context:
            ctx = self.pending_context
            self.pending_context = None
            text = f"{ctx}\n\n---\n\nUser message:\n{text}"

        # Acquire lock with timeout -- prevents infinite wait when previous prompt is stuck
        lock_deadline = PROMPT_LOCK_TIMEOUT if lock_timeout is None else lock_timeout
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=lock_deadline)
        except asyncio.TimeoutError:
            log.warning("Lock acquisition timed out for session %s", self.key)
            # Force-clear busy flag if subprocess is dead (stuck state recovery)
            if not self.client.alive:
                self.is_busy = False
            raise RuntimeError(
                "Agent is busy and not responding. Try /agent → New Session."
            )

        # Cleared *after* the lock, not before: a prompt waiting its turn used
        # to clear the flag out from under the turn ahead of it, so a Stop
        # landing on that turn was swallowed by the message queued behind it.
        self._abort_event.clear()

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
                    # Cancel at the agent before walking away, exactly as
                    # abort() does. Breaking out only stops us *relaying*: the
                    # turn would keep generating and keep running tools against
                    # a permission callback nobody is watching, and the next
                    # prompt would overlap it at the subprocess. Bounded by
                    # TIMEOUTS.prompt_cancel with a local fallback, so this
                    # cannot hang the caller.
                    try:
                        await self.client.abort_prompt()
                    except Exception:  # noqa: BLE001 - never mask the timeout
                        log.warning(
                            "Could not cancel timed-out prompt for session %s",
                            self.key,
                            exc_info=True,
                        )
                    yield PromptDone(stop_reason="timeout")
                    break
        finally:
            self.is_busy = False
            self._lock.release()

    async def abort(self) -> None:
        """Abort the current in-flight prompt, at the agent.

        Sets the abort event so prompt_stream breaks out on the next iteration,
        which triggers its finally block to properly release the lock. Then
        awaits the client's own cancel: for ACP that is a ``session/cancel``
        the agent answers, so generation actually stops and its context ends
        where the transcript does. Awaiting is the honest signature — Stop now
        costs a bounded wait (``TIMEOUTS.prompt_cancel``) instead of returning
        instantly on a promise it could not keep.

        Abort is a session-level concept: prompts driven straight at a client
        (consult, the strategy engine) run outside this lock and outside this.
        """
        # Signal prompt_stream to stop iterating
        self._abort_event.set()
        await self.client.abort_prompt()
        log.info("Session %s: prompt aborted", self.key)


class SessionLimitReached(RuntimeError):
    """Raised when a user already holds MAX_SESSIONS_PER_USER live sessions."""


def _deny_pending_confirmations(raw_key: str) -> int:
    """Deny whatever this session was still asking a human to approve.

    A session that is going away cannot act on an approval, and the identity
    that raised the request no longer exists — while the entry stays PENDING
    its "Approve" button is still tappable, and after a respawn under the same
    key it would authorize a live tool call for whoever is bound *now*. Denying
    here, on the one funnel every teardown goes through (destroy, agent/server
    switch, LRU detach, health-monitor reap, shutdown), is what keeps that from
    depending on each caller remembering to.
    """
    try:
        denied = get_confirmation_registry().deny_pending_for_session(raw_key)
    except Exception:  # noqa: BLE001 - cleanup must never block a teardown
        log.warning("Could not deny pending confirmations for %s", raw_key)
        return 0
    if denied:
        log.info("Denied %d pending confirmation(s) tearing down %s", denied, raw_key)
    return denied


async def _notify_detached_by_budget(key: SessionKey) -> None:
    """Tell a Telegram chat its session was detached to make room (CORR-227).

    Only the health monitor used to speak into a chat it had torn down, and it
    says "ended unexpectedly" because a dead subprocess is a fault. An LRU
    detach is not a fault: the conversation is durable and the next message
    reattaches it. Same delivery path, deliberately different words, so the
    two are distinguishable from inside the chat.
    """
    chat_id = key.telegram_chat_id
    if _health_bot is None or chat_id is None:
        return
    try:
        await _health_bot.send_message(
            chat_id=chat_id,
            text=(
                "Agent session detached to free a slot for another chat. "
                "Send a message to pick up where you left off."
            ),
        )
    except Exception:  # noqa: BLE001 - a notice must never break the budget
        log.warning("Failed to notify chat %s about a detached session", chat_id)


async def _enforce_session_budget(user_id: int, surface: str | None = None) -> None:
    """Reap dead sessions, detach the least recently used idle one, then refuse.

    Detaching used to be unthinkable — destroying a session destroyed the chat,
    so the only safe answer was a wall the user did not understand. Now that
    the conversation is durable (FEAT-015), the detached chat is fully
    recoverable and reattaching costs one lazy spawn, so the cap behaves as an
    LRU instead. Only when every session is *busy* is there nothing to give up.

    The cap counts every surface (it bounds subprocesses, not tabs), but each
    frontend only shows its own sessions — so a plain global LRU let a new web
    tab silently detach a Telegram chat the user could not see (CORR-227).
    ``surface`` is the incoming key's surface: the victim is chosen from that
    surface first, and only when it has no idle session does eviction cross
    over — audibly, for a Telegram victim.
    """
    for raw_key, session in list(_sessions.items()):
        if session.user_id == user_id and not session.client.alive:
            # Dropped straight from the registry rather than through
            # _destroy_session_internal (the subprocess is already gone), so
            # the sweeps it owns have to be repeated here -- including
            # remembering the slot, which a dead subprocess does not end.
            _deny_pending_confirmations(raw_key)
            _remember_detached(session)
            _sessions.pop(raw_key, None)

    while True:
        mine = [s for s in _sessions.values() if s.user_id == user_id]
        # Creations in flight for this user (reserved under the per-user
        # creation lock, registered only later) hold budget too, or N
        # concurrent creates of distinct keys would all pass the count.
        in_flight = len(_pending_creates.get(user_id, set()) - set(_sessions))
        if len(mine) + in_flight < MAX_SESSIONS_PER_USER:
            return

        idle = [s for s in mine if not s.is_busy]
        if not idle:
            raise SessionLimitReached(
                f"All {MAX_SESSIONS_PER_USER} of your sessions are busy. "
                "Wait for one to finish, or cancel it."
            )

        # Same surface first; the full list is the fallback that keeps the cap
        # a cap even when this surface has nothing idle to give up.
        same_surface = [s for s in idle if s.key.surface == surface]
        crossed = not same_surface
        victim = min(
            same_surface or idle, key=lambda s: s.last_prompt_at or s.created_at
        )
        log.info(
            "Session budget reached for user %s: detaching idle session %s "
            "(conversation %s is kept%s)",
            user_id,
            victim.key,
            victim.conversation_id or "none",
            ", crossing surfaces" if crossed else "",
        )
        await _destroy_session_internal(victim.key, retain=True)
        if crossed:
            await _notify_detached_by_budget(victim.key)


def bound_agent_context(
    bound: binding.SessionBinding, user_id: int, platform: str
) -> str:
    """The opening context of a chat bound to a specialist Agent.

    A specialist opens with its own identity and domain memory rather than the
    chat's context, which is what makes it a different brain instead of a skin
    — but it meant it was also the one brain that never passed through
    :func:`build_initial_context`, and so never heard how the surface it is
    speaking into renders a reply: no tables and no charts on the dashboard, no
    length rules on Telegram. The formatting section is appended here, at the
    branch that skips the other path, so both teach it exactly once.
    """
    return "\n\n".join(
        (
            binding.agent_identity_context(
                bound.agent_slug, user_id, bound.instructions, bound.label
            ),
            platform_formatting(platform),
        )
    )


def _resolve_conversation(
    spec: SessionSpec,
    key: SessionKey,
    *,
    agent_key: str,
    agent_slug: str,
    server_name: str | None,
) -> tuple[str, str]:
    """Attach this session to a conversation. Returns ``(conv_id, replay)``.

    A session without a ``user_id`` gets no conversation: the store is keyed by
    owner, and a transcript nobody owns can neither be listed nor authorized.
    Such a session behaves exactly as it did before this feature.

    A requested conversation that no longer exists falls through to a new one
    rather than resurrecting the id as an empty, meta-less directory. Callers
    can now hold an id across a restart (ARCH-101), so "deleted since" is a
    normal outcome and not a reason to strand the chat.
    """
    if not spec.user_id:
        return "", ""

    try:
        if spec.conversation_id and conversations.update_meta(
            spec.user_id,
            spec.conversation_id,
            agent_key=agent_key,
            agent_slug=agent_slug,
            server_name=server_name,
        ):
            return spec.conversation_id, conversations.replay_context(
                spec.user_id, spec.conversation_id
            )

        if spec.conversation_id:
            log.info(
                "Conversation %s is gone; %s starts a new one",
                spec.conversation_id,
                key,
            )

        meta = conversations.new_conversation(
            spec.user_id,
            key.surface,
            agent_key=agent_key,
            agent_slug=agent_slug,
            server_name=server_name,
            # Whether anyone but the owner can speak into this transcript. On
            # Telegram the key's owner is the *chat*, so a chat id that is not
            # the user's own id is a group: the session is one, its turns are
            # recorded under whoever opened it, and nothing downstream could
            # tell them apart afterwards. Recorded here so the sweep can refuse
            # it rather than trusting a guard in another module (FEAT-055).
            multi_author=bool(
                spec.chat_id is not None and spec.chat_id != spec.user_id
            ),
        )
        return meta.id, ""
    except Exception:  # noqa: BLE001 - a chat must start even if recording fails
        log.warning("Could not resolve conversation for %s", key, exc_info=True)
        return "", ""


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

    # One creation at a time per key (CORR-187): the second concurrent caller
    # waits here and then finds the first caller's session in the registry
    # instead of spawning a duplicate whose twin would leak until shutdown.
    # Locks are per key, so creations under different keys stay concurrent.
    async with _creation_lock(raw_key):
        return await _get_or_create_session_locked(
            spec, key, raw_key, permission_callback, user_data
        )


async def _get_or_create_session_locked(
    spec: SessionSpec,
    key: SessionKey,
    raw_key: str,
    permission_callback: PermissionCallback | None,
    user_data: dict | None,
) -> AgentSession:
    """Body of :func:`get_or_create_session`; runs under the per-key lock."""
    session = _sessions.get(raw_key)

    # Reuse existing session only if the same brain is still on the other end:
    # a different model OR a different bound Agent means a different session.
    # An empty spec.agent_key means "whatever the binding resolves", so it must
    # not count as a mismatch — otherwise every bound call would respawn.
    same_model = not spec.agent_key or session and session.agent_key == spec.agent_key
    # Likewise for the conversation: asking to resume a *different* transcript
    # under the same key means a different chat, so the subprocess is replaced
    # rather than silently answering the old one's context.
    same_conversation = (
        not spec.conversation_id
        or session
        and session.conversation_id == spec.conversation_id
    )
    if (
        session
        and same_model
        and same_conversation
        and session.agent_slug == spec.agent_slug
        and session.client.alive
    ):
        return session

    # Destroy old session if exists
    if session:
        await _destroy_session_internal(key)

    if spec.user_id:
        if session is None:
            # Only a genuinely new key counts against the budget — replacing
            # the session behind an existing key is a swap, not an extra
            # subprocess.
            await _enforce_session_budget(spec.user_id, key.surface)
        # Reserve this key's budget slot for the whole spawn. The reservation
        # must follow the check with no await in between — the event loop
        # makes the pair atomic, so two creates can never both pass the count
        # before either reserves (and _enforce_session_budget recounts after
        # every await it does make). The swap path reserves too: its old
        # session is already out of the registry, so a concurrent create
        # would otherwise undercount during the respawn window.
        _pending_creates.setdefault(spec.user_id, set()).add(raw_key)
        try:
            return await _spawn_session(
                spec, key, raw_key, permission_callback, user_data
            )
        finally:
            reserved = _pending_creates.get(spec.user_id)
            if reserved is not None:
                reserved.discard(raw_key)
                if not reserved:
                    del _pending_creates[spec.user_id]

    return await _spawn_session(spec, key, raw_key, permission_callback, user_data)


async def _spawn_session(
    spec: SessionSpec,
    key: SessionKey,
    raw_key: str,
    permission_callback: PermissionCallback | None,
    user_data: dict | None,
) -> AgentSession:
    """Spawn, contextualize, and register a new session for ``raw_key``.

    Runs under the per-key creation lock, with the key's budget slot already
    reserved when the spec names a user.
    """
    # MCP subprocess env expects a numeric chat id; surfaces without a chat
    # (web, mcp) fall back to the user id. The same pair goes down on argv via
    # binding.resolve, which is what the subprocess actually reads first — so
    # both channels take it from one derivation (SEC-180).
    effective_user_id, effective_chat_id = spec.effective_ids()
    extra_env = {
        "CONDOR_CHAT_ID": str(effective_chat_id),
        "CONDOR_USER_ID": str(effective_user_id),
        # Which session the MCP subprocess belongs to. The *conversation* id does
        # not exist yet here (it is minted below, after the client is up), but the
        # key does and is stable for the subprocess's whole life — so tools that
        # need conversation provenance (delegate) post the key back and let the
        # route resolve it where the truth lives.
        #
        # This env var reaches the *ACP* subprocess. It does not reliably reach
        # the MCP server the bridge spawns beneath it — a stdio MCP child gets
        # the ``env`` from its own config, not this process's environment — so
        # the key also goes down on argv via ``binding.resolve`` below, which is
        # the channel the subprocess actually reads first. Kept here too because
        # a run started outside a session has nothing else.
        "CONDOR_SESSION_KEY": raw_key,
    }

    # Who is answering: the bound Agent, or Condor when none is named — with
    # its model, tool allowlist, server pin and memory scope. Raises UnknownAgent
    # before anything is spawned, so a bad slug cannot orphan a subprocess.
    bound = binding.resolve(spec, user_data, session_key=raw_key)
    extra_env.update(bound.mcp_env)
    mcp_servers = bound.mcp_servers
    agent_key = bound.agent_key or spec.agent_key

    # One factory decides PydanticAI vs ACP for every surface (ARCH-192).
    # Chat-session specifics: the CONDOR_* env pair, the bound-Agent identity
    # header (system level — the opening context below is a user turn and loses
    # to the host's own system prompt, FEAT-025), the saved LM Studio pref as
    # the *default* base URL (a named custom endpoint still wins), and
    # strict_custom_endpoint=True so a key naming an unsaved endpoint fails
    # loudly with the guided RuntimeError instead of dying deep in httpx.
    import os

    from condor.preferences import get_agent_prefs
    from condor.runtime.llm_client import build_llm_client

    agent_prefs = get_agent_prefs(user_data) if user_data else {}
    client = build_llm_client(
        agent_key,
        mcp_servers=mcp_servers,
        permission_callback=permission_callback,
        # A bound Agent's allowlist is enforced here exactly as it is on
        # consult and loop, so an Agent has the same reach in every mode.
        allowed_tools=bound.tools or None,
        extra_env=extra_env,
        system_prompt=(
            agent_identity_header(bound.agent_slug, bound.label)
            if bound.is_agent
            else ""
        ),
        user_data=user_data,
        user_id=spec.user_id,
        default_base_url=(
            agent_prefs.get("base_url") or os.environ.get("LMSTUDIO_BASE_URL") or None
        ),
        tool_filter_mode=agent_prefs.get("tool_filter_mode"),
        strict_custom_endpoint=True,
    )

    await client.start()

    try:
        # Build initial context about server and permissions. A bound
        # specialist opens with its OWN identity and domain memory instead of
        # the chat's — that is what makes it a different brain, not a skin.
        initial_context = ""
        if bound.is_agent and spec.user_id:
            initial_context = bound_agent_context(bound, spec.user_id, spec.platform)
        elif spec.user_id:
            initial_context = build_initial_context(
                spec.user_id,
                spec.chat_id,
                user_data,
                agent_key=agent_key,
                platform=spec.platform,
                server_name=spec.server_name,
            )
        # Resolve the server name that was actually used for this session. The
        # chat default is the ambient answer, but ``chat_defaults`` is a global
        # map keyed by chat id and ``spec`` is an unvalidated request body on
        # the web, so naming someone else's chat would otherwise resolve their
        # server here (SEC-178). Every candidate is therefore held to existence
        # *and* reach, subjected on the run's own principal — the same id the
        # MCP toolset is built for, so the label and the credentials downstream
        # can never disagree about who this session belongs to.
        from config_manager import get_config_manager, get_effective_server

        cm = get_config_manager()
        subject_id, _ = spec.effective_ids()

        def usable(name: str | None) -> bool:
            return bool(name and cm.get_server(name)) and cm.has_server_access(
                subject_id, name
            )

        resolved_server = spec.server_name
        if not usable(resolved_server):
            resolved_server = get_effective_server(spec.chat_id, user_data)
        if not usable(resolved_server):
            accessible = cm.get_accessible_servers(subject_id)
            resolved_server = accessible[0] if accessible else None

        # The durable conversation behind this session. An empty id mints a new
        # one; a supplied id replays that transcript's tail into the opening
        # context. That replay *is* the whole resume mechanism — ACP has no
        # session/load, so a resumed agent is always a fresh brain that has read
        # the conversation, never the old one woken up.
        conversation_id, replay = _resolve_conversation(
            spec,
            key,
            agent_key=agent_key,
            agent_slug=bound.specialist_slug,
            server_name=bound.server_name or resolved_server,
        )

        # Caller-supplied context (e.g. a switch handoff recap) rides along as
        # spec data, so no caller needs the live session object.
        initial_context = "\n\n".join(
            part
            for part in (initial_context, replay, spec.extra_context)
            if part and part.strip()
        ).strip()

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
            server_name=bound.server_name or resolved_server,
            server_pinned=bound.server_pinned,
            user_id=spec.user_id,
            agent_slug=bound.specialist_slug,
            label=bound.label,
            conversation_id=conversation_id,
            pending_context=initial_context or None,
        )
    except Exception:
        # Something failed after start -- stop client to prevent orphan subprocess
        await client.stop()
        raise

    _sessions[raw_key] = session
    # The slot is running again, so the memory of it being reaped is stale --
    # and leaving it would list the slot twice on the next roster.
    _detached.pop(raw_key, None)
    log.info("Created agent session %s: %s (%s)", raw_key, agent_key, bound.label)
    return session


def get_session(key: SessionKey) -> AgentSession | None:
    """Get the live session object for a key, or None."""
    return _sessions.get(str(key))


def _remember_detached(session: "AgentSession") -> None:
    """File a reaped session under ``_detached`` so its slot can still be listed.

    Only a session with an owner and a durable conversation is worth
    remembering: a reattach resumes the transcript, so without one there is
    nothing for the slot to come back to.
    """
    if session.user_id is None or not session.conversation_id:
        return
    _detached[str(session.key)] = session.info().model_copy(
        update={"alive": False, "is_busy": False}
    )
    _trim_detached(session.user_id, session.key.surface)


def _trim_detached(user_id: int, surface: str) -> None:
    """Keep only the most recent MAX_DETACHED_PER_USER slots for one surface."""
    mine = [
        (raw_key, info)
        for raw_key, info in _detached.items()
        if info.user_id == user_id and info.surface == surface
    ]
    if len(mine) <= MAX_DETACHED_PER_USER:
        return
    mine.sort(key=lambda pair: pair[1].last_prompt_at or pair[1].created_at)
    for raw_key, _ in mine[: len(mine) - MAX_DETACHED_PER_USER]:
        _detached.pop(raw_key, None)


def _detached_infos(user_id: int | None) -> list[SessionInfo]:
    """The remembered slots that are still resumable, dropping the ones that aren't.

    A conversation deleted since the reap (from the dashboard, from Telegram, by
    hand on disk) has nothing to resume, and listing it would put a tab on
    screen whose first message could only fail. The check doubles as the
    garbage collection for this dict: there is no delete hook to subscribe to,
    and the read is the moment the truth is needed.
    """
    infos: list[SessionInfo] = []
    for raw_key, info in list(_detached.items()):
        if user_id is not None and info.user_id != user_id:
            continue
        if str(raw_key) in _sessions:  # respawned; the live entry is the truth
            continue
        try:
            alive_conversation = (
                conversations.get_conversation(info.user_id, info.conversation_id)
                is not None
            )
        except Exception:  # noqa: BLE001 - an unreadable id is a gone conversation
            alive_conversation = False
        if not alive_conversation:
            _detached.pop(raw_key, None)
            continue
        infos.append(info)
    return infos


def list_sessions(
    user_id: int | None = None, *, include_detached: bool = False
) -> list[SessionInfo]:
    """List every registered session, optionally filtered by owning user.

    ``include_detached`` widens the answer from "the subprocesses running right
    now" to "the slots this user has on the surface", adding the reaped ones
    with ``alive`` false (CORR-265). It is opt-in because the two are genuinely
    different questions: the REST session list and the teardown that walks live
    sessions want the narrow one, and a frontend rebuilding its tab strip wants
    the wide one.
    """
    infos: list[SessionInfo] = []
    for session in list(_sessions.values()):
        if user_id is not None and session.user_id != user_id:
            continue
        infos.append(session.info())
    if include_detached:
        infos.extend(_detached_infos(user_id))
    return infos


async def destroy_session(key: SessionKey) -> bool:
    """Destroy the session for a key. Returns True if a live session existed.

    Destroying is the deliberate one: it also forgets any memory of the slot, so
    the tab disappears from every roster instead of coming back as resumable.
    """
    return await _destroy_session_internal(key)


async def _destroy_session_internal(key: SessionKey, *, retain: bool = False) -> bool:
    """Tear down one session; ``retain`` remembers the slot as resumable.

    Every teardown funnels through here, and ``retain`` is what tells them
    apart: the reaps that keep the conversation (idle detach, LRU eviction, a
    dead subprocess swept up) pass True, and everything that means "this chat is
    over" leaves the default False, which also clears any earlier memory of the
    slot.
    """
    raw_key = str(key)
    _deny_pending_confirmations(raw_key)
    session = _sessions.pop(raw_key, None)
    if retain and session is not None:
        _remember_detached(session)
    elif not retain:
        _detached.pop(raw_key, None)
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
    # Nothing survives the process, so nothing is resumable on the other side of
    # this call either.
    _detached.clear()
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
    """Every 15s, sweep the registry once."""
    try:
        while True:
            await asyncio.sleep(15)
            await _sweep_sessions()
    except asyncio.CancelledError:
        pass


async def _sweep_sessions() -> None:
    """One health pass: reap the dead sessions, detach the idle ones.

    Two questions of the same registry, so one scan asks both rather than a
    second task asking the second — but they mean different things and are
    deliberately said differently. A dead subprocess is a fault, and the
    Telegram chat behind it is told so. An idle session is not a fault
    (PERF-226): it is a conversation nobody came back to, still holding an
    agent subprocess, the MCP tree it was spawned with, and one of the five
    slots in ``MAX_SESSIONS_PER_USER``. Since FEAT-015 the conversation
    outlives the subprocess, so retiring one is a detach the next message
    reattaches — silently, like the LRU's same-surface detach.

    Both go out through ``_destroy_session_internal``, the one funnel that also
    denies whatever the session was still asking a human to approve — and both
    ``retain`` the slot, because neither is the user saying the chat is over: a
    frontend can still list it, and the next message reattaches it (CORR-265).
    """
    ttl = TIMEOUTS.session_idle
    now = _utcnow()
    dead_keys: list[SessionKey] = []
    # (key, conversation id, seconds idle) — captured during the scan, because
    # the session is gone from the registry by the time it is logged.
    idle: list[tuple[SessionKey, str, float]] = []

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
            continue
        # A session mid-turn is never idle, however old its timestamp is:
        # ``last_prompt_at`` is stamped when the turn *starts*, so a long
        # answer would otherwise reap the subprocess writing it.
        if not ttl or session.is_busy:
            continue
        since = session.last_prompt_at or session.created_at
        seconds = (now - since).total_seconds()
        if seconds > ttl:
            idle.append((session.key, session.conversation_id, seconds))

    for key in dead_keys:
        log.warning("Health monitor: dead session %s, cleaning up", key)
        await _destroy_session_internal(key, retain=True)
        # Only Telegram sessions have a chat to notify.
        chat_id = key.telegram_chat_id
        if _health_bot and chat_id is not None:
            try:
                await _health_bot.send_message(
                    chat_id=chat_id,
                    text="Agent session ended unexpectedly. Send a message to start a new session.",
                )
            except Exception:
                log.warning("Failed to notify chat %s about dead session", chat_id)

    for key, conversation_id, seconds in idle:
        log.info(
            "Health monitor: session %s idle for %.0fs (over the %ss TTL), "
            "detaching (conversation %s is kept)",
            key,
            seconds,
            ttl,
            conversation_id or "none",
        )
        await _destroy_session_internal(key, retain=True)

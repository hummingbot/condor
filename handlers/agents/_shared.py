"""Telegram-side agent helpers, plus re-exports of the moved core (ARCH-190).

What remains here is genuinely Telegram-flavored: the chat↔agent binding and
conversation bookkeeping. The platform-neutral foundations moved down into
``condor/`` — the model catalog (:mod:`condor.llm.options`), the MCP toolset
builders (:mod:`condor.runtime.toolsets`), the danger classification
(:mod:`condor.runtime.danger`) and the context builders
(:mod:`condor.runtime.context`) — and are re-exported below so existing
handler imports keep working. New code should import from the new homes.
"""

import hashlib
import logging

# Re-exports for Telegram callers (ARCH-190) — the implementations live in
# condor/ now; condor/ itself must never import back through this module.
from condor.llm.options import (  # noqa: F401
    AGENT_OPTIONS,
    DEFAULT_AGENT,
    RECOMMENDED_AGENT,
    selectable_agent_options,
)
from condor.runtime.context import (  # noqa: F401
    _TELEGRAM_FORMATTING,
    _WEB_FORMATTING,
    COMPACT_CONTEXT_TEMPLATE,
    COMPACT_PROMPT_AUTO,
    COMPACT_PROMPT_CUSTOM_TEMPLATE,
    build_agent_context,
    build_initial_context,
    platform_formatting,
)
from condor.runtime.danger import (  # noqa: F401
    BLOCKED_TOOLS,
    DANGEROUS_AMM_ACTIONS,
    DANGEROUS_BOT_ACTIONS,
    DANGEROUS_EXECUTOR_ACTIONS,
    DANGEROUS_TOOLS,
    is_dangerous_tool_call,
    tool_call_input,
    tool_call_name,
)
from condor.runtime.toolsets import (  # noqa: F401
    _bot_id_args,
    build_mcp_servers_for_session,
    get_project_dir,
)

log = logging.getLogger(__name__)


def bind_chat_to_agent(user_data: dict | None, agent_slug: str) -> None:
    """Record (or clear) the specialist this chat talks to.

    Written by the "Talk to" picker and read by every path that spawns a session
    for the chat, which is what makes the choice outlive the subprocess that
    served it (CORR-090). An empty slug means Condor was picked, i.e. unbound.

    Only the slug field is written. Unbinding is a change of interlocutor, not
    the end of the chat: the conversation stored beside it (ARCH-101) has to
    survive picking the coordinator, so this must never drop the whole record.
    """
    from condor.preferences import set_chat_binding

    if user_data is None:
        return
    set_chat_binding(user_data, {"agent_slug": agent_slug})


def stale_binding_notice(slug: str) -> str:
    """Told once, when a bound Agent's directory is gone by the time we respawn."""
    return (
        f"The agent '{slug}' no longer exists, so this chat is back to Condor. "
        "Use /agent → Talk to if you want to bind it to another one."
    )


def resolve_chat_binding(user_data: dict | None, drop_stale: bool = True):
    """The Agent this chat is bound to, as ``(agent, stale_slug)``.

    ``agent`` is ``None`` when the chat is unbound (Condor answers) or when the
    binding could not be honoured. In that second case ``stale_slug`` names the
    agent that went missing — its directory was deleted while the chat was bound
    to it. Silently falling back to the coordinator is the bug this exists to
    prevent: it swaps the identity, the toolset, the pinned server and the
    memory scope with nothing on screen to show for it.

    ``drop_stale`` forgets such a binding, so the notice is delivered exactly
    once and every later respawn is cleanly unbound. Only the paths that spawn a
    session pass it: a surface that merely *renders* the binding must not be the
    one to consume the warning the next spawn owes the user.
    """
    from condor.agents.agent import AgentStore
    from condor.preferences import get_chat_binding

    if user_data is None:
        return None, ""

    slug = get_chat_binding(user_data).get("agent_slug") or ""
    if not slug:
        return None, ""

    agent = AgentStore().get(slug)
    if agent is None:
        log.warning("Chat is bound to unknown agent %r", slug)
        if drop_stale:
            # The agent is gone; the chat and its transcript are not. Clearing
            # the slug alone leaves the conversation to be resumed by Condor.
            bind_chat_to_agent(user_data, "")
        return None, slug

    return agent, ""


def remember_chat_conversation(user_data: dict | None, conversation_id: str) -> None:
    """Record the conversation this chat is now in, for the next respawn.

    The id lives on the in-memory session and nowhere else, so a chat that lost
    its subprocess had no way to say which transcript it belonged to and every
    respawn started blank (ARCH-101). Written after every successful spawn —
    including the ones that deliberately minted a fresh conversation, since that
    new one is what the chat is in from then on.

    An empty id is not recorded: it means the runtime could not resolve a
    conversation at all (a session with no owner, or a recording failure), which
    is no reason to forget the one the chat already had.
    """
    from condor.preferences import set_chat_binding

    if user_data is None or not conversation_id:
        return
    set_chat_binding(user_data, {"conversation_id": conversation_id})


def forget_chat_conversation(user_data: dict | None) -> None:
    """Start the chat's next spawn on a fresh conversation.

    The deliberate new-chat verbs — "New session" and the ``-`` reset — call
    this. The agent binding is untouched: a new chat is a new transcript, not a
    demotion to the coordinator.
    """
    from condor.preferences import set_chat_binding

    if user_data is None:
        return
    set_chat_binding(user_data, {"conversation_id": ""})


def stored_chat_conversation(user_data: dict | None) -> str:
    """The conversation this chat should come back to, or "" for a fresh one."""
    from condor.preferences import get_chat_binding

    if user_data is None:
        return ""
    return get_chat_binding(user_data).get("conversation_id") or ""


def conversation_token(conversation_id: str) -> str:
    """Short stable token for a conversation id, for use in callback_data.

    Telegram caps callback_data at 64 bytes. Conversation ids are only bounded
    by what the store accepts as a path segment, so the picker addresses them by
    a fixed-width digest instead of by the id itself — the payload is the same
    size whoever minted the id. It is deliberately not a list position either:
    the list is shared with the dashboard and reorders on every write (it is
    sorted by ``updated_at``), so an index rendered a minute ago can resolve to
    somebody else's conversation by the time it is tapped (CORR-097).
    """
    return hashlib.blake2s(conversation_id.encode("utf-8"), digest_size=4).hexdigest()


def find_conversation(metas: list, token: str):
    """Resolve a token from :func:`conversation_token` against a fresh listing.

    ``None`` means the conversation is no longer there — deleted from the
    dashboard, or from another chat, between the render and the tap. Callers
    must treat that as a normal outcome and say so rather than failing.
    """
    return next((m for m in metas if conversation_token(m.id) == token), None)

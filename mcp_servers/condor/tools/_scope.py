"""Whose library a tool call acts on, and who may write it.

Three tools take ``agent=`` — ``manage_skill``, ``manage_routines`` and
``manage_agent_controllers`` — and they answer one question: which agent's
files does this call touch. Only the controllers tool also asked the second
one, *may this seat write there*, so a launched specialist could rewrite a
peer's playbooks and routine code (and Condor's own, with ``agent="condor"``)
through the other two: the stores behind them are passive (MC-07).

The rule, in one place: Condor — the chat, with no specialist bound — may
target any agent. A bound specialist writes only to itself. Whether it may
*read* another agent's library is each tool's own contract and is what
``write`` carries: skills and routines allow it, controllers do not.
"""

from mcp_servers.condor.settings import settings


class ScopeError(Exception):
    """The call names a library this seat may not act on."""


class UnknownAgent(ScopeError):
    """``agent`` names no agent (and no loop that belongs to one)."""


def owner_of(target: str) -> str | None:
    """The agent slug ``target`` names — itself, or the owner of a loop key."""
    from condor.agents.agent import AgentStore
    from condor.agents.strategy import StrategyStore

    if AgentStore().get(target):
        return target
    # Legacy/convenience form: a composite loop key names its owning agent.
    s = StrategyStore().get_by_key(target)
    if s:
        return s.agent_slug
    return None


def resolve_target(
    agent: str | None, *, write: bool, what: str, loop_keys: bool = True
) -> str:
    """The agent slug this call acts on, or :class:`ScopeError`.

    ``write`` is whether the seat is about to change the library (pass ``True``
    for every action of a tool whose reads are own-only too). ``what`` names the
    library in the refusal. ``loop_keys`` admits a composite ``agent.loop`` key
    as a spelling of its owning agent.

    The refusal comes before the lookup on purpose: a specialist probing
    another library's existence with a write gets the same answer either way.
    """
    from condor.memory.paths import CHAT_SLUG

    own = settings.specialist_slug
    if not agent:
        return own or CHAT_SLUG

    if loop_keys:
        owner = owner_of(agent)
    else:
        from condor.agents.agent import AgentStore

        owner = agent if AgentStore().get(agent) is not None else None

    if own and write and owner != own:
        raise ScopeError(f"an agent manages only its own {what} — omit `agent`")
    if owner is None:
        raise UnknownAgent(f"no agent named '{agent}'")
    return owner

"""Every deed leaves a mark, whoever did it (FEAT-105).

A tick already writes down what it did: :func:`condor.agents.actions.append_actions`
puts one row per mutating tool call into ``actions.jsonl``, and
:meth:`condor.agents.ownership.BotLedger.note_deploy` puts every bot it deployed
into ``owned_bots.json``. Both files live in the run's own directory, and every
attribution surface in the codebase reads them.

Until this module, ``append_actions`` had **exactly one caller** — the tick
engine — so three of the five doors Condor's work leaves by wrote nothing at
all: a bot you asked for in the chat, an executor a delegation opened, a Deploy
pressed on ``/bots``. The attribution surfaces were not broken; they were
reporting, accurately, that nothing had told them anything.

This module is the missing half, and it is deliberately thin. It adds no file,
no filename and no row shape: a deed log *is* an action log, so
``read_actions``, ``read_owned``, ``build_deployments`` and ``latest_action``
all work on the new directories with no changes. What is genuinely new is only
two facts:

**Where a non-loop run's record lives.** A conversation's deeds go beside its
transcript, a delegation's beside its own record, and the dashboard's own
mutations under the acting user. All three are ordinary directories under
``.condor/users/{user_id}/``, so a person's whole footprint stays one directory
and retention that already prunes a conversation prunes its deeds with it.

**What its run key is.** A run key is ``{agent}.{strategy}`` everywhere in this
codebase — the scope-tree node id, the fleet map's join key, the URL's
``?scope=agent:`` value. A chat has no strategy, so it gets a reserved one.
``condor.chat`` and ``condor.ui`` are then ordinary run keys and every existing
consumer joins on them without learning a new shape. The reserved slugs are
refused as user-created strategy names (:meth:`StrategyStore.create`) so a real
strategy can never collide with one.

**This is a log, not a gate.** Nothing here can refuse anything; the namespace
rule in :mod:`condor.agents.ownership` stays the only enforcement and stays
exactly where it is. Accordingly nothing here may raise: a failed write must
never cost a deploy, so every entry point swallows its own failures the way
``append_actions`` already swallows its own.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from condor import paths
from condor.agents.actions import (
    DEPLOY_VERB,
    AgentAction,
    actions_from_tool_calls,
    append_actions,
    deployed_bot_names,
)
from condor.agents.ownership import BotLedger, bot_namespace
from condor.memory.paths import CHAT_SLUG

log = logging.getLogger(__name__)

# The pseudo-strategies. A chat, a delegation and the dashboard are runs without
# a strategy, but a run key needs two halves, so each gets a reserved slug of
# its own. Named apart rather than one "not-a-loop" bucket: "the chat deployed
# it" and "somebody pressed Deploy" are different answers to the same question,
# and a joined surface that cannot tell them apart is back to guessing.
CHAT_STRATEGY = "chat"
DELEGATION_STRATEGY = "delegation"
UI_STRATEGY = "ui"

#: Slugs a user-created strategy may not take, or its run key would collide
#: with a pseudo-run's and the two would report as one.
RESERVED_STRATEGY_SLUGS = frozenset({CHAT_STRATEGY, DELEGATION_STRATEGY, UI_STRATEGY})

# The kinds of run this module knows how to find a directory for.
KIND_CONVERSATION = "conversation"
KIND_DELEGATION = "delegation"
KIND_UI = "ui"

_STRATEGY_FOR_KIND = {
    KIND_CONVERSATION: CHAT_STRATEGY,
    KIND_DELEGATION: DELEGATION_STRATEGY,
    KIND_UI: UI_STRATEGY,
}

# Outside a loop there is no tick. Zero is the honest value rather than a
# fabricated number: every reader already treats the tick column as allowed to
# say nothing (``build_deployments``: "the tick column is the one heuristic, and
# it is allowed to say nothing").
NO_TICK = 0


@dataclass(frozen=True)
class DeedOwner:
    """Who did a thing, and where their record of it lives.

    ``agent_slug`` follows :mod:`condor.runtime.binding`'s settled rule: an
    empty slug is not "no agent", it is the default one, Condor. So the owner of
    an unbound chat's work is ``condor`` and the owner of a bound specialist's
    is that specialist, with no new concept and no new taxonomy.
    """

    kind: str
    user_id: int | str | None = None
    #: The conversation id or delegation task id. Unused for the dashboard,
    #: whose deeds are per-user and not per-anything-else.
    ref: str = ""
    agent_slug: str = ""

    @property
    def agent(self) -> str:
        return self.agent_slug or CHAT_SLUG

    @property
    def strategy(self) -> str:
        return _STRATEGY_FOR_KIND.get(self.kind, UI_STRATEGY)


def for_conversation(
    user_id: int | str | None, conversation_id: str, agent_slug: str = ""
) -> DeedOwner:
    """The owner of a chat turn's deeds."""
    return DeedOwner(
        kind=KIND_CONVERSATION,
        user_id=user_id,
        ref=conversation_id or "",
        agent_slug=agent_slug or "",
    )


def for_delegation(
    user_id: int | str | None, task_id: str, agent_slug: str = ""
) -> DeedOwner:
    """The owner of a delegation's or consult's deeds."""
    return DeedOwner(
        kind=KIND_DELEGATION,
        user_id=user_id,
        ref=task_id or "",
        agent_slug=agent_slug or "",
    )


def for_ui(user_id: int | str | None) -> DeedOwner:
    """The owner of a mutation made straight from the dashboard.

    The agent is Condor because nothing else asked for it, and the *acting
    person* is the first segment of the path the record is written to — which is
    a stronger statement than a field in a row, because a route cannot forget to
    supply it.
    """
    return DeedOwner(kind=KIND_UI, user_id=user_id)


def run_key_for(owner: DeedOwner) -> str:
    """``{agent}.{strategy}`` — ``"condor.chat"``, ``"brigado.chat"``, ``"condor.ui"``."""
    return f"{owner.agent}.{owner.strategy}"


def attribution_tag(owner: DeedOwner) -> str:
    """The ``controller_id`` this run's executors carry — ``""`` when it has none.

    A loop session tags its positions with ``agent_id =
    "{agent_slug}.{strategy_slug}_{N}"`` (``engine.py``), and that tag is the key
    the whole fleet map hangs off: :mod:`condor.agents.fleet_map` matches an
    executor to its owner by it, and ``fetch_agent_performance`` asks the
    trading API for exactly it. A conversation had no such tag, so an executor a
    chat opened was attributable to nothing — not to a bug in the join, but
    because nobody ever gave the model a string to pass (CORR-325).

    This is that string, and it is deliberately the *same shape*, one rule for
    both: ``{run_key}_{ref}``, with the conversation id where the session number
    goes. ``condor.chat_c-4f2a``, ``brigado.chat_c-4f2a``. No consumer has to
    learn a second format — the run key half is already the scope-tree node id
    and the join key, and the ref half is already how a deed names its run
    (:class:`~condor.agents.deed_index.OwnerRef`).

    A run with no ``ref`` — the dashboard, whose deeds are per-user and not
    per-anything-else — gets ``""``. That is not a tag anything could resolve,
    and returning it as one would mean asking the API for the empty
    ``controller_id``, which is every untagged executor on the server. An owner
    that cannot be named is better named as nothing.
    """
    return tag_for(run_key_for(owner), owner.ref)


def tag_for(run_key: str, ref: str) -> str:
    """:func:`attribution_tag`'s rule, over two halves already resolved.

    Separate from the function above because the readers do not have a
    :class:`DeedOwner` and should not have to fake one:
    :mod:`condor.agents.deed_index` recovers a run key from a ledger on disk,
    not from a live owner. Both ends of the join therefore spell the tag with
    the same call, which is the only way a tag written by one and looked up by
    the other cannot drift apart.
    """
    return f"{run_key}_{ref}" if run_key and ref else ""


def deed_dir(owner: DeedOwner) -> Path | None:
    """Where this owner's ``actions.jsonl`` lives, or ``None`` if nowhere.

    ``None`` for an owner with no user behind it or, for the two kinds that need
    one, no reference: a deed with nowhere to go is dropped rather than written
    to a guessed location. Never raises — an id that is not a safe path segment
    is a reason to keep no record, not to fail a deploy.
    """
    if owner.user_id is None or str(owner.user_id) == "":
        return None
    try:
        if owner.kind == KIND_CONVERSATION:
            if not owner.ref:
                return None
            return paths.conversation_dir(owner.user_id, owner.ref)
        if owner.kind == KIND_DELEGATION:
            if not owner.ref:
                return None
            return paths.delegation_dir(owner.user_id, owner.ref)
        if owner.kind == KIND_UI:
            return paths.ui_dir(owner.user_id)
    except Exception:  # noqa: BLE001 - an unsafe id means no record, not a 500
        log.debug("deeds: no directory for %r", owner, exc_info=True)
    return None


def record_deeds(owner: DeedOwner, tool_calls: list[dict[str, Any]]) -> None:
    """Write down what a run's tool calls actually did. Never raises.

    The three lines the tick engine already runs — fold the calls into rows,
    append them, claim any bot they deployed — lifted so a second caller does
    not copy them. The engine's own call site stays where it is: a refactor of
    the one working path is risk this feature has no reason to take.

    A run that mutated nothing writes nothing and **creates no directory**. That
    is the common case for a chat and it has to stay free, or every "what's the
    portfolio?" would leave a file behind.
    """
    try:
        actions = actions_from_tool_calls(
            tool_calls or [], tick=NO_TICK, at=time.time()
        )
        if not actions:
            return
        _write(owner, actions, deployed_bot_names(tool_calls or []))
    except Exception:  # noqa: BLE001 - a lost record must never cost the deed
        log.debug("deeds: could not record tool calls for %r", owner, exc_info=True)


def record_direct(
    owner: DeedOwner,
    *,
    verb: str,
    summary: str,
    ok: bool = True,
    subject: str = "",
    error: str = "",
) -> None:
    """Write down a mutation made without a model in the loop. Never raises.

    The dashboard's routes hold their arguments as a typed request body, which
    is a *better* record than a folded tool call rather than a worse one — so
    they state the verb and the line directly instead of being reverse-engineered
    into a tool call first. ``verb`` uses the same vocabulary the log already
    speaks (``"manage_bots:deploy"``, ``"stop_executor"``) so a reader joining on
    it cannot tell which door a row came through, which is the point.
    """
    try:
        tool = verb.split(":", 1)[0]
        action = AgentAction(
            tick=NO_TICK,
            at=time.time(),
            tool=tool,
            verb=verb,
            summary=summary,
            ok=ok,
            error="" if ok else (error or "did not complete"),
            subject=subject if verb == DEPLOY_VERB else "",
        )
        deployed = [subject] if (ok and subject and verb == DEPLOY_VERB) else []
        _write(owner, [action], deployed)
    except Exception:  # noqa: BLE001 - a lost record must never cost the deed
        log.debug("deeds: could not record %s for %r", verb, owner, exc_info=True)


def _write(owner: DeedOwner, actions: list[AgentAction], deployed: list[str]) -> None:
    """Append the rows, then claim the bots. Callers guard their own failures."""
    directory = deed_dir(owner)
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    append_actions(directory, actions)
    if not deployed:
        return
    # ``enforced=False`` is load-bearing: a chat is not namespace-bound and must
    # not be. The ledger here answers *since when*, never *may you* — the
    # namespace rule stays the only gate, at the tick's permission callback.
    ledger = BotLedger(
        namespace=bot_namespace(owner.agent, owner.strategy),
        session_dir=directory,
        enforced=False,
    )
    for name in deployed:
        ledger.note_deploy(name)

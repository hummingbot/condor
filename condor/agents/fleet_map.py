"""Who owns which trading, and what their loop is doing (FEAT-096).

The fleet page can say what is trading; it cannot say **whose** it is. A bot an
agent deployed is an ordinary bot row, and a standalone executor an agent
created hangs off the fleet root under its ``controller_id`` as if that string
were a bot name. This module is the registry that lets the browser tell them
apart, and it is deliberately the *cheap* one.

Both ownership links already exist and are enforced at the tool call, so
attribution needs no new trading-API call — only the rule and the names:

- **Bots.** A strategy may only operate bots inside its namespace
  ``{agent_slug}-{strategy_slug}`` (:mod:`condor.agents.ownership`, FEAT-017),
  plus the legacy names it *declares* — a ``bot_name`` configured before the
  convention existed, owned by its agent though the prefix does not prove it.
- **Standalone executors.** ``create_*_executor`` is refused unless the
  executor's ``controller_id`` equals the session's
  ``agent_id = "{agent_slug}.{strategy_slug}_{N}"`` (``risk.py``). There is no
  untagged agent executor, so the set of agent ids on disk is the tag set.

Why the rule stays here rather than being re-derived in TypeScript: it is
enforced here, and ``declared_bots`` is not derivable from any name at all.

**No Hummingbot API call anywhere in this module.** The registry half is a
directory walk (memoised, see :data:`REGISTRY_TTL`); the live half is
:class:`~condor.runtime.loops.LoopSupervisor`'s in-memory engines plus one small
journal read per *live* engine, never cached — that read is the band's
freshness, and the whole point of the endpoint being separate from ``GET
/agents`` (which fans out a performance fetch per session of every strategy).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any

from condor.agents.ownership import bot_namespace, declared_names

log = logging.getLogger(__name__)

# How long the filesystem half is reused. The whole map is one small object, so
# it is memoised as a unit rather than per strategy. A strategy created inside
# the window is still not *missed*: a live loop merges its own owner in below,
# which is the only half that changes on a five-second poll.
REGISTRY_TTL = 60.0

_LAST_ACTION_RE = re.compile(r"^Last action:\s*(.+)$", re.MULTILINE)


@dataclass
class LiveLoop:
    """The tick loop currently driving a strategy, as the header band reads it."""

    agent_id: str
    session_num: int
    #: ``running`` | ``paused``. A strategy with no engine has no ``LiveLoop``
    #: at all, which the band reads as *idle*.
    status: str
    tick_count: int
    #: Epoch seconds, or 0 when the loop has not ticked yet.
    last_tick_at: float
    frequency_sec: int
    #: The ``Last action:`` line of the journal's Summary — what the agent last
    #: *said*. Kept under its own name and meaning; what it last *did* is the
    #: separate field below, and the band shows the two as separate statements.
    last_action: str = ""
    #: What the loop last **did**: ``asdict`` of the session's most recent
    #: :class:`~condor.agents.actions.AgentAction`, or ``None`` for a session
    #: that has not acted (or predates the log — nothing is backfilled).
    last_did: dict | None = None
    last_error: str = ""


@dataclass
class FleetOwner:
    """One ``(agent, strategy)`` and everything needed to attribute its work."""

    #: ``"brigado.brl_mm"`` — the node id in the scope tree and the join key.
    run_key: str
    agent_slug: str
    agent_name: str
    strategy_slug: str
    strategy_name: str
    #: ``{agent_slug}-{strategy_slug}``: the prefix that proves bot ownership.
    namespace: str
    #: Configured bot names that sit *outside* the namespace (legacy escape
    #: hatch). Matched exactly rather than by prefix.
    declared_bots: list[str] = field(default_factory=list)
    #: Every ``"{run_key}_{N}"`` on disk — the executor ``controller_id`` tag set.
    agent_ids: list[str] = field(default_factory=list)
    live: LiveLoop | None = None


# ── The registry half: the filesystem, memoised ──

_registry_cache: tuple[float, list[FleetOwner]] | None = None


def reset_fleet_map_cache() -> None:
    """Drop the memoised registry — for tests and for a strategy just created."""
    global _registry_cache
    _registry_cache = None


def _build_registry() -> list[FleetOwner]:
    from condor.agents.agent import AgentStore
    from condor.agents.sessions_index import enumerate_agent_ids
    from condor.agents.strategy import StrategyStore

    agents = {a.slug: a for a in AgentStore().list_all()}
    owners: list[FleetOwner] = []
    for strategy in StrategyStore().list_all():
        namespace = bot_namespace(strategy.agent_slug, strategy.slug)
        agent = agents.get(strategy.agent_slug)
        try:
            ids = sorted(
                agent_id
                for agent_id, _num, _kind in enumerate_agent_ids(
                    strategy.key, strategy.dir
                )
            )
        except Exception:
            log.debug("fleet_map: could not enumerate ids for %s", strategy.key)
            ids = []
        owners.append(
            FleetOwner(
                run_key=strategy.key,
                agent_slug=strategy.agent_slug,
                agent_name=agent.name if agent else strategy.agent_slug,
                strategy_slug=strategy.slug,
                strategy_name=strategy.name,
                namespace=namespace,
                declared_bots=declared_names(strategy.default_config or {}, namespace),
                agent_ids=ids,
            )
        )
    return owners


def _registry(now: float) -> list[FleetOwner]:
    global _registry_cache
    if _registry_cache is not None and now - _registry_cache[0] < REGISTRY_TTL:
        return _registry_cache[1]
    owners = _build_registry()
    _registry_cache = (now, owners)
    return owners


# ── The live half: in-memory engines, never cached ──


def read_last_action(journal: Any) -> str:
    """The ``Last action:`` line of the journal's Summary, or ``""``.

    One small file read per live engine (the journal caches its own text), and
    the reason the live half is not memoised: the band's job is to say what the
    loop is doing *now*.

    Public because it has a second caller: the strategy view's `RunningInstance`
    reports the same pulse the fleet band does, and a loop must not be able to
    say two different things about itself on two screens.
    """
    if journal is None:
        return ""
    try:
        match = _LAST_ACTION_RE.search(journal.read_summary() or "")
    except Exception:
        return ""
    return match.group(1).strip() if match else ""


def read_last_did(engine: Any) -> dict | None:
    """The last mutating tool call of this engine's session, or ``None``.

    One tail read of ``actions.jsonl`` per *live* engine — strictly cheaper than
    the ``read_summary()`` above it — and uncached for the same reason: the
    band's job is to say what the loop is doing now.
    """
    from dataclasses import asdict

    from condor.agents.actions import latest_action

    try:
        action = latest_action(getattr(engine, "session_dir", None))
    except Exception:
        return None
    return asdict(action) if action is not None else None


def _live_owners() -> dict[str, tuple[FleetOwner, LiveLoop]]:
    """Every registered engine, keyed by run key, newest session winning.

    The owner is rebuilt from the engine rather than looked up, so a loop whose
    strategy the memoised registry has not seen yet still names itself.
    """
    from condor.runtime.loops import get_supervisor

    out: dict[str, tuple[FleetOwner, LiveLoop]] = {}
    for engine in get_supervisor().all().values():
        try:
            agent, strategy = engine.agent, engine.strategy
            run_key = f"{agent.slug}.{strategy.slug}"
            session_num = int(getattr(engine, "session_num", 0) or 0)
            seen = out.get(run_key)
            if seen is not None and seen[1].session_num >= session_num:
                continue
            journal = getattr(engine, "journal", None)
            config = getattr(engine, "config", {}) or {}
            namespace = bot_namespace(agent.slug, strategy.slug)
            out[run_key] = (
                FleetOwner(
                    run_key=run_key,
                    agent_slug=agent.slug,
                    agent_name=agent.name,
                    strategy_slug=strategy.slug,
                    strategy_name=strategy.name,
                    namespace=namespace,
                    declared_bots=declared_names(config, namespace),
                ),
                LiveLoop(
                    agent_id=getattr(engine, "agent_id", "") or "",
                    session_num=session_num,
                    status=engine.status,
                    tick_count=int(getattr(journal, "tick_count", 0) or 0),
                    last_tick_at=float(getattr(engine, "_last_tick_at", 0.0) or 0.0),
                    frequency_sec=int(config.get("frequency_sec", 60) or 60),
                    last_action=read_last_action(journal),
                    last_did=read_last_did(engine),
                    last_error=str(getattr(engine, "_last_error", "") or ""),
                ),
            )
        except Exception:
            log.debug("fleet_map: skipping an unreadable engine", exc_info=True)
    return out


# ── The map ──


def build_fleet_map(now: float | None = None) -> list[FleetOwner]:
    """Every ``(agent, strategy)`` that could own trading, live or not.

    A strategy with no engine still appears — its bots may still be trading and
    its finished work is still its own; ``live`` is ``None`` and the header
    reads *idle*.
    """
    stamp = time.time() if now is None else now
    # Copied, so the live half never leaks into the memoised registry. Shallow
    # is enough: the lists below are replaced, never mutated in place.
    owners = [replace(owner) for owner in _registry(stamp)]
    by_key = {owner.run_key: owner for owner in owners}

    for run_key, (stub, loop) in _live_owners().items():
        owner = by_key.get(run_key)
        if owner is None:
            owner = stub
            by_key[run_key] = owner
            owners.append(owner)
        owner.live = loop
        # A session that opened inside the memoisation window is not yet in the
        # enumerated ids, and it is exactly the one tagging executors right now.
        if loop.agent_id and loop.agent_id not in owner.agent_ids:
            owner.agent_ids = sorted([*owner.agent_ids, loop.agent_id])

    owners.sort(key=lambda owner: owner.run_key)
    return owners

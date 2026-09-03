"""Who made this, when no name proves it (FEAT-106).

:mod:`condor.agents.fleet_map` ships two ownership rules to the browser, and
both are **prescriptive**: the ``{agent}-{strategy}`` namespace and
``declared_bots`` say what a *loop* is allowed to touch. Neither can attribute a
bot you asked Condor for in the chat under a name you chose, so everything a
human asked Condor to do arrived at ``/bots`` as one dishonest word,
``Unattributed`` — a bucket doing the work of three unrelated facts.

FEAT-105 made the missing half exist: every door Condor's work leaves by now
writes the same two files a tick writes, ``actions.jsonl`` and
``owned_bots.json``, in the run's own directory. This module is the reader. It
turns those records into one map, ``bot base → OwnerRef``, and the fleet map
carries it out beside the rules it supplements.

**Observed, never enforced.** A namespace answer is a *proof* — the tick's
permission callback refused everything else. A deed answer is a *report*: it
says what was recorded, and a record can be stale (a bot destroyed and its name
reused). So the join in the browser tries this index **last**, after both
enforced rules, and marks what it attributes as such. Nothing here is a gate and
nothing here can refuse anything.

**Bots, not executors.** :mod:`condor.agents.actions` states its own limit in
writing: a row records a call's *arguments* and never its result, so "created
executor 4f2a" is nowhere on disk. An executor is therefore attributed the way
it already was — through its bot, or through the ``agent_id`` its
``controller_id`` carries — and this index does not pretend to a second key it
has no data for.

**The walk is bounded by deeds, not by history.** Most conversations deploy
nothing: they have no ``owned_bots.json`` and no ``actions.jsonl``, so they cost
one :func:`~pathlib.Path.exists` and are skipped. A run with a ledger reads that
one small fixed-size JSON object and stops there; ``actions.jsonl`` is opened
only for a run that has deeds and *no* ledger. That keeps the reads proportional
to what was done rather than to how much has been said, which is the failure
mode this design was chosen against. Memoised on :data:`INDEX_TTL`, like the
registry it rides beside, and it makes **no Hummingbot API call** — the promise
that licenses the five-second poll of ``/bots`` is not weakened by an index that
never leaves the filesystem.

**Two sources, and only one of them can date the ledger.** The bot map reads
every run's ledger — a chat's, a delegation's, the dashboard's, *and* a loop
session's, because a session ledger is the same file recording the same deed and
it is the only record of a bot a session deployed outside its own namespace
(``ema_trend_loop``, on this install, owned by ``directional_trader`` and
provable no other way). But :attr:`DeedIndex.since` — the instant before which
Condor did *not* write down everything it did — is computed from the
FEAT-105 doors alone. A session ledger predates complete coverage by months, so
letting it set the cut would rename every unrecorded chat deploy of that era
"outside Condor", which is a lie. When no such deed exists yet, ``since`` is
``0.0`` and the honest reading is that nothing can be called outside.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from condor import paths
from condor.agents.actions import (
    ACTIONS_FILENAME,
    DEPLOY_VERB,
    MAX_ACTION_LINES,
    read_actions,
)
from condor.agents.deeds import CHAT_STRATEGY, DELEGATION_STRATEGY, UI_STRATEGY
from condor.agents.ownership import (
    read_ledger_namespace,
    read_owned,
    strip_deploy_suffix,
)
from condor.memory.paths import CHAT_SLUG

log = logging.getLogger(__name__)

#: How long the walk is reused. The registry half of the fleet map is memoised
#: for the same minute and for the same reason: the map is polled every five
#: seconds by a page that only needs the filesystem to be roughly current.
INDEX_TTL = 60.0

#: What a pseudo-run is called when a surface wants words rather than slugs.
#: The slugs themselves are :mod:`condor.agents.deeds`'s and are the join key.
PSEUDO_STRATEGY_NAMES = {
    CHAT_STRATEGY: "Chat",
    DELEGATION_STRATEGY: "Delegation",
    UI_STRATEGY: "Dashboard",
}


@dataclass(frozen=True)
class OwnerRef:
    """The run a record was traced back to, and the deed that says so."""

    #: ``"condor.chat"``, ``"brigado.delegation"``, ``"directional_trader.ema_trend_loop"``.
    run_key: str
    #: The conversation id, the delegation task id, ``"ui"``, or ``"s3"``.
    run_id: str
    #: Epoch seconds the deed happened. Also how a name reused by a second run
    #: is resolved: the newest claim wins.
    at: float


@dataclass(frozen=True)
class DeedIndex:
    """Everything the records can attribute, and how far back they reach."""

    #: Bot **base** names (no ``-20260731-101500`` deploy suffix) → who made it.
    bots: dict[str, OwnerRef] = field(default_factory=dict)
    #: Epoch seconds of the earliest deed written by a door FEAT-105 wired, or
    #: ``0.0`` when there is none. Before this instant Condor's record of its own
    #: work is incomplete, so an unattributed record cannot be judged; after it,
    #: an unattributed record was made by something that is not Condor. One
    #: timestamp, and it is the whole difference between the two honest buckets.
    since: float = 0.0

    def run_keys(self) -> list[str]:
        """The runs this index can attribute something to, sorted."""
        return sorted({ref.run_key for ref in self.bots.values()})

    def owner_of(self, bot_name: str) -> OwnerRef | None:
        """The run that made this bot, by base name. Deploy suffix tolerated."""
        base = strip_deploy_suffix((bot_name or "").strip())
        return self.bots.get(base) if base else None


# ── The walk ──


def _earliest(values: Iterable[float]) -> float:
    """The smallest positive value, or ``0.0`` — "nothing said when"."""
    positive = [float(v) for v in values if v and float(v) > 0]
    return min(positive) if positive else 0.0


def _claim(bots: dict[str, OwnerRef], base: str, ref: OwnerRef) -> None:
    """Record a run's claim on a bot base, newest deed winning.

    Name reuse is the one way a deed can lie: a bot deleted and a new one
    deployed under the same name inherits the old record. Preferring the newest
    claim bounds that to the window between the two deploys, and the enforced
    rules outrank this index entirely either way.
    """
    name = strip_deploy_suffix((base or "").strip())
    if not name:
        return
    seen = bots.get(name)
    if seen is None or ref.at >= seen.at:
        bots[name] = ref


def _pseudo_runs() -> Iterator[tuple[Path, str, str]]:
    """Every chat, delegation and dashboard run on disk: dir, slug, run id.

    The three doors FEAT-105 wired, and the only ones whose records prove *when*
    Condor's log became complete. One ``iterdir`` per user per kind; the runs
    themselves are not opened here.
    """
    for user_id in paths.iter_user_ids():
        try:
            roots = (
                (paths.conversations_dir(user_id), CHAT_STRATEGY),
                (paths.delegations_dir(user_id), DELEGATION_STRATEGY),
            )
            ui_dir = paths.ui_dir(user_id)
        except Exception:  # noqa: BLE001 - an unsafe id indexes nothing
            log.debug("deed_index: skipping user %r", user_id, exc_info=True)
            continue
        for root, strategy in roots:
            try:
                children = sorted(root.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir():
                    yield child, strategy, child.name
        yield ui_dir, UI_STRATEGY, UI_STRATEGY


def _loop_runs() -> Iterator[tuple[Path, str, str]]:
    """Every loop session on disk: dir, its run key, and ``"s{N}"``.

    A session's ledger is the same file saying the same thing, and it is the
    only record that can name a bot a session deployed *outside* its namespace
    and never declared. Experiments are deliberately absent: a dry run's ledger
    is in-memory only (``BotLedger`` with no path), so there is nothing to read.
    """
    from condor.agents.sessions_index import SESSION_DIRNAMES
    from condor.agents.strategy import StrategyStore

    try:
        strategies = StrategyStore().list_all()
    except Exception:  # noqa: BLE001 - no registry is an empty index, not a 500
        log.debug("deed_index: could not list strategies", exc_info=True)
        return
    for strategy in strategies:
        for dirname in SESSION_DIRNAMES:
            try:
                children = sorted((strategy.dir / dirname).iterdir())
            except OSError:
                continue
            for child in children:
                if not child.is_dir() or not child.name.startswith("session_"):
                    continue
                try:
                    num = int(child.name.split("_", 1)[1])
                except (ValueError, IndexError):
                    continue
                yield child, strategy.key, f"s{num}"


def _run_key_of(directory: Path, strategy: str) -> str:
    """``{agent}.{strategy}`` for a pseudo-run, read off the ledger it wrote.

    A conversation's directory is named after the conversation, so who was bound
    to it is nowhere in the path. The ledger records the namespace it was
    constructed with (``brigado-chat``), and the strategy half is known from the
    directory the run was found in, so the agent half is what remains. An
    unbound turn — the common case — resolves to Condor, which is
    ``binding.py``'s settled rule and not a fallback.
    """
    namespace = read_ledger_namespace(directory)
    suffix = f"-{strategy}"
    agent = namespace[: -len(suffix)] if namespace.endswith(suffix) else ""
    return f"{agent or CHAT_SLUG}.{strategy}"


def _index_pseudo_run(
    directory: Path, strategy: str, run_id: str, bots: dict[str, OwnerRef]
) -> float:
    """Index one chat/delegation/dashboard run; return its earliest deed.

    Ledger first, and for a run that has one that is the only file opened. The
    fallback below is for the run that has deeds and *no* ledger — a turn that
    stopped a bot rather than deploying one (nothing to own, but it still dates
    the log), or the narrow window in which a deed's rows landed and its ledger
    write did not.
    """
    owned = read_owned(directory)
    if owned:
        run_key = _run_key_of(directory, strategy)
        for bot in owned:
            _claim(bots, bot.base, OwnerRef(run_key, run_id, bot.since))
        return _earliest(bot.since for bot in owned)
    if not (directory / ACTIONS_FILENAME).exists():
        return 0.0
    rows = read_actions(directory, limit=MAX_ACTION_LINES)
    if not rows:
        return 0.0
    # No ledger means no namespace was written down, so the acting agent is
    # unrecoverable and the default one is the honest answer.
    run_key = f"{CHAT_SLUG}.{strategy}"
    for row in rows:
        if row.verb == DEPLOY_VERB and row.ok and row.subject:
            _claim(bots, row.subject, OwnerRef(run_key, run_id, row.at))
    return _earliest(row.at for row in rows)


def _build() -> DeedIndex:
    bots: dict[str, OwnerRef] = {}
    firsts: list[float] = []
    for directory, strategy, run_id in _pseudo_runs():
        try:
            firsts.append(_index_pseudo_run(directory, strategy, run_id, bots))
        except Exception:  # noqa: BLE001 - one unreadable run is not the fleet
            log.debug("deed_index: unreadable run %s", directory, exc_info=True)
    for directory, run_key, run_id in _loop_runs():
        try:
            for bot in read_owned(directory):
                _claim(bots, bot.base, OwnerRef(run_key, run_id, bot.since))
        except Exception:  # noqa: BLE001
            log.debug("deed_index: unreadable session %s", directory, exc_info=True)
    return DeedIndex(bots=bots, since=_earliest(firsts))


# ── The memo ──

_cache: tuple[float, DeedIndex] | None = None


def reset_deed_index_cache() -> None:
    """Drop the memoised walk — for tests and for a deed just written."""
    global _cache
    _cache = None


def build_deed_index(now: float | None = None) -> DeedIndex:
    """Every record Condor's own logs can attribute, memoised for a minute."""
    global _cache
    stamp = time.time() if now is None else now
    if _cache is not None and stamp - _cache[0] < INDEX_TTL:
        return _cache[1]
    index = _build()
    _cache = (stamp, index)
    return index

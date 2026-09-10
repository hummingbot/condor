"""Who made this, when no name proves it (FEAT-106).

:mod:`condor.agents.fleet_map` ships two ownership rules to the browser, and
both are **prescriptive**: the ``{agent}-{strategy}`` namespace and
``declared_bots`` say what a *loop* is allowed to touch. Neither can attribute a
bot you asked Condor for in the chat under a name you chose, so everything a
human asked Condor to do arrived at ``/bots`` as one dishonest word,
``Unattributed`` — a bucket doing the work of three unrelated facts.

FEAT-105 made the missing half exist, and CORR-622 finished it: every door
Condor's work leaves by -- the chat, a delegation, the dashboard and Telegram --
now writes the same two files a tick writes, ``actions.jsonl`` and
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

**Two sources, and neither of them dates the ledger.** The bot map reads every
run's ledger — a chat's, a delegation's, Telegram's, the dashboard's, *and* a
loop session's, because a session ledger is the same file recording the same
deed and it is the only record of a bot a session deployed outside its own
namespace (``ema_trend_loop``, on this install, owned by ``directional_trader``
and provable no other way). But :attr:`DeedIndex.since` — the instant before
which Condor did *not* write down everything it did — is **not** read off these
rows at all. A row proves a deed happened; nothing in it proves that the door
next to it was recording too, and the oldest row on disk is therefore an
*over*-claim of coverage exactly when a door was wired late (CORR-622: the
Telegram one, for a year). Coverage is a fact about the running build, so
:func:`~condor.agents.deeds.coverage_since` stamps it instead, and this module
reports what that stamp says.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from condor import paths
from condor.agents.actions import (
    ACTIONS_FILENAME,
    DEPLOY_VERB,
    MAX_ACTION_LINES,
    read_actions,
)
from condor.agents.deeds import (
    CHAT_STRATEGY,
    DELEGATION_STRATEGY,
    TELEGRAM_STRATEGY,
    UI_STRATEGY,
    coverage_since,
    tag_for,
)
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
    TELEGRAM_STRATEGY: "Telegram",
}

#: The pseudo-runs with no reference of their own: one directory per person,
#: not one per conversation or task. They are the two that can never carry an
#: attribution tag (:func:`~condor.agents.deeds.attribution_tag` gives a
#: ref-less owner ``""``), and the two whose ``run_id`` is their own slug.
REFLESS_STRATEGIES = frozenset({UI_STRATEGY, TELEGRAM_STRATEGY})


@dataclass(frozen=True)
class OwnerRef:
    """The run a record was traced back to, and the deed that says so."""

    #: ``"condor.chat"``, ``"brigado.delegation"``, ``"directional_trader.ema_trend_loop"``.
    run_key: str
    #: The conversation id, the delegation task id, ``"ui"``, ``"telegram"``
    #: or ``"s3"``.
    run_id: str
    #: Epoch seconds the deed happened. Also how a name reused by a second run
    #: is resolved: the newest claim wins.
    at: float


@dataclass(frozen=True)
class DeedIndex:
    """Everything the records can attribute, and how far back they reach."""

    #: Bot **base** names (no ``-20260731-101500`` deploy suffix) → who made it.
    bots: dict[str, OwnerRef] = field(default_factory=dict)
    #: Run key → every executor ``controller_id`` tag a run of it could have
    #: set, sorted (CORR-325). The pseudo-run half of what
    #: ``sessions_index.enumerate_agent_ids`` already answers for loops, and the
    #: reason it has to live here: a conversation's tag is
    #: ``{run_key}_{conversation_id}``, and the conversation ids are only
    #: discoverable by the walk this index is already doing.
    #:
    #: Chats and delegations only. The dashboard and Telegram are a person
    #: pressing a button — there is no model to hand a tag to, so neither can
    #: ever have set one, and listing a tag it could not have used would invite
    #: a match that is a lie.
    tags: dict[str, list[str]] = field(default_factory=dict)
    #: Epoch seconds from which this install has been recording at *every*
    #: door (:func:`~condor.agents.deeds.coverage_since`), or ``0.0`` when that
    #: is unknown. Before this instant Condor's record of its own work is
    #: incomplete, so an unattributed record cannot be judged; after it, an
    #: unattributed record was made by something that is not Condor. One
    #: timestamp, and it is the whole difference between the two honest buckets.
    #:
    #: It is the *stamp* and not the oldest deed on disk, because those two
    #: answer different questions: a deed says something was recorded, only the
    #: stamp says everything was (CORR-622).
    since: float = 0.0

    def run_keys(self) -> list[str]:
        """The runs this index can attribute something to, sorted.

        Both halves, because a run that opened an executor and deployed no bot
        is still a run that owns trading. Deriving this from ``bots`` alone was
        exactly why such a conversation got no fleet-map owner row at all, and
        so had nowhere for its executors to hang (CORR-325).
        """
        return sorted({ref.run_key for ref in self.bots.values()} | set(self.tags))

    def owner_of(self, bot_name: str) -> OwnerRef | None:
        """The run that made this bot, by base name. Deploy suffix tolerated."""
        base = strip_deploy_suffix((bot_name or "").strip())
        return self.bots.get(base) if base else None


# ── The walk ──


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
    """Every chat, delegation, Telegram and dashboard run on disk: dir, slug, id.

    Every door outside a loop, which is the set that has to stay whole: a door
    missing from this walk is a door whose deeds are on disk and attributed to
    nobody. One ``iterdir`` per user per kind; the runs themselves are not
    opened here.
    """
    for user_id in paths.iter_user_ids():
        try:
            roots = (
                (paths.conversations_dir(user_id), CHAT_STRATEGY),
                (paths.delegations_dir(user_id), DELEGATION_STRATEGY),
            )
            refless = (
                (paths.ui_dir(user_id), UI_STRATEGY),
                (paths.telegram_dir(user_id), TELEGRAM_STRATEGY),
            )
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
        for directory, strategy in refless:
            yield directory, strategy, strategy


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
                children = sorted((strategy.home / dirname).iterdir())
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


def _note_tag(
    tags: dict[str, list[str]], strategy: str, run_key: str, run_id: str
) -> None:
    """Record the ``controller_id`` a run of this kind could have set.

    Skips the ref-less doors, the dashboard and Telegram: their ``run_id`` is
    their own slug rather than a reference to anything, which is the same fact
    :func:`~condor.agents.deeds.attribution_tag` states from the other side by
    giving a ref-less owner no tag. Kept as one rule read twice rather than a
    second opinion about who can be tagged. There is also nobody to hand a tag
    to at either door — a button press has no model in it.
    """
    if strategy in REFLESS_STRATEGIES:
        return
    tag = tag_for(run_key, run_id)
    if tag and tag not in tags.setdefault(run_key, []):
        tags[run_key].append(tag)


def _index_pseudo_run(
    directory: Path,
    strategy: str,
    run_id: str,
    bots: dict[str, OwnerRef],
    tags: dict[str, list[str]],
) -> None:
    """Index one chat/delegation/Telegram/dashboard run.

    Ledger first, and for a run that has one that is the only file opened. The
    fallback below is for the run that has deeds and *no* ledger — a turn whose
    rows name a bot nothing claimed, or the narrow window in which a deed's rows
    landed and its ledger write did not. A run that only stopped things owns
    nothing and is indexed as nothing.
    """
    owned = read_owned(directory)
    if owned:
        run_key = _run_key_of(directory, strategy)
        _note_tag(tags, strategy, run_key, run_id)
        for bot in owned:
            _claim(bots, bot.base, OwnerRef(run_key, run_id, bot.since))
        return
    if not (directory / ACTIONS_FILENAME).exists():
        return
    rows = read_actions(directory, limit=MAX_ACTION_LINES)
    if not rows:
        return
    # No ledger means no namespace was written down, so the acting agent is
    # unrecoverable and the default one is the honest answer.
    run_key = f"{CHAT_SLUG}.{strategy}"
    # Before the deploy filter below, deliberately: a run whose only deed was
    # opening an executor deploys nothing, and it is precisely the run that
    # needs its tag known.
    #
    # The known boundary of this branch: a run with no ledger has no namespace
    # written down, so a *bound specialist* whose only deed was opening an
    # executor is indexed under `condor.chat` and its tag spelled with it, while
    # the tag the chat was actually handed says `brigado.chat` (the conversation
    # meta knows the binding; this directory does not). That costs a match in
    # the fleet view and nothing else: a tag carries the conversation id, so the
    # misspelled one matches no executor at all rather than someone else's. A
    # miss, never a lie — and the conversation's own ledger route reads the meta
    # and is exact regardless.
    _note_tag(tags, strategy, run_key, run_id)
    for row in rows:
        if row.verb == DEPLOY_VERB and row.ok and row.subject:
            _claim(bots, row.subject, OwnerRef(run_key, run_id, row.at))


def _build() -> DeedIndex:
    bots: dict[str, OwnerRef] = {}
    tags: dict[str, list[str]] = {}
    for directory, strategy, run_id in _pseudo_runs():
        try:
            _index_pseudo_run(directory, strategy, run_id, bots, tags)
        except Exception:  # noqa: BLE001 - one unreadable run is not the fleet
            log.debug("deed_index: unreadable run %s", directory, exc_info=True)
    for directory, run_key, run_id in _loop_runs():
        try:
            for bot in read_owned(directory):
                _claim(bots, bot.base, OwnerRef(run_key, run_id, bot.since))
        except Exception:  # noqa: BLE001
            log.debug("deed_index: unreadable session %s", directory, exc_info=True)
    return DeedIndex(
        bots=bots,
        tags={key: sorted(values) for key, values in tags.items()},
        since=coverage_since(),
    )


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

"""Session-ownership PnL attribution engine ([[FEAT-018]], [[ARCH-191]]).

Single source of truth for how a bot's realized PnL / volume / trades / fees are
attributed to the sessions that operated it. The vocabulary lives here in one
place:

- **session ownership resolution** — which bases a session owned and since when,
  from the ledger ``owned_bots.json`` writes (:mod:`condor.agents.ownership`)
  with a legacy shim for sessions predating it;
- **owner-window tiling** — the per-base ``[since_i, since_{i+1})`` windows that
  reproduce a bot's whole cumulative with no gap and no double count;
- **slice-and-merge** — folding a sliced history window into a performance
  object, including the shared fee rules (``fees_known`` heuristic and the
  live ``cum_fees_quote`` fallback, which also owns the flag), which exist
  exactly once, here;
- **the current-owner rule** — live unrealized PnL and the open book belong to
  whoever operates the bot now.

Both consumers go through this module: the web strategy rollup
(``condor.web.routes.agents``) via :func:`apply_bot_mode_pnl` /
:func:`current_owner_bases` / :func:`session_ownership`, and the agent's own
view (``condor.agents.performance``) via :func:`fold_sliced_window` /
:func:`apply_fee_fallback`. Both slice over the same unit, an
:class:`OwnershipWindow` per base ([[ARCH-662]]): the rollup tiles them with
:func:`tile_owner_windows`, and every single-session caller builds them with
:func:`ownership_windows` / :func:`session_windows` and hands them to
``fetch_agent_performance`` unflattened. That is what makes the invariant
structural instead of hand-maintained: the dashboard and the tick loop cannot
disagree, because they no longer have separate copies of the rules to drift
apart.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Mapping, NamedTuple

from pydantic import BaseModel

from condor.agents.ownership import OwnedBot, read_owned
from condor.agents.sessions_index import find_session_dir, session_started_at

log = logging.getLogger(__name__)


# ── Shared merge rules ──
# ``perf`` is duck-typed over the attribute vocabulary the two consumers share:
# the agents-side ``AgentPerformance`` dataclass and the web's
# ``AgentPerformanceModel`` both carry realized_pnl / unrealized_pnl / total_pnl /
# volume / fees / fees_known / trade_count / closed_count.


def fold_sliced_window(perf: Any, window: tuple[float, float, float, float]) -> None:
    """Fold one sliced ``(realized, volume, trades, fees)`` window into ``perf``.

    Sliced closes are round-trip closes, so they are this window's trades AND
    its closed positions — the same two counters bumped on both surfaces, so the
    session detail and the rollup report one number.
    """
    realized, volume, trades, fees = window
    closes = int(round(trades))
    perf.realized_pnl += realized
    perf.volume += volume
    perf.fees += fees
    # Traded but charged nothing: the fee column is missing, not the fees.
    if volume > 0 and fees == 0.0:
        perf.fees_known = False
    perf.trade_count += closes
    perf.closed_count += closes
    perf.total_pnl = perf.realized_pnl + perf.unrealized_pnl


def sliced_or_live_fees(sliced_fees: float, bot: dict[str, Any]) -> float:
    """Fees from the sliced history, else the live snapshot's cumulative figure.

    A backend that reports no cumulative fee column slices to zero; the live
    open-position figure is then the only one there is — attributed rather than
    silently dropped.
    """
    if sliced_fees:
        return sliced_fees
    return float(bot.get("cum_fees_quote", 0) or 0)


def apply_fee_fallback(perf: Any, sliced_fees: float, bot: dict[str, Any]) -> None:
    """Top ``perf.fees`` up to the live figure, and record that it is a floor.

    The fallback figure is open-position fees only (``_aggregate_by_bot`` derives
    it from ``positions_summary``), so a window it covers has no fee column at
    all — the number is a floor, exactly what ``fees_known=False`` means. Applied
    *after* :func:`fold_sliced_window` on both surfaces, so the heuristic always
    sees the raw sliced column and the flag cannot diverge on identical data.
    """
    live = sliced_or_live_fees(sliced_fees, bot)
    if live == sliced_fees:
        return
    perf.fees += live - sliced_fees
    perf.fees_known = False


# ── Session ownership resolution ──


def session_bot_base(strategy_dir: Path, default_config: dict | None, num: int) -> str:
    """Bot base name a session operates: per-session config, else strategy default.

    A non-empty per-session ``bot_name`` wins (so runtime-named bots that record
    their deployed name resolve), but an empty/absent one falls back to the
    strategy default — early sessions predating the config's ``bot_name`` saved it
    as ``''`` and must still map to the shared bot they operated. Empty string when
    neither is set (direct-executor strategies). Shared by the per-session PnL
    distribution and the operator's live-executor view so both resolve identically.
    """
    from condor.agents.config import load_full_config

    default_base = (default_config or {}).get("bot_name", "") or ""
    sd = find_session_dir(strategy_dir, num)
    if not sd:
        return default_base
    return load_full_config(sd, default_config).get("bot_name", "") or default_base


def session_start_epoch(strategy_dir: Path, num: int) -> float:
    """Session start time per :func:`~condor.agents.sessions_index.session_started_at`, or 0.0."""
    sd = find_session_dir(strategy_dir, num)
    if not sd:
        return 0.0
    start = session_started_at(sd)
    return 0.0 if start is None else start


def session_ownership(
    strategy_dir: Path, default_config: dict | None, num: int
) -> list[OwnedBot]:
    """Bases a session owned and the instant it took each over, oldest first.

    Two sources, in order:

    1. ``{session_dir}/owned_bots.json`` — the ledger [[FEAT-017]] writes, which
       knows both the bases (a session may operate several) and the exact takeover
       instant, whether the bot was deployed here or adopted after a restart.
    2. the legacy shim — a session predating the ledger resolves its single
       ``bot_name`` as one owned bot ``since`` the session started, reproducing the
       session-start tiling attribution used before the ledger existed.

    Empty for direct-executor strategies, whose per-session executor attribution
    already stands and must not be touched.
    """
    owned = read_owned(find_session_dir(strategy_dir, num))
    if owned:
        return owned
    base = session_bot_base(strategy_dir, default_config, num)
    if not base:
        return []
    start = session_start_epoch(strategy_dir, num)
    return [OwnedBot(base=base, origin="legacy", since=start, last_seen=start)]


# ── Owner-window tiling ──


class OwnershipWindow(NamedTuple):
    """The span one session held one base: ``[since, end)``.

    ``end`` is ``0.0`` while the session still holds the base — the window runs to
    now, and the bot's live open book (unrealized PnL, open rows) belongs to it.
    A closed window (``end > 0``) ended at a release or at the next owner's
    takeover: it keeps the realized slice and never the live book. ``since`` of
    ``0.0`` means the takeover instant is unknown, which the agent-side fetch
    reads as "no slice" and falls back to the lifetime aggregate.
    """

    since: float
    end: float = 0.0

    @property
    def is_open(self) -> bool:
        """Still held — the window runs to now and carries the live open book."""
        return self.end <= 0

    def bounds(self, now: float) -> tuple[float, float]:
        """``(start, stop)`` to slice the history over, an open end read as ``now``."""
        return self.since, (self.end if self.end > 0 else now)


def ownership_windows(
    owned: list[OwnedBot], handovers: Mapping[str, float] | None = None
) -> dict[str, OwnershipWindow]:
    """``{base: OwnershipWindow}`` for one session's ledger, one window per base.

    A released base (``until``) closes there. ``handovers`` maps a base to the
    instant a LATER owner took it over, which one session's own ledger cannot
    see: the window is clipped there too, so it stops where the next owner's
    starts instead of running on to now. The session-detail route gets those
    instants from :func:`session_windows`; a live session is its bots' current
    owner and passes none.
    """
    out: dict[str, OwnershipWindow] = {}
    for ob in owned:
        ends = [t for t in (ob.until, (handovers or {}).get(ob.base, 0.0)) if t > 0]
        out[ob.base] = OwnershipWindow(ob.since, min(ends) if ends else 0.0)
    return out


def window_span(windows: Mapping[str, OwnershipWindow]) -> tuple[float, float]:
    """``(since, until)`` covering every window, for a scalar-span consumer.

    ``since`` is the earliest known takeover; ``until`` is the latest close, or
    ``0.0`` (to now) while any window is still open. ``(0.0, 0.0)`` when no
    window has a known start.
    """
    since = min((w.since for w in windows.values() if w.since > 0), default=0.0)
    if not windows or any(w.is_open for w in windows.values()):
        return since, 0.0
    return since, max(w.end for w in windows.values())


def owner_windows(
    session_nums: list[int], strategy_dir: Path, default_config: dict | None
) -> dict[str, list[tuple[float, int, float]]]:
    """``{base: [(since, session_num, until), …]}`` — owners, oldest takeover first.

    Keyed per base rather than globally per session number: two bases handed over
    at different moments never share a timeline. Ties on ``since`` go to the
    higher session number. :func:`tile_owner_windows` turns one base's list into
    its windows.
    """
    owners: dict[str, list[tuple[float, int, float]]] = {}
    for n in session_nums:
        for ob in session_ownership(strategy_dir, default_config, n):
            owners.setdefault(ob.base, []).append((ob.since, n, ob.until))
    for lst in owners.values():
        lst.sort(key=lambda t: (t[0], t[1]))
    return owners


def tile_owner_windows(
    owners: list[tuple[float, int, float]],
) -> list[tuple[int, OwnershipWindow]]:
    """One base's owners as ``[(session_num, window), …]``, tiling its timeline.

    The windows tile ``[since_i, since_{i+1})`` and the last one runs to now, so
    slicing over them reproduces the bot's whole cumulative with no gap and no
    double count. A released window stops at its ``until`` rather than at the
    next takeover, which is the one case where the tiling deliberately leaves a
    gap: PnL a bot earned with no session operating it belongs to no session.
    Only the last owner's window can be open, and only if it never released.
    """
    out: list[tuple[int, OwnershipWindow]] = []
    for i, (since, num, until) in enumerate(owners):
        nxt = owners[i + 1][0] if i + 1 < len(owners) else 0.0
        ends = [t for t in (nxt, until) if t > 0]
        out.append((num, OwnershipWindow(since, min(ends) if ends else 0.0)))
    return out


def session_windows(
    strategy_dir: Path,
    default_config: dict | None,
    session_nums: list[int],
    num: int,
) -> dict[str, OwnershipWindow]:
    """Session ``num``'s windows, cut by the same tiling the rollup slices with.

    What :func:`ownership_windows` gives for one ledger, plus the next owner's
    takeover — so a session detail reports exactly its row in the strategy
    rollup, including for a base a later session adopted.
    """
    nums = sorted(set(session_nums) | {num})
    return {
        base: window
        for base, owners in owner_windows(nums, strategy_dir, default_config).items()
        for owner, window in tile_owner_windows(owners)
        if owner == num
    }


def current_owner_bases(
    strategy_dir: Path,
    default_config: dict | None,
    session_nums: list[int],
    num: int,
) -> list[str]:
    """Bases ``num`` is the CURRENT owner of — its window on them is still open.

    A bot's live open positions belong to whoever operates it now, so this is the
    gate for merging them into one session's view. It reads the same windows
    :func:`apply_bot_mode_pnl` slices with — only the last takeover's window can
    be open — so the rollup and the per-session detail can never disagree about
    who holds the open book. A session that released the bot is not its current
    owner, so an ended session shows no live open book.
    """
    windows = session_windows(strategy_dir, default_config, session_nums, num)
    return sorted(base for base, w in windows.items() if w.is_open)


# ── The attribution engine ──


async def apply_bot_mode_pnl(
    real_sessions: list, strategy_dir: Path, default_config: dict | None, client: Any
) -> bool:
    """Distribute each owned bot's PnL across the sessions that operated it.

    One rule covers deploy and handover: every owned bot is attributed by slicing
    its history over its :func:`tile_owner_windows` window
    ``[since, min(next_owner.since, until) or now)``, where ``since`` is the
    takeover instant the ownership ledger recorded. A bot the session *deployed*
    has no history before its ``since``, so the general rule already hands it the
    whole instance — the exact case falls out instead of needing its own branch.

    Live unrealized PnL, fees and open positions go to each base's LAST owner by
    ``since`` — a lookup in the ledger where it used to be a ``max(session_num)``
    guess, so a new session that never adopted the bot no longer inherits its
    open book.

    Works uniformly for single- and multi-controller bots (history sums controllers
    per instance) and for a base re-launched under several instances. Strategies
    whose sessions own no bot (direct-executor agents) are left untouched.

    Returns whether the live snapshot fetch failed, i.e. whether the rows it just
    wrote are missing their unrealized PnL and open positions. Mutation and a
    return value together, because the caller's question is not *what did this
    compute* but *is what it computed worth caching* ([[CORR-700]]); a strategy
    that owns no bot never asks the backend at all and so is never degraded.
    """
    from condor.fetchers.bot_performance import (
        bot_executor_rows,
        fetch_base_histories,
        fetch_bot_universe_checked,
        fetch_live_instance_names,
        resolve_bots,
        slice_history,
    )

    if not client or not real_sessions:
        return False
    by_num = {s.session_num: s for s in real_sessions}
    owners = owner_windows(list(by_num), strategy_dir, default_config)
    bases = sorted(owners)
    if not bases:
        return False  # direct-executor strategy — nothing to attribute

    # Archived instances carry the realized PnL of every bot a session stopped —
    # the normal end state of a finished session, and invisible in the live
    # snapshot. Same universe and failure policy the live agent's own view uses,
    # so the dashboard and the tick loop cannot disagree about what a session
    # earned — not even when the live snapshot is down.
    all_perf, archived, degraded = await fetch_bot_universe_checked(client)

    now = time.time()
    # The oldest takeover across every base sets how far back the histories must
    # reach; sampling resolution is chosen from it so no owner's window falls off
    # the end of the retained rows.
    earliest = min(
        (since for lst in owners.values() for since, _, _ in lst if since > 0),
        default=0.0,
    )
    histories_by_base = await fetch_base_histories(
        client, all_perf, bases, earliest, now, extra_names=archived
    )

    # The snapshot keeps a stopped instance's final unrealized and positions, so
    # only the instances actually running may carry the open book.
    live = resolve_bots(all_perf, bases, await fetch_live_instance_names(client))
    for base in bases:
        tiled = tile_owner_windows(owners[base])
        insts = histories_by_base.get(base, [])

        # Realized / volume / trades / fees: one window per owner, tiling the
        # timeline. A released window (the session stopped and left the bot
        # running) ends at its release instant, so PnL earned while no session
        # was operating the bot is attributed to nobody instead of accruing to
        # whoever happened to hold last.
        sliced_fees = 0.0
        for num, owned_window in tiled:
            start, stop = owned_window.bounds(now)
            if stop <= start:
                continue
            window = slice_history(insts, start, stop)
            fold_sliced_window(by_num[num], window)
            sliced_fees += window[3]

        # Live unrealized + open positions → the base's current owner, i.e. the
        # one whose window is still open: an ended session holds no open book.
        bot = live.get(base)
        if not bot:
            continue
        num, last_window = tiled[-1]
        if not last_window.is_open:
            continue
        operator = by_num[num]
        b_rows = bot_executor_rows(bot)
        operator.unrealized_pnl += float(bot.get("unrealized_pnl_quote", 0) or 0)
        # Top the operator up to the fallback figure: when the whole sliced fee
        # column is zero, the live open-position fees are the only ones there are
        # — a floor, so the shared rule stamps ``fees_known=False`` with it.
        apply_fee_fallback(operator, sliced_fees, bot)
        operator.open_count += sum(1 for r in b_rows if r["status"] == "RUNNING")
        operator.executors = list(operator.executors) + b_rows
        operator.total_pnl = operator.realized_pnl + operator.unrealized_pnl

    return degraded


# ── The run ledger ──
# What a run put into the world, as rows. The same genre as everything above —
# a pure join over ``OwnedBot`` and a performance object — and the reason it
# lives here rather than in the route module that renders it: two routes serve
# it (a session's executors and a conversation's deployments) and its tests
# unit-test a pure function, none of which should have to drag a FastAPI
# router in ([[ARCH-588]]).


class DeploymentRow(BaseModel):
    """One thing a run put into the world (FEAT-100).

    A bot it deployed, a controller one of those bots ran, or a standalone
    executor it created — the three kinds together are the answer to *what did
    this run actually do out there*, which until now could only be assembled by
    leaving the agent for the fleet browser and reading a strategy's whole
    lifetime instead of the one run.
    """

    #: ``bot`` | ``controller`` | ``executor``.
    kind: str
    #: The base name, the controller id, or ``"grid SOL-USDC"``.
    label: str
    #: Origin for a bot, connector·pair for a controller, connector otherwise.
    detail: str = ""
    #: The tick whose creating call most likely produced this — ``None`` when the
    #: join found nothing, which is every run predating the actions log. Never
    #: guessed; see :func:`condor.agents.actions.tick_for`.
    created_tick: int | None = None
    started_at: float = 0.0
    #: When this run stopped owning it, or ``None`` while it still does.
    ended_at: float | None = None
    #: Whether this run still holds it. Read off ownership, never off ``status``
    #: — an archived instance's performance snapshot still says "running".
    live: bool = False
    pnl: float = 0.0
    volume: float = 0.0
    #: The fleet address this row links to (``bot:``/``ctrl:``/``exec:``).
    scope: str = ""
    #: ``False`` when ``pnl``/``volume`` are not USD: a controller whose quote
    #: had no USD rate keeps its face value and this flag
    #: (:func:`condor.fetchers.bot_performance.restate_universe_in_usd`) — the
    #: ``converted`` convention of :mod:`condor.quote_conversion` (CORR-707). A
    #: bot row is always USD: its sums leave such a controller out, exactly as
    #: the bot totals did.
    usd_converted: bool = True


def _instance_for_base(base: str, live: list[str], instances: list[str]) -> str:
    """The deploy a bot row should link to: the live one, else the newest."""
    from condor.agents.ownership import strip_deploy_suffix

    for name in live:
        if strip_deploy_suffix(name) == base:
            return name
    mine = [n for n in instances if strip_deploy_suffix(n) == base]
    return mine[-1] if mine else base


def build_deployments(
    owned: list[Any],
    bot_bases: list[str],
    perf: Any,
    actions: list[Any],
    agent_id: str,
) -> list[DeploymentRow]:
    """Everything one run put into the world, from values it already has (FEAT-100).

    Pure — every input is already on ``get_session_executors``'s stack, which is
    the whole reason the ledger is a field on that response rather than a second
    endpoint that would have to redo ``session_ownership`` *and* re-fetch the
    session's performance to fill the same PnL column.

    Three joins, none of them clever:

    - a **bot** is an :class:`~condor.agents.ownership.OwnedBot`, and it is live
      iff this session is still the base's current owner (``bot_bases``) — not
      iff its snapshot says "running", which an archived instance also does;
    - a **controller** belongs to the bot whose deploy it ran under, so it
      inherits that bot's window and its tick;
    - an **executor** is this run's own iff it is tagged with the session's
      ``agent_id``, the same join the fleet browser performs.

    The tick column is the one heuristic, and it is allowed to say nothing: the
    actions log records arguments and never results, so there is no id to join
    on and a record is credited to the nearest preceding create of its kind. Runs
    written before that log exists get ``None`` everywhere, and the ledger still
    renders — the bots and the executors are all there.
    """
    from condor.agents.actions import tick_for
    from condor.agents.ownership import strip_deploy_suffix

    live_instances = list(getattr(perf, "bot_names", None) or [])
    all_instances = list(getattr(perf, "bot_instances", None) or [])
    controllers = list(getattr(perf, "controllers", None) or [])
    executors = list(getattr(perf, "executors", None) or [])
    base_windows = getattr(perf, "base_windows", None) or {}
    owned_by_base = {b.base: b for b in owned}

    rows: list[DeploymentRow] = []
    for bot in sorted(owned, key=lambda b: (b.since, b.base)):
        ran = [
            c
            for c in controllers
            if strip_deploy_suffix(str(c.get("bot_name") or "")) == bot.base
        ]
        # A controller left at face value is not USD, and the bot totals it
        # came from already excluded it; adding it here would put native money
        # into a USD row (CORR-707).
        mine = [c for c in ran if c.get("usd_converted", True) is not False]
        live = bot.base in bot_bases
        window = base_windows.get(bot.base)
        if window is not None:
            # The session's slice of the bot's history — the same figure the KPI
            # strip folded — plus the open book only while this session still
            # holds the base (the current-owner rule). The controllers' own
            # realized PnL is lifetime and would credit what was inherited.
            realized, volume, _trades, _fees = window
            unrealized = sum(float(c.get("unrealized_pnl_quote") or 0.0) for c in mine)
            pnl = realized + (unrealized if live else 0.0)
        else:
            # No window could be cut: the lifetime aggregate is all there is.
            pnl = sum(_controller_pnl(c) for c in mine)
            volume = sum(float(c.get("volume_traded") or 0.0) for c in mine)
        rows.append(
            DeploymentRow(
                kind="bot",
                label=bot.base,
                detail=bot.origin,
                created_tick=tick_for(actions, "bot", bot.since),
                started_at=bot.since,
                ended_at=bot.until or None,
                live=live,
                pnl=pnl,
                volume=volume,
                scope=f"bot:{_instance_for_base(bot.base, live_instances, all_instances)}",
            )
        )

    live_set = set(live_instances)
    for c in controllers:
        instance = str(c.get("bot_name") or "")
        base = strip_deploy_suffix(instance)
        parent = owned_by_base.get(base)
        cid = str(c.get("controller_id") or "")
        detail = " · ".join(
            p
            for p in (str(c.get("connector") or ""), str(c.get("trading_pair") or ""))
            if p
        )
        rows.append(
            DeploymentRow(
                kind="controller",
                label=cid or str(c.get("controller_name") or "controller"),
                detail=detail,
                # A controller has no creating call of its own: it came into the
                # world with the deploy that carried it.
                created_tick=(
                    tick_for(actions, "bot", parent.since) if parent else None
                ),
                started_at=parent.since if parent else 0.0,
                ended_at=(parent.until or None) if parent else None,
                live=instance in live_set,
                # Lifetime, not sliced: the controller history is per instance,
                # never per controller, so only the bot row above can carry the
                # session's window. The two levels are on different bases.
                pnl=_controller_pnl(c),
                volume=float(c.get("volume_traded") or 0.0),
                scope=f"ctrl:{instance}:{cid}" if instance and cid else "",
                usd_converted=c.get("usd_converted", True) is not False,
            )
        )

    for ex in executors:
        if str(ex.get("controller_id") or "") != agent_id:
            continue
        started = float(ex.get("timestamp") or 0.0)
        closed = float(ex.get("close_timestamp") or 0.0)
        kind_name = str(ex.get("type") or "").replace("_executor", "")
        pair = str(ex.get("pair") or "")
        rows.append(
            DeploymentRow(
                kind="executor",
                label=" ".join(p for p in (kind_name, pair) if p) or str(ex.get("id")),
                detail=str(ex.get("connector") or ""),
                created_tick=tick_for(actions, "executor", started),
                started_at=started,
                ended_at=closed or None,
                live=closed <= 0,
                pnl=float(ex.get("pnl") or 0.0),
                volume=float(ex.get("volume") or 0.0),
                scope=f"exec:{ex.get('id')}" if ex.get("id") else "",
            )
        )
    return rows


def _controller_pnl(c: dict[str, Any]) -> float:
    """What a controller has made, on the same basis as the KPI strip's total."""
    return float(c.get("realized_pnl_quote") or 0.0) + float(
        c.get("unrealized_pnl_quote") or 0.0
    )

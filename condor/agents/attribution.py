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
  live ``cum_fees_quote`` fallback), which exist exactly once, here;
- **the current-owner rule** — live unrealized PnL and the open book belong to
  whoever operates the bot now.

Both consumers go through this module: the web strategy rollup
(``condor.web.routes.agents``) via :func:`apply_bot_mode_pnl` /
:func:`current_owner_bases` / :func:`session_ownership`, and the agent's own
view (``condor.agents.performance``) via :func:`fold_sliced_window` /
:func:`sliced_or_live_fees`. That is what makes the invariant structural
instead of hand-maintained: the dashboard and the tick loop cannot disagree,
because they no longer have separate copies of the rules to drift apart.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from condor.agents.ownership import OwnedBot, read_owned
from condor.agents.sessions_index import find_session_dir

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
    """Session start time: config.yml is written once at start, so its mtime is stable."""
    sd = find_session_dir(strategy_dir, num)
    if not sd:
        return 0.0
    cfg = sd / "config.yml"
    target = cfg if cfg.exists() else sd
    try:
        return os.path.getmtime(target)
    except OSError:
        return 0.0


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


def owner_windows(
    real_sessions: list, strategy_dir: Path, default_config: dict | None
) -> dict[str, list[tuple[float, Any, float]]]:
    """``{base: [(since, session, until), …]}`` — owners, oldest takeover first.

    The windows a base's owners occupy tile ``[since_i, since_{i+1})`` and the last
    one runs to now, so slicing over them reproduces the bot's whole cumulative with
    no gap and no double count. Keyed per base rather than globally per session
    number: two bases handed over at different moments never share a timeline.

    ``until`` is the instant the session released the bot, or ``0.0`` while it
    still holds it. A released last window stops there rather than running to now,
    which is the one case where the tiling deliberately leaves a gap: PnL a bot
    earned with no session operating it belongs to no session.
    """
    owners: dict[str, list[tuple[float, Any, float]]] = {}
    for s in real_sessions:
        for ob in session_ownership(strategy_dir, default_config, s.session_num):
            owners.setdefault(ob.base, []).append((ob.since, s, ob.until))
    for lst in owners.values():
        lst.sort(key=lambda t: (t[0], t[1].session_num))
    return owners


def current_owner_bases(
    strategy_dir: Path,
    default_config: dict | None,
    session_nums: list[int],
    num: int,
) -> list[str]:
    """Bases ``num`` is the CURRENT owner of — the last takeover by ``since``.

    A bot's live open positions belong to whoever operates it now, so this is the
    gate for merging them into one session's view. Same rule
    :func:`apply_bot_mode_pnl` applies to live unrealized PnL, kept here as one
    lookup over the same windows so the rollup and the per-session detail can
    never disagree about who holds the open book. A session that released the bot
    is not its current owner, so an ended session shows no live open book.
    """
    last: dict[str, tuple[float, int, float]] = {}
    for n in session_nums:
        for ob in session_ownership(strategy_dir, default_config, n):
            if last.get(ob.base, (float("-inf"), -1, 0.0))[:2] <= (ob.since, n):
                last[ob.base] = (ob.since, n, ob.until)
    return sorted(
        base for base, (_, owner, until) in last.items() if owner == num and until <= 0
    )


# ── The attribution engine ──


async def apply_bot_mode_pnl(
    real_sessions: list, strategy_dir: Path, default_config: dict | None, client: Any
) -> None:
    """Distribute each owned bot's PnL across the sessions that operated it.

    One rule covers deploy and handover: every owned bot is attributed by slicing
    its history over ``[since, next_owner.since or now)``, where ``since`` is the
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
    """
    from condor.fetchers.bot_performance import (
        bot_executor_rows,
        fetch_all_bot_performance,
        fetch_archived_instances,
        fetch_base_histories,
        resolve_bots,
        slice_history,
    )

    if not client or not real_sessions:
        return
    owners = owner_windows(real_sessions, strategy_dir, default_config)
    bases = sorted(owners)
    if not bases:
        return  # direct-executor strategy — nothing to attribute

    try:
        all_perf = await fetch_all_bot_performance(client)
    except Exception as e:
        log.warning("bot perf fetch for %s failed: %s", strategy_dir.name, e)
        return

    now = time.time()
    # The oldest takeover across every base sets how far back the histories must
    # reach; sampling resolution is chosen from it so no owner's window falls off
    # the end of the retained rows.
    earliest = min(
        (since for lst in owners.values() for since, _, _ in lst if since > 0),
        default=0.0,
    )
    # Archived instances carry the realized PnL of every bot a session stopped —
    # the normal end state of a finished session, and invisible in the live
    # snapshot. Same universe the live agent's own view uses, so the dashboard and
    # the tick loop cannot disagree about what a session earned.
    archived = await fetch_archived_instances(client)
    histories_by_base = await fetch_base_histories(
        client, all_perf, bases, earliest, now, extra_names=archived
    )

    live = resolve_bots(all_perf, bases)
    for base in bases:
        window_owners = owners[base]
        insts = histories_by_base.get(base, [])

        # Realized / volume / trades / fees: one window per owner, tiling the
        # timeline. A released window (the session stopped and left the bot
        # running) ends at its release instant, so PnL earned while no session
        # was operating the bot is attributed to nobody instead of accruing to
        # whoever happened to hold last.
        sliced_fees = 0.0
        for i, (since, s, until) in enumerate(window_owners):
            end = window_owners[i + 1][0] if i + 1 < len(window_owners) else now
            if until > 0:
                end = min(end, until)
            if end <= since:
                continue
            window = slice_history(insts, since, end)
            fold_sliced_window(s, window)
            sliced_fees += window[3]

        # Live unrealized + open positions → the base's current owner, unless it
        # has released the bot: an ended session holds no open book.
        bot = live.get(base)
        if not bot:
            continue
        last_since, operator, last_until = window_owners[-1]
        if last_until > 0:
            continue
        b_rows = bot_executor_rows(bot)
        operator.unrealized_pnl += float(bot.get("unrealized_pnl_quote", 0) or 0)
        # Top the operator up to the fallback figure: when the whole sliced fee
        # column is zero, the live open-position fees are the only ones there are.
        operator.fees += sliced_or_live_fees(sliced_fees, bot) - sliced_fees
        operator.open_count += sum(1 for r in b_rows if r["status"] == "RUNNING")
        operator.executors = list(operator.executors) + b_rows
        operator.total_pnl = operator.realized_pnl + operator.unrealized_pnl

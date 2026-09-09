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
:func:`apply_fee_fallback`. That is what makes the invariant structural
instead of hand-maintained: the dashboard and the tick loop cannot disagree,
because they no longer have separate copies of the rules to drift apart.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

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
        # column is zero, the live open-position fees are the only ones there are
        # — a floor, so the shared rule stamps ``fees_known=False`` with it.
        apply_fee_fallback(operator, sliced_fees, bot)
        operator.open_count += sum(1 for r in b_rows if r["status"] == "RUNNING")
        operator.executors = list(operator.executors) + b_rows
        operator.total_pnl = operator.realized_pnl + operator.unrealized_pnl


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
    owned_by_base = {b.base: b for b in owned}

    rows: list[DeploymentRow] = []
    for bot in sorted(owned, key=lambda b: (b.since, b.base)):
        mine = [
            c
            for c in controllers
            if strip_deploy_suffix(str(c.get("bot_name") or "")) == bot.base
        ]
        rows.append(
            DeploymentRow(
                kind="bot",
                label=bot.base,
                detail=bot.origin,
                created_tick=tick_for(actions, "bot", bot.since),
                started_at=bot.since,
                ended_at=bot.until or None,
                live=bot.base in bot_bases,
                pnl=sum(_controller_pnl(c) for c in mine),
                volume=sum(float(c.get("volume_traded") or 0.0) for c in mine),
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
                pnl=_controller_pnl(c),
                volume=float(c.get("volume_traded") or 0.0),
                scope=f"ctrl:{instance}:{cid}" if instance and cid else "",
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

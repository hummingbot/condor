"""Shared aggregator for trading agent performance.

Single source of truth for PnL / volume / trade stats for a given ``agent_id``
(``controller_id`` tag on executors). Used both by the live ``ExecutorsProvider``
and the web API so they always agree.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING, Any, Mapping

from condor.fetchers._pagination import walk_pages
from condor.fetchers.executors import EXECUTORS_PAGE_SIZE, extract_executors_list

if TYPE_CHECKING:
    from condor.agents.attribution import OwnershipWindow

log = logging.getLogger(__name__)


@dataclass
class AgentPerformance:
    agent_id: str
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    volume: float = 0.0
    fees: float = 0.0
    trade_count: int = 0
    # Executor-derived, and ``None`` when there is nothing to derive it from —
    # which is not the same as 0%. Only closed executor rows carry a per-trade
    # outcome; a bot-mode agent's trades come from the controller snapshot, which
    # says how many positions closed but not how any of them ended.
    win_rate: float | None = None
    open_count: int = 0
    closed_count: int = 0
    executors: list[dict[str, Any]] = field(default_factory=list)
    # Controller-mode attribution: each bot the agent operates has its aggregate
    # PnL merged into the totals above, and its resolved instance name surfaced
    # here for transparency. A session can own several bots ([[FEAT-018]]), so this
    # is a list; the merge is plain addition over disjoint sets and needs no
    # de-duplication as long as the bases are disjoint (see ``resolve_bots``).
    bot_names: list[str] = field(default_factory=list)
    # Every instance that ever ran under an owned base, oldest deploy first —
    # including the ones this session already stopped. ``bot_names`` names only the
    # instances alive now, so a session that redeployed three times reported the
    # last one and hid the two it had operated and wound down. Both are needed:
    # the live names say what is running, this says what the session ran.
    bot_instances: list[str] = field(default_factory=list)
    controllers: list[dict[str, Any]] = field(default_factory=list)
    # Raw ``CloseType.X -> n`` breakdown across every operated controller.
    # ``trade_count`` counts only round-trip closes, which reads a directional
    # controller's risk stop (EARLY_STOP) as churn and reports 0 trades on a
    # session that closed three positions. Nothing in the payload tells a market
    # maker's re-quote from a directional stop, so the breakdown ships alongside
    # the count instead of a heuristic guessing between them.
    close_type_counts: dict[str, int] = field(default_factory=dict)
    # False when ``fees`` is a floor rather than a figure: a backend that reports
    # no cumulative fee column leaves bot-mode fees derivable only from open
    # positions, so a flat bot sums to 0.0 — which is "unknown", not "free".
    fees_known: bool = True
    # Owned bases that resolved to no instance at all — neither running nor
    # archived. Their contribution is unknown, not zero, and the two must not
    # render alike: a session whose bot has vanished reporting "$0.00" reads as
    # "traded flat" when it means "cannot see the bot".
    unresolved_bases: list[str] = field(default_factory=list)

    @property
    def bot_name(self) -> str:
        """The first operated bot — wire compat for single-bot consumers."""
        return self.bot_names[0] if self.bot_names else ""

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "bot_name": self.bot_name}


def _executor_row(ex: dict) -> dict[str, Any]:
    """The agents-side display row for one raw executor.

    The transform itself is shared — :func:`condor.fetchers.executors.build_executor_row`
    — so an executor cannot mean two things depending on whether the executors
    tab or the agent session view built its row. Only the agents-side reading of
    it stays here: ``status`` is uppercased for this wire, and a position
    executor that resolved no entry price is worth a log line.
    """
    from condor.fetchers.executors import build_executor_row

    row = build_executor_row(ex)
    row["status"] = row["status"].upper()

    # A position executor with no entry_price is genuinely suspicious; everything
    # else legitimately lacks one, so don't warn for them.
    if row["entry_price"] == 0.0 and "position" in str(row["type"]).lower():
        log.warning(
            "entry_price fell back to 0.0 for position executor %s — PnL may be wrong",
            row["id"] or "?",
        )
    return row


async def fetch_agent_performance(
    client: Any,
    agent_id: str,
    bot_names: list[str] | None = None,
    windows: Mapping[str, OwnershipWindow] | None = None,
) -> AgentPerformance:
    """Fetch authoritative performance for a single ``agent_id``.

    When ``bot_names`` or ``windows`` name bases, the agent is in controller mode:
    each base's PnL is merged into the returned totals (see
    :func:`fetch_agent_performance_batch`).

    ``windows`` maps a base to the :class:`~condor.agents.attribution.OwnershipWindow`
    this session held it over — build it with
    :func:`~condor.agents.attribution.ownership_windows` or
    :func:`~condor.agents.attribution.session_windows`, never by flattening the
    ledger to one instant. Each base's realized/volume/trades/fees are then sliced
    to its own window exactly as the web rollup slices them, so a session that
    adopted a long-running bot is not credited with the PnL it inherited, nor with
    what the bot earned after it let go. A base named only in ``bot_names`` has no
    known takeover and gets the lifetime aggregate, which is only right for a
    session that deployed the bot itself.
    """
    names = list(dict.fromkeys(b for b in [*(bot_names or []), *(windows or {})] if b))
    batch = await fetch_agent_performance_batch(
        client,
        [agent_id],
        {agent_id: names} if names else None,
        windows={agent_id: dict(windows)} if names and windows else None,
    )
    return batch.get(
        agent_id, AgentPerformance(agent_id=agent_id, bot_names=list(names))
    )


def _merge_bot_perf(
    perf: AgentPerformance,
    bot: dict[str, Any],
    window: tuple[float, float, float, float] | None = None,
) -> None:
    """Fold a bot's contribution into an executor-derived ``AgentPerformance``.

    The two sources are disjoint (bot controllers tag executors with their own
    config ids, never the ``agent_id``), so the merge is plain addition — no
    de-duplication. The bot's open positions are surfaced as executor-like rows so
    bot-mode agents show live positions in both the executors tab and the agent's
    own core-data view, which otherwise only see the (empty) ``agent_id`` table.

    Additive in the bot dimension too: folding several owned bots in turn
    accumulates rather than overwrites, so a session operating two bots reports
    their sum and both controller breakdowns.

    ``window`` is this session's sliced ``(realized, volume, trades, fees)`` from
    the controller history. When given it replaces the lifetime aggregate for
    those four; unrealized PnL and the open rows always come from the live
    snapshot, since the open book belongs to whoever operates the bot now.

    Trades come from the bot's real round-trip closes (``closed_trades``, or the
    sliced ``trades`` when a window is given) — never from ``rows``, which only
    describe the positions open at this instant. ``win_rate`` is left untouched:
    the bot snapshot reports how many positions closed but not how each one ended,
    so a bot-mode agent has no per-trade outcome to derive one from.
    """
    from condor.agents.attribution import apply_fee_fallback, fold_sliced_window
    from condor.fetchers.bot_performance import bot_executor_rows

    rows = bot_executor_rows(bot)
    open_rows = [r for r in rows if r["status"] == "RUNNING"]

    if window is None:
        # Lifetime aggregate, folded through the same shared rule as a window so
        # the fees_known heuristic exists exactly once (condor.agents.attribution).
        # The lifetime aggregate has no fee *column*: ``cum_fees_quote`` is what
        # ``_aggregate_by_bot`` sums off ``positions_summary``, i.e. the fees of the
        # positions open at this instant, with every closed position's fees missing.
        # So it is the fallback figure, not a window's fees — fold a zero fee slot
        # and let ``apply_fee_fallback`` add it and stamp ``fees_known=False``
        # ([[CORR-219]]). ``perf.fees`` lands on the same number either way; only
        # the certainty changes.
        fold_sliced_window(
            perf,
            (
                float(bot.get("realized_pnl_quote", 0) or 0),
                float(bot.get("volume_traded", 0) or 0),
                float(bot.get("closed_trades", 0) or 0),
                0.0,
            ),
        )
        apply_fee_fallback(perf, 0.0, bot)
    else:
        # Fold the RAW sliced window, then top up: the fees_known heuristic must
        # see the unsubstituted fee column, and the fallback stamps its own flag.
        # Same order as the web rollup, so the two surfaces cannot disagree.
        fold_sliced_window(perf, window)
        apply_fee_fallback(perf, window[3], bot)

    for ct, n in (bot.get("close_type_counts") or {}).items():
        perf.close_type_counts[str(ct)] = perf.close_type_counts.get(str(ct), 0) + int(
            n or 0
        )

    perf.unrealized_pnl += float(bot.get("unrealized_pnl_quote", 0) or 0)
    perf.total_pnl = perf.realized_pnl + perf.unrealized_pnl
    perf.controllers = perf.controllers + list(bot.get("controllers", []))
    perf.executors = perf.executors + rows
    perf.open_count += len(open_rows)


async def fetch_agent_pnl_series(
    client: Any,
    bot_names: list[str],
    since: float,
    until: float = 0.0,
) -> list[dict[str, Any]]:
    """This session's realized-PnL curve over its ownership window.

    Reconstructed from the bots' own controller-performance history rather than
    replayed from the per-tick journal snapshots. The snapshots are a *cache* of
    whatever the aggregator believed at each tick, so a session that ticked while
    the aggregator was blind to its bots holds a permanent record of flat zeros —
    the KPI can be corrected, a written-down snapshot cannot. Deriving the curve
    from the same history the KPI is sliced from means the two cannot disagree,
    and a fix reaches every past session without rewriting any journal.

    Realized only: the history carries no unrealized column, and a mark-to-market
    line would in any case be a point-in-time value rather than something that
    accrues. The final point therefore matches the KPI's *Realized*, not its
    *Total*, whenever a position is still open.

    Returns ``[{"timestamp": iso, "pnl": realized, "volume": volume}, …]``, empty
    when the session owns no bot or the history is unavailable.
    """
    import time as _time

    from condor.fetchers.bot_performance import (
        fetch_base_histories,
        fetch_bot_universe,
        slice_history_series,
    )

    bases = [b for b in (bot_names or []) if b]
    if not client or not bases or since <= 0:
        return []

    end = until if until > 0 else _time.time()
    all_bot_perf, archived = await fetch_bot_universe(client)
    try:
        histories = await fetch_base_histories(
            client, all_bot_perf, bases, since, end, extra_names=archived
        )
    except Exception as e:
        log.warning("pnl series: history fetch failed: %s", e)
        return []

    instances = [h for hs in histories.values() for h in hs if h]
    if not instances:
        return []

    # One point per instant any instance was sampled. Each is the whole session's
    # cumulative at that moment, so the curve is continuous across a redeploy
    # rather than restarting at zero with each new bot.
    # Single merge pass over all instances instead of a slice_history rescan
    # per stamp — same values, O(stamps + rows) instead of O(stamps × rows).
    stamps = sorted({t for h in instances for t, *_ in h if since <= t <= end})
    series: list[dict[str, Any]] = []
    for t, realized, volume, _trades, _fees in slice_history_series(
        instances, since, stamps
    ):
        series.append(
            {
                "timestamp": datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
                "pnl": realized,
                "volume": volume,
            }
        )
    return series


def _build_perf_from_rows(
    agent_id: str,
    rows: list[dict[str, Any]],
) -> AgentPerformance:
    # Compute everything directly from per-executor rows so realized/unrealized
    # stay consistent with what the UI renders per-row. The backend's
    # performance_report endpoint returns net_pnl_quote which already includes
    # open-position PnL; using it as "realized" and then adding unrealized on
    # top double-counts open positions.
    running = [r for r in rows if r["status"] == "RUNNING"]
    closed = [r for r in rows if r["status"] != "RUNNING"]

    unrealized = sum(r["pnl"] for r in running)
    realized_pnl = sum(r["pnl"] for r in closed)
    volume = sum(r["volume"] for r in rows)
    fees = sum(r["fees"] for r in rows)

    win_rate: float | None = None
    if closed:
        wins = sum(1 for r in closed if r["pnl"] > 0)
        win_rate = wins / len(closed)

    return AgentPerformance(
        agent_id=agent_id,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized,
        total_pnl=realized_pnl + unrealized,
        volume=volume,
        fees=fees,
        trade_count=len(rows),
        win_rate=win_rate,
        open_count=len(running),
        closed_count=len(closed),
        executors=rows,
    )


async def fetch_agent_performance_batch(
    client: Any,
    agent_ids: list[str],
    bot_names: dict[str, list[str]] | None = None,
    failed_ids: set[str] | None = None,
    windows: Mapping[str, Mapping[str, OwnershipWindow]] | None = None,
) -> dict[str, AgentPerformance]:
    """Batched multi-agent fetch via a single cursor-paginated executor search.

    ``bot_names`` maps ``agent_id -> the bases it owns`` for agents running in
    controller mode; each such agent's bot figures (one shared snapshot fetch for
    the whole batch) are merged into its executor-derived totals.

    ``windows`` maps ``agent_id -> {base: OwnershipWindow}``, the span that agent
    held each base. A base with a known takeover has its realized/volume/trades/
    fees sliced from the controller history over its OWN window instead of the
    bot's whole lifetime, so the figure matches what the web rollup attributes to
    the same session. The live open book (unrealized PnL, open rows, controller
    breakdown) is merged only for a base whose window is still open — the
    current-owner rule the rollup applies; a closed window keeps its realized
    slice and nothing else. A base in ``bot_names`` with no window (or no known
    ``since``) keeps the lifetime aggregate.

    ``failed_ids``, when provided, is populated with the agent_ids whose executor
    search raised — their entries may be partial/empty. This lets callers avoid
    caching a failed fetch as a genuinely empty result.
    """
    out: dict[str, AgentPerformance] = {
        aid: AgentPerformance(agent_id=aid) for aid in agent_ids
    }
    if not client or not agent_ids:
        return out

    # Fetch per-agent in parallel. A single multi-id filter was unreliable:
    # the backend sometimes returned partial data for some controller_ids,
    # causing sessions with many executors to appear as zero in the rollup
    # while the per-session endpoint showed the correct numbers.
    # The page size belongs to the layer that owns this endpoint, not to this
    # call site: asking for 50 where ``fetchers.executors`` asks 500 of the same
    # ``search_executors`` cost 10x the sequential round trips for the same rows.
    PAGE_SIZE = EXECUTORS_PAGE_SIZE
    # Safety cap, expressed in rows: the walker counts what it accumulated, not
    # how many times it looped, and its own terminal guards end a stalled walk.
    # Stated as rows so it stays put when the page size moves.
    MAX_ROWS = 10_000  # executors per agent

    async def _fetch_rows(aid: str) -> list[dict]:
        rows: list[dict] = []
        try:
            async for page in walk_pages(
                partial(client.executors.search_executors, controller_ids=[aid]),
                extract_executors_list,
                page_size=PAGE_SIZE,
                max_items=MAX_ROWS,
            ):
                for ex in page:
                    if isinstance(ex, dict):
                        rows.append(_executor_row(ex))
        except Exception as e:
            log.warning("search_executors(%s) failed: %s", aid, e)
            if failed_ids is not None:
                failed_ids.add(aid)
        return rows

    rows_lists = await asyncio.gather(*[_fetch_rows(aid) for aid in agent_ids])
    for aid, rows in zip(agent_ids, rows_lists):
        out[aid] = _build_perf_from_rows(aid, rows)

    # Controller mode: merge each agent's bot aggregates. One snapshot fetch is
    # shared across the whole batch since the API returns all bots at once.
    wanted = {
        aid: [b for b in bases if b]
        for aid, bases in (bot_names or {}).items()
        if aid in out and any(bases)
    }
    if wanted:
        import time

        from condor.agents.attribution import fold_sliced_window
        from condor.fetchers.bot_performance import (
            fetch_bot_universe,
            partition_instances,
            resolve_bots,
        )

        # Stopped instances still hold the realized PnL they earned, and a session
        # that stopped its bot before the rollup ran would otherwise report $0.
        all_bot_perf, archived = await fetch_bot_universe(client)
        now = time.time()
        for aid, bases in wanted.items():
            # Resolved per agent over ALL its bases at once, so an owned parent
            # never resolves to a tagged sibling's instance and no bot is merged
            # into the same agent twice.
            live = resolve_bots(all_bot_perf, bases)
            instances = partition_instances(all_bot_perf, bases, archived)
            owned = {
                base: w
                for base, w in ((windows or {}).get(aid) or {}).items()
                if base in bases
            }
            sliced = await _slice_owned_windows(
                client, aid, all_bot_perf, archived, owned, now
            )
            for base in bases:
                bot = live.get(base)
                window = owned.get(base)
                # An unresolved base (never deployed, or no snapshot yet) still
                # names the bot the agent operates, as the single-bot path did.
                out[aid].bot_names.append(bot.get("bot_name", base) if bot else base)
                # Everything ever deployed under the base, stopped instances
                # included — the session operated them all, and the two this one
                # wound down are exactly where its realized PnL came from.
                out[aid].bot_instances.extend(instances.get(base) or [])
                if bot and (window is None or window.is_open):
                    # Held now: the open book is this agent's, over its window's
                    # slice (or the lifetime aggregate when none could be cut).
                    _merge_bot_perf(out[aid], bot, sliced.get(base))
                elif base in sliced:
                    # Stopped, released or handed over: no open book to merge, but
                    # the slice of its history is exactly what this agent realized.
                    fold_sliced_window(out[aid], sliced[base])
                if not instances.get(base):
                    out[aid].unresolved_bases.append(base)
            if out[aid].unresolved_bases:
                log.warning(
                    "agent %s owns bases with no live or archived instance: %s — "
                    "their PnL is unknown, not zero",
                    aid,
                    ", ".join(out[aid].unresolved_bases),
                )
    return out


async def _slice_owned_windows(
    client: Any,
    aid: str,
    all_bot_perf: dict[str, dict],
    archived: list[str],
    owned: Mapping[str, OwnershipWindow],
    now: float,
) -> dict[str, tuple[float, float, float, float]]:
    """``{base: sliced (realized, volume, trades, fees)}`` over each base's window.

    One history fetch reaching back to the earliest known takeover, then one
    slice per base over its own :meth:`OwnershipWindow.bounds` — the rule
    :func:`condor.agents.attribution.apply_bot_mode_pnl` tiles with. A base with
    no known ``since`` is left out (lifetime aggregate), and so is an empty window
    (closed at or before it opened), which the rollup skips too.
    """
    from condor.fetchers.bot_performance import fetch_base_histories, slice_history

    cut = {b: w for b, w in owned.items() if w.since > 0}
    if not cut or not (all_bot_perf or archived):
        return {}
    earliest = min(w.since for w in cut.values())
    try:
        histories = await fetch_base_histories(
            client, all_bot_perf, sorted(cut), earliest, now, extra_names=archived
        )
    except Exception as e:
        # Falling back to the lifetime aggregate over-credits an adopted bot, but
        # reporting zero would be worse: the agent would read a live position as
        # costless.
        log.warning("history slice for %s failed: %s", aid, e)
        return {}
    out: dict[str, tuple[float, float, float, float]] = {}
    for base, window in cut.items():
        start, stop = window.bounds(now)
        if stop > start:
            out[base] = slice_history(histories.get(base, []), start, stop)
    return out

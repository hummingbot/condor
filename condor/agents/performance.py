"""Shared aggregator for trading agent performance.

Single source of truth for PnL / volume / trade stats for a given ``agent_id``
(``controller_id`` tag on executors). Used both by the live ``ExecutorsProvider``
and the web API so they always agree.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

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
    controllers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def bot_name(self) -> str:
        """The first operated bot — wire compat for single-bot consumers."""
        return self.bot_names[0] if self.bot_names else ""

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "bot_name": self.bot_name}


def _extract_executors_list(result: Any) -> list[dict]:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("executors", "data", "results", "items"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


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
    since: float = 0.0,
) -> AgentPerformance:
    """Fetch authoritative performance for a single ``agent_id``.

    When ``bot_names`` is given, the agent is in controller mode: each named bot's
    PnL is merged into the returned totals (see
    :func:`fetch_agent_performance_batch`).

    ``since`` is the instant this session took the bots over. With it, the bot's
    realized/volume/trades/fees are sliced to ``[since, now)`` exactly as the web
    rollup slices them, so a session that adopted a long-running bot is not
    credited with the PnL it inherited. Without it the whole lifetime aggregate is
    merged, which is only right for a session that deployed the bot itself.
    """
    names = [b for b in (bot_names or []) if b]
    batch = await fetch_agent_performance_batch(
        client,
        [agent_id],
        {agent_id: names} if names else None,
        since={agent_id: since} if names and since > 0 else None,
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
    from condor.fetchers.bot_performance import bot_executor_rows

    rows = bot_executor_rows(bot)
    open_rows = [r for r in rows if r["status"] == "RUNNING"]

    if window is None:
        closes = int(bot.get("closed_trades", 0) or 0)
        perf.realized_pnl += float(bot.get("realized_pnl_quote", 0) or 0)
        perf.volume += float(bot.get("volume_traded", 0) or 0)
        perf.fees += float(bot.get("cum_fees_quote", 0) or 0)
    else:
        realized, volume, trades, fees = window
        closes = int(round(trades))
        perf.realized_pnl += realized
        perf.volume += volume
        # A backend that reports no cumulative fee column slices to zero; the
        # live open-position figure is then the only one there is.
        perf.fees += fees if fees else float(bot.get("cum_fees_quote", 0) or 0)

    perf.trade_count += closes
    perf.closed_count += closes
    perf.unrealized_pnl += float(bot.get("unrealized_pnl_quote", 0) or 0)
    perf.total_pnl = perf.realized_pnl + perf.unrealized_pnl
    perf.controllers = perf.controllers + list(bot.get("controllers", []))
    perf.executors = perf.executors + rows
    perf.open_count += len(open_rows)


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
    since: dict[str, float] | None = None,
) -> dict[str, AgentPerformance]:
    """Batched multi-agent fetch via a single cursor-paginated executor search.

    ``bot_names`` maps ``agent_id -> the bases it owns`` for agents running in
    controller mode; each such agent's bot figures (one shared snapshot fetch for
    the whole batch) are merged into its executor-derived totals.

    ``since`` maps ``agent_id -> the instant it took its bots over``. Where it is
    known, that agent's bot realized/volume/trades/fees are sliced from the
    controller history to ``[since, now)`` instead of taking the bot's whole
    lifetime, so the figure matches what the web rollup attributes to the same
    session.

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
    PAGE_SIZE = 50
    MAX_PAGES = 200  # safety cap → 10,000 executors per agent

    async def _fetch_rows(aid: str) -> list[dict]:
        rows: list[dict] = []
        cursor: str | None = None
        try:
            for _ in range(MAX_PAGES):
                kwargs: dict[str, Any] = {
                    "controller_ids": [aid],
                    "limit": PAGE_SIZE,
                }
                if cursor:
                    kwargs["cursor"] = cursor
                result = await client.executors.search_executors(**kwargs)
                page = _extract_executors_list(result)
                for ex in page:
                    if isinstance(ex, dict):
                        rows.append(_executor_row(ex))

                next_cursor = None
                if isinstance(result, dict):
                    next_cursor = result.get("next_cursor") or result.get("cursor")
                    pagination = result.get("pagination")
                    if not next_cursor and isinstance(pagination, dict):
                        next_cursor = pagination.get("next_cursor") or pagination.get(
                            "cursor"
                        )
                if not next_cursor or len(page) < PAGE_SIZE:
                    break
                if next_cursor == cursor:
                    # The API echoed the cursor we sent: the walk is not
                    # progressing. Re-issuing it would re-append the same page
                    # and inflate this agent's PnL, volume and trade count.
                    break
                cursor = next_cursor
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

        from condor.fetchers.bot_performance import (
            fetch_all_bot_performance,
            fetch_base_histories,
            resolve_bots,
            slice_history,
        )

        try:
            all_bot_perf = await fetch_all_bot_performance(client)
        except Exception as e:
            log.warning("fetch_all_bot_performance failed: %s", e)
            all_bot_perf = {}
        now = time.time()
        for aid, bases in wanted.items():
            # Resolved per agent over ALL its bases at once, so an owned parent
            # never resolves to a tagged sibling's instance and no bot is merged
            # into the same agent twice.
            live = resolve_bots(all_bot_perf, bases)
            start = float((since or {}).get(aid, 0.0) or 0.0)
            windows: dict[str, tuple[float, float, float, float]] = {}
            if start > 0 and all_bot_perf:
                try:
                    histories = await fetch_base_histories(
                        client, all_bot_perf, bases, start, now
                    )
                    windows = {
                        base: slice_history(hs, start, now)
                        for base, hs in histories.items()
                    }
                except Exception as e:
                    # Falling back to the lifetime aggregate over-credits an
                    # adopted bot, but reporting zero would be worse: the agent
                    # would read a live position as costless.
                    log.warning("history slice for %s failed: %s", aid, e)
            for base in bases:
                bot = live.get(base)
                # An unresolved base (never deployed, or no snapshot yet) still
                # names the bot the agent operates, as the single-bot path did.
                out[aid].bot_names.append(bot.get("bot_name", base) if bot else base)
                if bot:
                    _merge_bot_perf(out[aid], bot, windows.get(base))
    return out

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
    win_rate: float = 0.0
    open_count: int = 0
    closed_count: int = 0
    executors: list[dict[str, Any]] = field(default_factory=list)
    # Controller-mode attribution: when the agent operates a named bot, the bot's
    # aggregate PnL is merged into the totals above and surfaced here for transparency.
    bot_name: str = ""
    controllers: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_executors_list(result: Any) -> list[dict]:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("executors", "data", "results", "items"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


def _executor_row(ex: dict) -> dict[str, Any]:
    from condor.fetchers.executors import (
        get_executor_fees,
        get_executor_pnl,
        get_executor_volume,
    )

    cfg = ex.get("config", ex) if isinstance(ex.get("config"), dict) else ex
    custom_info = ex.get("custom_info") or {}
    if not isinstance(custom_info, dict):
        custom_info = {}

    # entry_price is a display-only field; PnL comes straight from the executor's
    # reported fields via get_executor_pnl() and never depends on it.
    # Only position executors carry a real entry_price (config > top-level > custom_info).
    # Grid/DCA executors expose break_even_price instead; use it as the display "entry".
    _cfg_entry = float(cfg.get("entry_price") or 0)
    _top_entry = float(ex.get("entry_price") or 0)
    _ci_entry = float(custom_info.get("current_position_average_price") or 0)
    _be_price = float(custom_info.get("break_even_price") or 0)
    entry_price = _cfg_entry or _top_entry or _ci_entry or _be_price or 0.0
    # A position executor with no entry_price is genuinely suspicious; everything
    # else legitimately lacks one, so don't warn for them.
    _ex_type = str(cfg.get("type") or ex.get("type") or "").lower()
    if entry_price == 0.0 and "position" in _ex_type:
        log.warning(
            "entry_price fell back to 0.0 for position executor %s — PnL may be wrong",
            ex.get("id") or ex.get("executor_id") or "?",
        )

    # current_price / close_price: top-level > custom_info
    _top_cur = float(ex.get("current_price") or 0)
    _ci_close = float(custom_info.get("close_price") or 0)
    current_price = _top_cur if _top_cur > 0 else (_ci_close if _ci_close > 0 else 0.0)

    return {
        "id": str(ex.get("id") or ex.get("executor_id") or ""),
        "type": cfg.get("type") or ex.get("type") or "",
        "connector": cfg.get("connector_name")
        or ex.get("connector_name")
        or cfg.get("connector")
        or ex.get("connector")
        or "",
        "pair": cfg.get("trading_pair") or ex.get("trading_pair") or "",
        "side": str(cfg.get("side") or ex.get("side") or ""),
        "status": str(ex.get("status") or "").upper(),
        "close_type": str(ex.get("close_type") or ""),
        "pnl": get_executor_pnl(ex),
        "volume": get_executor_volume(ex),
        "fees": get_executor_fees(ex),
        "entry_price": entry_price,
        "current_price": current_price,
        "amount": float(cfg.get("total_amount_quote") or cfg.get("amount") or 0),
        "timestamp": float(cfg.get("timestamp") or ex.get("timestamp") or 0),
        "close_timestamp": float(ex.get("close_timestamp") or 0),
        "controller_id": str(cfg.get("controller_id") or ex.get("controller_id") or ""),
        "custom_info": custom_info,
        "config": ex.get("config") if isinstance(ex.get("config"), dict) else {},
    }


async def fetch_agent_performance(
    client: Any, agent_id: str, bot_name: str = ""
) -> AgentPerformance:
    """Fetch authoritative performance for a single ``agent_id``.

    When ``bot_name`` is given, the agent is in controller mode: the named bot's
    aggregate PnL is merged into the returned totals (see
    :func:`fetch_agent_performance_batch`).
    """
    bot_names = {agent_id: bot_name} if bot_name else None
    batch = await fetch_agent_performance_batch(client, [agent_id], bot_names)
    return batch.get(agent_id, AgentPerformance(agent_id=agent_id, bot_name=bot_name))


def _merge_bot_perf(perf: AgentPerformance, bot: dict[str, Any]) -> None:
    """Fold a bot's aggregate into an executor-derived ``AgentPerformance`` in place.

    The two sources are disjoint (bot controllers tag executors with their own
    config ids, never the ``agent_id``), so the merge is plain addition — no
    de-duplication. Fees are left untouched: the bot snapshot carries no fee field.
    """
    perf.realized_pnl += float(bot.get("realized_pnl_quote", 0) or 0)
    perf.unrealized_pnl += float(bot.get("unrealized_pnl_quote", 0) or 0)
    perf.total_pnl = perf.realized_pnl + perf.unrealized_pnl
    perf.volume += float(bot.get("volume_traded", 0) or 0)
    perf.bot_name = bot.get("bot_name", perf.bot_name)
    perf.controllers = bot.get("controllers", [])


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

    win_rate = 0.0
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
    bot_names: dict[str, str] | None = None,
) -> dict[str, AgentPerformance]:
    """Batched multi-agent fetch via a single cursor-paginated executor search.

    ``bot_names`` maps ``agent_id -> bot_name`` for agents running in controller
    mode; each such agent's bot aggregate (one shared snapshot fetch for the whole
    batch) is merged into its executor-derived totals.
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
                cursor = next_cursor
        except Exception as e:
            log.warning("search_executors(%s) failed: %s", aid, e)
        return rows

    rows_lists = await asyncio.gather(*[_fetch_rows(aid) for aid in agent_ids])
    for aid, rows in zip(agent_ids, rows_lists):
        out[aid] = _build_perf_from_rows(aid, rows)

    # Controller mode: merge each agent's bot aggregate. One snapshot fetch is
    # shared across the whole batch since the API returns all bots at once.
    wanted = {bn for bn in (bot_names or {}).values() if bn}
    if wanted:
        from condor.fetchers.bot_performance import fetch_all_bot_performance

        try:
            all_bot_perf = await fetch_all_bot_performance(client)
        except Exception as e:
            log.warning("fetch_all_bot_performance failed: %s", e)
            all_bot_perf = {}
        for aid, bn in (bot_names or {}).items():
            if not bn or aid not in out:
                continue
            out[aid].bot_name = bn
            bot = all_bot_perf.get(bn)
            if bot:
                _merge_bot_perf(out[aid], bot)
    return out

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

    # entry_price: prefer custom_info (position executors store it there)
    entry_price = (
        float(cfg.get("entry_price") or 0)
        or float(ex.get("entry_price") or 0)
        or float(custom_info.get("current_position_average_price") or 0)
    )
    # current_price / close_price
    current_price = (
        float(ex.get("current_price") or 0)
        or float(custom_info.get("close_price") or 0)
    )

    ex_id = ex.get("id") or ex.get("executor_id") or "unknown"
    if entry_price == 0.0:
        log.warning("Executor %s: entry_price fell back to 0.0 — PnL may be misleading", ex_id)
    if current_price == 0.0:
        log.warning("Executor %s: current_price fell back to 0.0 — PnL may be misleading", ex_id)

    return {
        "id": str(ex.get("id") or ex.get("executor_id") or ""),
        "type": cfg.get("type") or ex.get("type") or "",
        "connector": cfg.get("connector_name") or ex.get("connector_name") or cfg.get("connector") or ex.get("connector") or "",
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


async def fetch_agent_performance(client: Any, agent_id: str) -> AgentPerformance:
    """Fetch authoritative performance for a single ``agent_id``."""
    batch = await fetch_agent_performance_batch(client, [agent_id])
    return batch.get(agent_id, AgentPerformance(agent_id=agent_id))


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
    client: Any, agent_ids: list[str]
) -> dict[str, AgentPerformance]:
    """Batched multi-agent fetch via a single cursor-paginated executor search."""
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
    return out

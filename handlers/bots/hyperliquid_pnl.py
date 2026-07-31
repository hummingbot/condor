"""
Time-range PnL/volume/fee summaries sourced directly from Hyperliquid's own fill
history, bypassing hummingbot-api's per-bot-instance performance bookkeeping.

Why this exists: the dashboard's per-controller `volume_traded`/`global_pnl_quote`
figures (from hummingbot-api's `/bot-orchestration/status`) are seeded from a
controller's own `positions_held`, which only advances on a formally-closed
executor and resets to whatever `initial_positions` says on every redeploy. Across
several redeploys in one day, that makes cumulative "how are we doing today/this
week" tracking impossible from that field alone -- confirmed live 2026-07-29.

Hyperliquid's own fill history already carries `closedPnl` per fill (computed by
the exchange itself, not replayed by us), so summing it directly over a wall-clock
time window is accurate regardless of how many times any bot has been restarted in
that window -- it's scoped to (account, coin, time range), not to any specific bot
process.

Known limitations (Hyperliquid API, not this code):
- `userFillsByTime` does NOT support a server-side `coin` filter despite some
  third-party docs claiming otherwise -- confirmed empirically 2026-07-29 (the
  response still contained every traded coin). We fetch all coins for the window
  in one pass and group client-side, which is actually more efficient here since
  one fetch covers every bot sharing the account.
- Capped at 2000 fills per response; paginated here by advancing past the latest
  fill timestamp seen, deduped by `tid` to guard against any boundary overlap.
- Hyperliquid only exposes the 10,000 most recent fills account-wide. A very old
  start_time on a highly active account can silently return incomplete history --
  there is no way to detect or work around that from the API alone.
"""

import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
_MAX_FILLS_PER_PAGE = 2000
_REQUEST_TIMEOUT_SECONDS = 15


async def fetch_account_fills(
    address: str,
    start_time_ms: int,
    end_time_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch every fill for the account between start_time_ms and end_time_ms
    (inclusive), across all coins, paginating past Hyperliquid's 2000-per-call cap.

    Returns the raw fill dicts as Hyperliquid provides them (coin, px, sz, side,
    time, closedPnl, fee, tid, ...), deduplicated by `tid`.
    """
    fills_by_tid: Dict[Any, Dict[str, Any]] = {}
    cursor_start = start_time_ms
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            payload: Dict[str, Any] = {
                "type": "userFillsByTime",
                "user": address,
                "startTime": cursor_start,
            }
            if end_time_ms is not None:
                payload["endTime"] = end_time_ms

            async with session.post(HYPERLIQUID_INFO_URL, json=payload) as resp:
                resp.raise_for_status()
                batch = await resp.json()

            if not batch:
                break

            for fill in batch:
                fills_by_tid[fill.get("tid")] = fill

            if len(batch) < _MAX_FILLS_PER_PAGE:
                break

            latest_seen = max(fill["time"] for fill in batch)
            next_cursor = latest_seen + 1
            if next_cursor <= cursor_start:
                # Defensive: avoid an infinite loop if timestamps ever fail to advance.
                break
            cursor_start = next_cursor

    return list(fills_by_tid.values())


def summarize_fills_by_coin(fills: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Group fills by coin and sum realized PnL, fees, volume, and trade count.

    Realized PnL is Hyperliquid's own `closedPnl` per fill -- not recomputed here --
    so it's exact, not an approximation from replaying OPEN/CLOSE trade pairs.
    """
    by_coin: Dict[str, Dict[str, Any]] = {}
    for fill in fills:
        coin = fill.get("coin", "Unknown")
        bucket = by_coin.setdefault(
            coin,
            {"realized_pnl": 0.0, "fees": 0.0, "volume": 0.0, "trade_count": 0},
        )
        bucket["realized_pnl"] += float(fill.get("closedPnl", 0) or 0)
        bucket["fees"] += float(fill.get("fee", 0) or 0)
        bucket["volume"] += float(fill.get("px", 0) or 0) * float(fill.get("sz", 0) or 0)
        bucket["trade_count"] += 1
    return by_coin


async def get_pnl_summary(
    address: str,
    start_time_ms: int,
    end_time_ms: Optional[int] = None,
    coins: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Fetch and summarize PnL/volume/fees for an account over a time window.

    If `coins` is given, the per-coin breakdown is filtered to just those (still
    fetched via one account-wide call -- see module docstring on why there's no
    server-side coin filter), and a `total` across just those coins is included
    alongside the full per-coin breakdown.
    """
    fills = await fetch_account_fills(address, start_time_ms, end_time_ms)
    by_coin = summarize_fills_by_coin(fills)

    if coins is not None:
        selected = {c: by_coin[c] for c in coins if c in by_coin}
    else:
        selected = by_coin

    total = {"realized_pnl": 0.0, "fees": 0.0, "volume": 0.0, "trade_count": 0}
    for bucket in selected.values():
        total["realized_pnl"] += bucket["realized_pnl"]
        total["fees"] += bucket["fees"]
        total["volume"] += bucket["volume"]
        total["trade_count"] += bucket["trade_count"]

    return {
        "start_time_ms": start_time_ms,
        "end_time_ms": end_time_ms,
        "by_coin": selected,
        "total": total,
    }


def midnight_utc_ms(now: Optional[float] = None) -> int:
    """Start of the current UTC calendar day, in milliseconds."""
    now = now if now is not None else time.time()
    return int(now // 86400) * 86400 * 1000

"""Fetch one archived run's performance, whole and cached.

An archived run is a sqlite file a stopped bot's container left behind. Reading
one is expensive and unbounded — the trade table is walked page by page with
retries, the executors are fetched and normalized, and every quote-denominated
figure is restated in USD — and the answer never changes, because the file is
immutable. So the walk happens once per ``(server, db_path)`` and every reader
of that run shares it: the performance route, the executors page, the
controller rollup, and the ``archived_analyzer`` routine that charts it.

It lives here rather than in the route that used to own it because the routine
is now one of its callers, and a routine published to ``agents/_shared/routines``
must not reach into the web package to read an archive.

The one thing this module borrows from ``condor.web`` is ``models`` — the
pydantic wire shapes, which import nothing from condor and so introduce no cycle
the package's no-web rule exists to prevent. It raises
:class:`ArchivedRunUnavailable` rather than ``HTTPException``; mapping that to a
status code is the route's job.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from typing import Any

from condor.fetchers.executors import normalize_executor_side
from condor.web.models import (
    ArchivedBotPerformance,
    ArchivedChartSeries,
    NormalizedExecutor,
    PnlPoint,
)

logger = logging.getLogger(__name__)


class ArchivedRunUnavailable(Exception):
    """The run could not be read. ``missing`` separates "no such db" from "the
    backend failed", which is the whole of what a caller needs to decide
    between a 404 and a 502."""

    def __init__(self, detail: str, *, missing: bool = False):
        super().__init__(detail)
        self.detail = detail
        self.missing = missing


# Module-level LRU cache for archived data (immutable per db_path, so no TTL —
# but bounded, since each entry holds up to ~5000 PnL points plus the full
# executor list and a long-lived process would otherwise grow without ceiling).
# Key: (server_name, db_path) -> cached result. Executors live only inside the
# performance entry; the executors route pages out of it.
_PERFORMANCE_CACHE_MAX = 32
_performance_cache: OrderedDict[tuple[str, str], ArchivedBotPerformance] = OrderedDict()

# In-flight fetches keyed like the cache, so concurrent cold-cache requests for
# the same archived bot share one backend walk (same idiom as
# condor.pool_data._single_flight).
_performance_inflight: dict[tuple[str, str], "asyncio.Task[ArchivedBotPerformance]"] = (
    {}
)

# Attempts per page of the archived trade walk. A run with tens of thousands of
# trades needs dozens of round trips, and one transient failure must not decide
# the whole run's headline numbers.
_TRADE_PAGE_ATTEMPTS = 3


def _cache_get(cache_key: tuple[str, str]) -> ArchivedBotPerformance | None:
    """Return the cached entry, marking it most-recently-used."""
    perf = _performance_cache.get(cache_key)
    if perf is not None:
        _performance_cache.move_to_end(cache_key)
    return perf


def _cache_put(cache_key: tuple[str, str], perf: ArchivedBotPerformance) -> None:
    """Store an entry, evicting the least-recently-used past the cap."""
    _performance_cache[cache_key] = perf
    _performance_cache.move_to_end(cache_key)
    while len(_performance_cache) > _PERFORMANCE_CACHE_MAX:
        _performance_cache.popitem(last=False)


def cached_run(name: str, db_path: str) -> ArchivedBotPerformance | None:
    """The warm entry for this run, or ``None``. Never fetches."""
    return _cache_get((name, db_path))


def extract_bot_name(db_path: str) -> str:
    """The bot's name, out of the archive filename."""
    name = os.path.basename(db_path)
    if name.endswith(".sqlite"):
        name = name[:-7]
    elif name.endswith(".db"):
        name = name[:-3]
    return name


def _parse_json_field(val: Any) -> dict:
    """Parse a field that may be a JSON string or already a dict."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip().startswith("{"):
        import json

        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


def _to_epoch_seconds(ts: Any) -> float:
    """Convert various timestamp formats to epoch seconds."""
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return ts / 1000 if ts > 1e12 else float(ts)
    if hasattr(ts, "timestamp"):
        return ts.timestamp()
    return 0.0


def _normalize_executors(raw: list[dict]) -> list[NormalizedExecutor]:
    """Map raw executor dicts from hummingbot DB to normalized format."""
    result = []
    for ex in raw:
        if not isinstance(ex, dict):
            continue

        custom_info = _parse_json_field(ex.get("custom_info", {}))
        config = _parse_json_field(ex.get("config", {}))

        # Resolve side from multiple sources
        side_raw = ex.get("side") or custom_info.get("side") or config.get("side", "")

        # Resolve connector
        connector = str(
            ex.get("connector", "")
            or ex.get("connector_name", "")
            or config.get("connector_name", "")
        )

        # Resolve trading pair
        trading_pair = str(ex.get("trading_pair", "") or config.get("trading_pair", ""))

        # Resolve prices
        entry_price = float(
            ex.get("entry_price", 0)
            or custom_info.get("current_position_average_price", 0)
            or 0
        )
        close_price = float(
            ex.get("close_price", 0)
            or ex.get("current_price", 0)
            or custom_info.get("close_price", 0)
            or 0
        )

        # Resolve PnL and fees
        pnl = float(ex.get("net_pnl_quote", 0) or ex.get("pnl", 0) or 0)
        fees = float(ex.get("cum_fees_quote", 0) or 0)
        volume = float(ex.get("filled_amount_quote", 0) or ex.get("volume", 0) or 0)
        net_pnl_pct = float(ex.get("net_pnl_pct", 0) or 0)

        result.append(
            NormalizedExecutor(
                id=str(ex.get("id", "") or ex.get("executor_id", "")),
                type=str(
                    ex.get("type", "")
                    or ex.get("executor_type", "")
                    or config.get("type", "position")
                ),
                connector=connector,
                trading_pair=trading_pair,
                side=normalize_executor_side(side_raw),
                status=str(ex.get("status", "") or "closed"),
                close_type=str(ex.get("close_type", "") or ""),
                pnl=pnl,
                volume=volume,
                timestamp=_to_epoch_seconds(ex.get("timestamp")),
                close_timestamp=_to_epoch_seconds(ex.get("close_timestamp")),
                entry_price=entry_price,
                current_price=close_price,
                cum_fees_quote=fees,
                net_pnl_pct=net_pnl_pct,
                controller_id=str(ex.get("controller_id", "")),
                custom_info=custom_info,
                config=config,
            )
        )
    return result


def _derive_primary_pair(
    executors: list[NormalizedExecutor],
    exchanges: list[str],
    trading_pairs: list[str],
) -> tuple[str, str]:
    """Find the most common connector + trading pair from executors."""
    from collections import Counter

    pair_counts: Counter[tuple[str, str]] = Counter()
    for ex in executors:
        if ex.connector and ex.trading_pair:
            pair_counts[(ex.connector, ex.trading_pair)] += 1

    if pair_counts:
        (connector, pair), _ = pair_counts.most_common(1)[0]
        return connector, pair

    # Fallback to summary data
    connector = exchanges[0] if exchanges else ""
    pair = trading_pairs[0] if trading_pairs else ""
    return connector, pair


async def fetch_archived_run(
    client: Any, name: str, db_path: str
) -> ArchivedBotPerformance:
    """Return cached performance data, coalescing concurrent cold-cache fetches.

    The fetch runs as a detached task and awaiters ``shield`` it, so one caller
    navigating away and cancelling their request cannot also cancel the fetch
    the remaining callers are waiting on.
    """
    cache_key = (name, db_path)

    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    task = _performance_inflight.get(cache_key)
    if task is None or task.done():
        task = asyncio.ensure_future(_fetch_performance(client, name, db_path))
        _performance_inflight[cache_key] = task

        def _clear(finished: "asyncio.Task", _key: tuple[str, str] = cache_key) -> None:
            if _performance_inflight.get(_key) is finished:
                _performance_inflight.pop(_key, None)

        task.add_done_callback(_clear)
    return await asyncio.shield(task)


async def _fetch_performance(
    client: Any, name: str, db_path: str
) -> ArchivedBotPerformance:
    """Fetch full performance data from the backend and cache it."""
    cache_key = (name, db_path)

    # Fetch summary
    try:
        summary = await client.archived_bots.get_database_summary(db_path)
    except Exception as e:
        raise ArchivedRunUnavailable(f"Failed to fetch summary: {e}")

    if not summary or not isinstance(summary, dict):
        raise ArchivedRunUnavailable("Database not found", missing=True)

    bot_name = summary.get("bot_name") or extract_bot_name(db_path)
    trading_pairs = summary.get("trading_pairs", [])
    exchanges = summary.get("exchanges", [])

    # Fetch all trades (paginated).
    #
    # A run with tens of thousands of trades takes dozens of round trips, and a
    # single transient 5xx used to end the walk silently: the summary was then
    # built from a truncated (often empty) trade list, reported $0.00 across the
    # header, and was cached under that. Each page is retried, and a walk that
    # still fails is reported as incomplete so the caller can fall back to
    # executors rather than publishing zeros as fact.
    all_trades: list[dict] = []
    offset = 0
    limit = 500
    trades_complete = True
    while True:
        resp = None
        last_error: Exception | None = None
        for attempt in range(_TRADE_PAGE_ATTEMPTS):
            try:
                resp = await client.archived_bots.get_database_trades(
                    db_path, limit=limit, offset=offset
                )
                last_error = None
                break
            except Exception as e:
                last_error = e
                if attempt + 1 < _TRADE_PAGE_ATTEMPTS:
                    await asyncio.sleep(0.5 * (attempt + 1))

        if last_error is not None:
            logger.warning(
                "Archived trade walk for %s stopped at offset %d after %d "
                "attempts: %s",
                db_path,
                offset,
                _TRADE_PAGE_ATTEMPTS,
                last_error,
            )
            trades_complete = False
            break

        if not resp:
            break
        trades = resp.get("trades", [])
        if not trades:
            break
        all_trades.extend(trades)
        if len(trades) < limit:
            break
        offset += limit

    # Fetch executors
    raw_executors: list[dict] = []
    try:
        exec_resp = await client.archived_bots.get_database_executors(db_path)
        if exec_resp and isinstance(exec_resp, dict):
            raw_executors = exec_resp.get("executors", [])
        elif exec_resp and isinstance(exec_resp, list):
            raw_executors = exec_resp
    except Exception:
        pass

    # Normalize executors to consistent field names
    executors = _normalize_executors(raw_executors)

    # Derive primary connector and trading pair
    primary_connector, primary_trading_pair = _derive_primary_pair(
        executors, exchanges, trading_pairs
    )

    # Resolve one USD rate per quote currency before any money is totalled.
    # A run's PnL, fees and volume are quote-denominated, and most of this
    # history is BRL-quoted, so reporting them behind a "$" without this
    # overstated every such run by the whole BRL/USD rate.
    from condor.quote_conversion import (
        convert_trades_to_usd,
        quote_of,
        quotes_in,
        resolve_usd_rates,
    )

    pair_sources = [ex.trading_pair for ex in executors if ex.trading_pair]
    pair_sources.extend(trading_pairs)
    quote_rates = await resolve_usd_rates(name, quotes_in(pair_sources))
    convert_trades_to_usd(all_trades, quote_rates)

    # Calculate PnL, preferring trades and falling back to executors.
    # A run can archive tens of thousands of executors alongside an empty
    # trades table; reading the summary only from trades then reports zeros
    # across the header while the chart below it, which reads executors,
    # plots real PnL. Whichever source answered is reported as
    # ``stats_source`` so the UI can label the count card honestly.
    from condor.archived_pnl import (
        calculate_pnl_from_executors,
        calculate_pnl_from_trades,
    )

    if all_trades and trades_complete:
        stats_source = "trades"
        pnl_data = calculate_pnl_from_trades(all_trades)
        fill_count = len(all_trades)
        buy_count = sum(
            1 for t in all_trades if str(t.get("trade_type", "")).upper() == "BUY"
        )
        sell_count = fill_count - buy_count
    else:
        stats_source = "executors"
        pnl_data = calculate_pnl_from_executors(executors, quote_rates)
        fill_count = len(executors)
        # Counted separately rather than as the complement of buys: an
        # unrecognized side stays uncounted instead of inflating the sells.
        buy_count = sum(1 for ex in executors if ex.side == "BUY")
        sell_count = sum(1 for ex in executors if ex.side == "SELL")

    # Convert cumulative_pnl to PnlPoint list with epoch seconds
    raw_cumulative = pnl_data.get("cumulative_pnl", [])
    cumulative_pnl: list[PnlPoint] = []
    for point in raw_cumulative:
        ts = point.get("timestamp")
        if ts is None:
            continue
        if hasattr(ts, "timestamp"):
            epoch = ts.timestamp()
        elif isinstance(ts, (int, float)):
            epoch = ts / 1000 if ts > 1e12 else ts
        else:
            continue
        cumulative_pnl.append(PnlPoint(timestamp=epoch, pnl=point.get("pnl", 0)))

    # Downsample if >5000 points
    if len(cumulative_pnl) > 5000:
        step = len(cumulative_pnl) // 5000
        cumulative_pnl = cumulative_pnl[::step]
        if cumulative_pnl[-1] != raw_cumulative[-1]:
            last = raw_cumulative[-1]
            ts = last.get("timestamp")
            if hasattr(ts, "timestamp"):
                epoch = ts.timestamp()
            elif isinstance(ts, (int, float)):
                epoch = ts / 1000 if ts > 1e12 else ts
            else:
                epoch = 0
            cumulative_pnl.append(PnlPoint(timestamp=epoch, pnl=last.get("pnl", 0)))

    from condor.archived_chart_series import build_chart_series

    chart_series = {
        key: ArchivedChartSeries(**payload)
        for key, payload in build_chart_series(executors, quote_rates).items()
    }

    # Each executor carries its own rate so the executor table can render its
    # money columns in USD without re-deriving the market's quote, while prices
    # on the row stay native to the market they traded in.
    for ex in executors:
        ex.usd_rate = quote_rates.for_pair(ex.trading_pair)

    result = ArchivedBotPerformance(
        bot_name=bot_name,
        db_path=db_path,
        total_pnl=pnl_data.get("total_pnl", 0),
        total_fees=pnl_data.get("total_fees", 0),
        total_volume=pnl_data.get("total_volume", 0),
        trade_count=fill_count,
        stats_source=stats_source,
        buy_count=buy_count,
        sell_count=sell_count,
        pnl_by_pair=pnl_data.get("pnl_by_pair", {}),
        cumulative_pnl=cumulative_pnl,
        trading_pairs=trading_pairs,
        exchanges=exchanges,
        executors=executors,
        primary_connector=primary_connector,
        primary_trading_pair=primary_trading_pair,
        executor_count=len(executors),
        chart_series=chart_series,
        quote_currency=quote_of(primary_trading_pair),
        usd_rates=quote_rates.rates,
        converted=quote_rates.converted,
    )

    # Cache full result (executors included — the executors route pages out of it)
    _cache_put(cache_key, result)
    return result

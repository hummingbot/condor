"""Chart series for an archived run, aggregated over *every* executor.

The archived-run chart used to be drawn from whatever the first executor page
happened to hold -- 50 rows out of a run that archived tens of thousands. That
produced a chart captioned "50 executors": one lonely volume bar, a PnL curve
that stopped minutes into the run, and a candle window sized from those 50 rows
rather than from the run, so a 28-minute run was charted with hourly candles.

Shipping all the executors to the browser instead is not the fix -- the series
the chart actually draws are bounded by the number of candles, not by the
number of executors, so they are aggregated here and sent as a handful of
kilobytes. Overlays (per-executor entry/exit markers) are deliberately *not*
here: above a small threshold the chart replaces them with the volume
histogram, so they stay a client-side concern over the executor page.

Pure computation over normalized executors (attribute access, not dicts), with
no client, no plotting and no web imports, so both the web routes and routines
can call it.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

# Above this many points the PnL curve is thinned before it goes over the wire.
# A run with 50k executors would otherwise send 50k points to draw a line a few
# hundred pixels wide.
_MAX_PNL_POINTS = 2000


class Interval(NamedTuple):
    """A candle interval as both the API's string and its width in seconds."""

    name: str
    seconds: int


def pick_interval(duration_sec: float) -> Interval:
    """Candle interval proportionate to how long the run actually lasted.

    Mirrors the thresholds the chart component used client-side. It lives
    server-side now because the choice depends on the *true* activity range,
    which only the full executor set knows.
    """
    if duration_sec < 2 * 3600:
        return Interval("1m", 60)
    if duration_sec < 12 * 3600:
        return Interval("5m", 300)
    if duration_sec < 3 * 86400:
        return Interval("15m", 900)
    if duration_sec < 14 * 86400:
        return Interval("1h", 3600)
    return Interval("4h", 14400)


def _open_at(ex: Any) -> float:
    return float(getattr(ex, "timestamp", 0) or 0)


def _close_at(ex: Any) -> float:
    return float(getattr(ex, "close_timestamp", 0) or 0)


def _amount(ex: Any) -> float:
    """Base-asset size of an executor, from whichever field records it.

    Mirrors the chart's own resolution order: the config's declared amount, then
    the runtime one, then back-computed from quote volume and entry price.
    """
    for source in (getattr(ex, "config", None), getattr(ex, "custom_info", None)):
        if isinstance(source, dict):
            try:
                amount = float(source.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if amount > 0:
                return amount

    volume = float(getattr(ex, "volume", 0) or 0)
    entry = float(getattr(ex, "entry_price", 0) or 0)
    if volume > 0 and entry > 0:
        return volume / entry
    return 0.0


def _pool_address(executors: list[Any]) -> str | None:
    """The pool these DEX/LP executors traded in, from whichever records one."""
    for ex in executors:
        for source in (getattr(ex, "config", None), getattr(ex, "custom_info", None)):
            if isinstance(source, dict):
                pool = source.get("pool_address")
                if pool:
                    return str(pool)
    return None


def activity_range(executors: list[Any]) -> tuple[float, float]:
    """First open and last close across the executors, in epoch seconds.

    An executor that never closed contributes its open stamp to the end, so a
    still-open position cannot drag the window to the epoch or leave it short.
    Returns ``(0.0, 0.0)`` when nothing carries a usable timestamp.
    """
    start = 0.0
    end = 0.0
    for ex in executors:
        opened = _open_at(ex)
        closed = _close_at(ex)
        if opened > 0:
            start = opened if start == 0 else min(start, opened)
            end = max(end, opened)
        if closed > 0:
            start = closed if start == 0 else min(start, closed)
            end = max(end, closed)
    return start, end


def _volume_buckets(
    executors: list[Any], width: int, rate: float = 1.0
) -> list[dict[str, Any]]:
    """Executor volume summed into candle-width buckets, split by side.

    ``rate`` restates the quote-denominated volume in USD; it is constant across
    the group because a group is one market.
    """
    buckets: dict[float, dict[str, Any]] = {}
    for ex in executors:
        opened = _open_at(ex)
        if opened <= 0:
            continue
        bucket_time = (opened // width) * width
        bucket = buckets.get(bucket_time)
        if bucket is None:
            bucket = {
                "time": bucket_time,
                "buy_vol": 0.0,
                "sell_vol": 0.0,
                "buy_count": 0,
                "sell_count": 0,
            }
            buckets[bucket_time] = bucket

        volume = float(getattr(ex, "volume", 0) or 0)
        if volume < 0:
            volume = 0.0
        volume *= rate
        if str(getattr(ex, "side", "") or "").upper() == "BUY":
            bucket["buy_vol"] += volume
            bucket["buy_count"] += 1
        else:
            bucket["sell_vol"] += volume
            bucket["sell_count"] += 1

    return [buckets[t] for t in sorted(buckets)]


def _position_deltas(executors: list[Any], width: int) -> list[dict[str, Any]]:
    """Net base-asset change per bucket: opens add exposure, closes remove it.

    The client running-sums these to draw the net-position pane, which is why
    deltas travel rather than levels -- a delta series is sparse where nothing
    happened, and the running sum is exact at every bucket boundary regardless.
    """
    deltas: dict[float, float] = {}

    def _add(when: float, delta: float) -> None:
        if when <= 0 or delta == 0:
            return
        bucket_time = (when // width) * width
        deltas[bucket_time] = deltas.get(bucket_time, 0.0) + delta

    for ex in executors:
        amount = _amount(ex)
        if amount <= 0:
            continue
        sign = 1.0 if str(getattr(ex, "side", "") or "").upper() == "BUY" else -1.0
        _add(_open_at(ex), sign * amount)
        _add(_close_at(ex), -sign * amount)

    return [
        {"time": t, "delta": round(deltas[t], 8)} for t in sorted(deltas) if deltas[t]
    ]


def _pnl_evolution(executors: list[Any], rate: float = 1.0) -> list[dict[str, Any]]:
    """Cumulative net PnL, gross trade PnL and fees in USD, ordered by close time.

    Only closed executors contribute: an open one has not realized anything yet,
    and dating it by its open stamp would step the curve up before the run
    earned it.
    """
    closed = sorted(
        (ex for ex in executors if _close_at(ex) > 0),
        key=_close_at,
    )
    if not closed:
        return []

    points: list[dict[str, Any]] = []
    cum_net = 0.0
    cum_fees = 0.0
    for ex in closed:
        cum_net += float(getattr(ex, "pnl", 0) or 0) * rate
        cum_fees += float(getattr(ex, "cum_fees_quote", 0) or 0) * rate
        points.append(
            {
                "time": _close_at(ex),
                "net_pnl": cum_net,
                # Net is already fee-inclusive, so adding fees back gives the
                # gross figure the chart plots against it.
                "trade_pnl": cum_net + cum_fees,
                "cum_fees": -cum_fees,
            }
        )

    if len(points) > _MAX_PNL_POINTS:
        step = len(points) // _MAX_PNL_POINTS
        thinned = points[::step]
        # The final point carries the run's actual total; never drop it.
        if thinned[-1] is not points[-1]:
            thinned.append(points[-1])
        points = thinned

    return points


def build_chart_series(
    executors: list[Any], rates: Any = None
) -> dict[str, dict[str, Any]]:
    """Aggregate every executor into per-market chart series.

    Keyed ``"{connector}:{trading_pair}"`` to match the pair selector, so the
    page can switch markets without refetching. Executors missing either half of
    the key are skipped -- they cannot be charted against a price series.

    ``rates`` is an optional :class:`condor.quote_conversion.QuoteRates`; when
    given, the money series (volume, PnL, fees) are restated in USD. Position
    deltas are base-asset amounts and the range is time, so neither is scaled.
    """
    groups: dict[str, list[Any]] = {}
    for ex in executors:
        connector = getattr(ex, "connector", "") or ""
        pair = getattr(ex, "trading_pair", "") or ""
        if not connector or not pair:
            continue
        groups.setdefault(f"{connector}:{pair}", []).append(ex)

    series: dict[str, dict[str, Any]] = {}
    for key, group in groups.items():
        start, end = activity_range(group)
        if start <= 0:
            continue
        interval = pick_interval(max(end - start, 0))
        rate = rates.for_pair(getattr(group[0], "trading_pair", "")) if rates else 1.0
        series[key] = {
            "interval": interval.name,
            "interval_sec": interval.seconds,
            "start": start,
            "end": end,
            "executor_count": len(group),
            "volume_buckets": _volume_buckets(group, interval.seconds, rate),
            "position_deltas": _position_deltas(group, interval.seconds),
            "pnl_evolution": _pnl_evolution(group, rate),
            "pool_address": _pool_address(group),
        }

    return series

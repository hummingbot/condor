"""Prefetch Hyperliquid historical closes for replay tick timestamps."""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from routines.macdbb_replay.models import TickMeta

logger = logging.getLogger(__name__)

HlPriceCache = dict[tuple[str, int], float]

_INTERVAL_MAX_DELTA_MS: dict[str, int] = {
    "1m": 45 * 60 * 1000,
    "5m": 20 * 60 * 1000,
    "15m": 45 * 60 * 1000,
    "1h": 90 * 60 * 1000,
    "4h": 5 * 60 * 60 * 1000,
}


@dataclass(frozen=True)
class HlPrefetchSettings:
    interval: str = "5m"
    buffer_hours: int = 1
    max_concurrent: int = 1
    request_interval_ms: int = 400
    max_retries: int = 6


def hl_prefetch_settings_from_config(config: object) -> HlPrefetchSettings:
    return HlPrefetchSettings(
        interval=getattr(config, "hl_price_interval", "5m"),
        max_concurrent=getattr(config, "hl_max_concurrent", 1),
        request_interval_ms=getattr(config, "hl_request_interval_ms", 400),
        max_retries=getattr(config, "hl_max_retries", 6),
    )


def _load_hl_candles():
    """Reload hl_candles so dev hot-reload picks up new exports."""
    import routines.lib.hl_candles as hl_candles_mod

    return importlib.reload(hl_candles_mod)


def _max_nearest_delta_ms(interval: str, interval_ms: int | None) -> int:
    if interval_ms is None:
        return 45 * 60 * 1000
    return _INTERVAL_MAX_DELTA_MS.get(interval, interval_ms * 3)


def _tick_pairs(meta: TickMeta) -> set[str]:
    return set(meta.macd_pairs) | set(meta.queue_total) | set(meta.signals_1h)


def _aggregate_pair_requests(
    session_tick_maps: dict[int, dict[int, TickMeta]],
) -> dict[str, list[tuple[int, int, dt.datetime]]]:
    """pair -> [(session_num, tick_num, tick_time), ...] across all sessions."""
    pair_requests: dict[str, list[tuple[int, int, dt.datetime]]] = {}
    for session_num, tick_meta_map in session_tick_maps.items():
        for tick_num, meta in tick_meta_map.items():
            for pair in _tick_pairs(meta):
                pair_requests.setdefault(pair, []).append(
                    (session_num, tick_num, meta.timestamp)
                )
    return pair_requests


def hl_cache_has_prices(
    tick_meta_map: dict[int, TickMeta],
    hl_price_cache: HlPriceCache | None,
) -> bool:
    if not hl_price_cache:
        return False
    for meta in tick_meta_map.values():
        for pair in _tick_pairs(meta):
            if hl_price_cache.get((pair, meta.tick), 0.0) > 0:
                return True
    return False


def _configure_hl_throttle(settings: HlPrefetchSettings) -> None:
    hl_candles = _load_hl_candles()
    hl_candles.configure_hl_rate_limit(
        request_interval_ms=settings.request_interval_ms,
        max_retries=settings.max_retries,
    )
    hl_candles.reset_hl_rate_limit_state()


async def prefetch_replay_hl_prices(
    session_tick_maps: dict[int, dict[int, TickMeta]],
    *,
    settings: HlPrefetchSettings | None = None,
) -> dict[int, HlPriceCache]:
    """Fetch each unique pair once and fan out prices to per-session caches."""
    if not session_tick_maps:
        return {}

    opts = settings or HlPrefetchSettings()
    _configure_hl_throttle(opts)

    hl_candles = _load_hl_candles()
    fetch_hl_candles_between = hl_candles.fetch_hl_candles_between
    hl_close_nearest = hl_candles.hl_close_nearest
    trading_pair_to_hl_coin = hl_candles.trading_pair_to_hl_coin
    interval_ms = hl_candles._INTERVAL_MS.get(opts.interval)
    max_delta_ms = _max_nearest_delta_ms(opts.interval, interval_ms)

    pair_requests = _aggregate_pair_requests(session_tick_maps)
    if not pair_requests:
        return {session_num: {} for session_num in session_tick_maps}

    session_caches: dict[int, HlPriceCache] = {
        session_num: {} for session_num in session_tick_maps
    }
    pair_candles: dict[str, list[dict[str, float]]] = {}
    semaphore = asyncio.Semaphore(max(1, opts.max_concurrent))
    pairs_sorted = sorted(pair_requests)

    async with aiohttp.ClientSession() as session:
        async def load_pair(pair: str) -> None:
            requests = pair_requests[pair]
            start = min(tick_time for _, _, tick_time in requests) - dt.timedelta(
                hours=opts.buffer_hours
            )
            end = max(tick_time for _, _, tick_time in requests) + dt.timedelta(
                hours=opts.buffer_hours
            )
            async with semaphore:
                try:
                    candles = await fetch_hl_candles_between(
                        pair,
                        opts.interval,
                        start,
                        end,
                        session=session,
                    )
                except Exception as error:
                    logger.warning(
                        "HL price prefetch failed for %s (%s): %s",
                        pair,
                        trading_pair_to_hl_coin(pair),
                        error,
                    )
                    return
            if not candles:
                logger.warning("HL price prefetch empty for %s", pair)
                return
            pair_candles[pair] = candles

        await asyncio.gather(
            *[load_pair(pair) for pair in pairs_sorted],
            return_exceptions=True,
        )

    for pair, requests in pair_requests.items():
        candles = pair_candles.get(pair)
        if not candles:
            continue
        for session_num, tick_num, tick_time in requests:
            close = hl_close_nearest(
                candles,
                tick_time,
                max_delta_ms=max_delta_ms,
            )
            if close and close > 0:
                session_caches[session_num][(pair, tick_num)] = close

    total_prices = sum(len(cache) for cache in session_caches.values())
    logger.info(
        "HL replay prefetch: %d prices across %d sessions (%d unique pairs)",
        total_prices,
        len(session_tick_maps),
        len(pair_requests),
    )
    return session_caches


async def prefetch_session_hl_prices(
    tick_meta_map: dict[int, TickMeta],
    *,
    interval: str = "5m",
    buffer_hours: int = 1,
    max_concurrent: int = 1,
    request_interval_ms: int = 400,
    max_retries: int = 6,
) -> HlPriceCache:
    """Prefetch prices for a single session (delegates to batched replay prefetch)."""
    settings = HlPrefetchSettings(
        interval=interval,
        buffer_hours=buffer_hours,
        max_concurrent=max_concurrent,
        request_interval_ms=request_interval_ms,
        max_retries=max_retries,
    )
    caches = await prefetch_replay_hl_prices({0: tick_meta_map}, settings=settings)
    return caches.get(0, {})

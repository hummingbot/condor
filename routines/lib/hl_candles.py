"""Hyperliquid REST candleSnapshot helpers (bypass hummingbot WS candle feeds)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_hl_rate_limit_lock: asyncio.Lock | None = None
_hl_rate_limit_interval_s: float = 0.0
_hl_last_request_at: float = 0.0
_hl_max_retries: int = 6


def configure_hl_rate_limit(
    *,
    request_interval_ms: int = 400,
    max_retries: int = 6,
) -> None:
    """Tune global HL REST throttling (used by replay prefetch)."""
    global _hl_rate_limit_interval_s, _hl_max_retries, _hl_rate_limit_lock
    _hl_rate_limit_interval_s = max(0.0, request_interval_ms / 1000.0)
    _hl_max_retries = max(1, max_retries)
    if _hl_rate_limit_lock is None:
        _hl_rate_limit_lock = asyncio.Lock()


def reset_hl_rate_limit_state() -> None:
    """Clear rate-limit timing state between replay runs."""
    global _hl_last_request_at
    _hl_last_request_at = 0.0


async def _await_hl_rate_limit() -> None:
    global _hl_last_request_at
    if _hl_rate_limit_interval_s <= 0:
        return
    if _hl_rate_limit_lock is None:
        configure_hl_rate_limit()
    async with _hl_rate_limit_lock:
        now = time.monotonic()
        wait_s = _hl_rate_limit_interval_s - (now - _hl_last_request_at)
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        _hl_last_request_at = time.monotonic()

HL_INFO_URL = "https://api.hyperliquid.xyz/info"

_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def trading_pair_to_hl_coin(trading_pair: str) -> str:
    """Map connector pair to HL coin (ZEC-USD -> ZEC, ABCD:FOO-USD -> ABCD:FOO)."""
    if "-" in trading_pair:
        return trading_pair.rsplit("-", 1)[0]
    return trading_pair


_MAX_CHUNK_BARS = 2000
_DEFAULT_MAX_NEAREST_MS = 45 * 60 * 1000


def _parse_hl_candle_snapshot(raw: list[Any]) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            candle: dict[str, float] = {
                "open": float(row["o"]),
                "high": float(row["h"]),
                "low": float(row["l"]),
                "close": float(row["c"]),
                "volume": float(row["v"]),
            }
            if "t" in row:
                candle["timestamp_ms"] = float(row["t"])
            candles.append(candle)
        except (KeyError, TypeError, ValueError):
            continue
    return candles


def hl_close_nearest(
    candles: list[dict[str, float]],
    target: dt.datetime,
    max_delta_ms: int = _DEFAULT_MAX_NEAREST_MS,
) -> float | None:
    """Return close price from the candle whose open time is nearest to target."""
    if not candles:
        return None
    target_ms = int(target.timestamp() * 1000)
    with_ts = [candle for candle in candles if "timestamp_ms" in candle]
    if not with_ts:
        return None
    best = min(with_ts, key=lambda candle: abs(candle["timestamp_ms"] - target_ms))
    if abs(best["timestamp_ms"] - target_ms) > max_delta_ms:
        return None
    return float(best["close"])


async def fetch_hl_candles(
    trading_pair: str,
    interval: str,
    max_records: int,
) -> list[dict[str, float]]:
    """Fetch OHLCV via HL candleSnapshot. Raises on failure."""
    interval_ms = _INTERVAL_MS.get(interval)
    if not interval_ms:
        raise ValueError(f"Unsupported HL candle interval: {interval}")

    coin = trading_pair_to_hl_coin(trading_pair)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - max_records * interval_ms
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(HL_INFO_URL, json=payload) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"HL candleSnapshot HTTP {resp.status} for {coin} {interval}"
                )
            data = await resp.json()

    if not isinstance(data, list) or not data:
        raise RuntimeError(f"HL candleSnapshot empty for {coin} {interval}")

    candles = _parse_hl_candle_snapshot(data)
    if len(candles) < max(30, max_records // 4):
        raise RuntimeError(
            f"HL candleSnapshot too few bars for {coin} {interval}: {len(candles)}"
        )
    return candles[-max_records:]


async def _fetch_hl_candle_chunk(
    session: aiohttp.ClientSession,
    coin: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, float]]:
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }
    last_error = ""
    for attempt in range(_hl_max_retries):
        await _await_hl_rate_limit()
        async with session.post(HL_INFO_URL, json=payload) as resp:
            if resp.status in {429, 500, 502, 503}:
                retry_after_header = resp.headers.get("Retry-After")
                if retry_after_header:
                    try:
                        backoff_s = float(retry_after_header)
                    except ValueError:
                        backoff_s = 2.0 ** attempt
                else:
                    backoff_s = min(30.0, 2.0 ** attempt)
                last_error = f"HTTP {resp.status}"
                logger.info(
                    "HL candleSnapshot %s for %s %s — retry in %.1fs (%d/%d)",
                    resp.status,
                    coin,
                    interval,
                    backoff_s,
                    attempt + 1,
                    _hl_max_retries,
                )
                await asyncio.sleep(backoff_s)
                continue
            if resp.status != 200:
                raise RuntimeError(
                    f"HL candleSnapshot HTTP {resp.status} for {coin} {interval}"
                )
            data = await resp.json()
        if not isinstance(data, list) or not data:
            return []
        return _parse_hl_candle_snapshot(data)

    raise RuntimeError(
        f"HL candleSnapshot {last_error or 'failed'} for {coin} {interval} "
        f"after {_hl_max_retries} retries"
    )


async def fetch_hl_candles_between(
    trading_pair: str,
    interval: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    session: aiohttp.ClientSession | None = None,
) -> list[dict[str, float]]:
    """Fetch OHLCV candles between start and end (UTC), chunked for long ranges."""
    interval_ms = _INTERVAL_MS.get(interval)
    if not interval_ms:
        raise ValueError(f"Unsupported HL candle interval: {interval}")

    coin = trading_pair_to_hl_coin(trading_pair)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    if end_ms <= start_ms:
        return []

    chunk_ms = _MAX_CHUNK_BARS * interval_ms
    all_candles: list[dict[str, float]] = []
    chunk_start = start_ms

    async def _load_chunks(http_session: aiohttp.ClientSession) -> list[dict[str, float]]:
        candles: list[dict[str, float]] = []
        cursor = chunk_start
        while cursor < end_ms:
            chunk_end = min(cursor + chunk_ms, end_ms)
            chunk = await _fetch_hl_candle_chunk(
                http_session,
                coin,
                interval,
                cursor,
                chunk_end,
            )
            candles.extend(chunk)
            cursor = chunk_end
        return candles

    if session is not None:
        all_candles = await _load_chunks(session)
    else:
        async with aiohttp.ClientSession() as http_session:
            all_candles = await _load_chunks(http_session)

    deduped: dict[int, dict[str, float]] = {}
    for candle in all_candles:
        timestamp_ms = candle.get("timestamp_ms")
        if timestamp_ms is None:
            continue
        deduped[int(timestamp_ms)] = candle
    return [deduped[key] for key in sorted(deduped)]


__all__ = [
    "HL_INFO_URL",
    "configure_hl_rate_limit",
    "fetch_hl_candles",
    "fetch_hl_candles_between",
    "hl_close_nearest",
    "reset_hl_rate_limit_state",
    "trading_pair_to_hl_coin",
]

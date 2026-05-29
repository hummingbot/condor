"""Hyperliquid REST candleSnapshot helpers (bypass hummingbot WS candle feeds)."""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

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


def _parse_hl_candle_snapshot(raw: list[Any]) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            candles.append(
                {
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": float(row["v"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return candles


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

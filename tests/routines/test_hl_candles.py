"""Tests for Hyperliquid candle helpers."""

from __future__ import annotations

import asyncio
import datetime as dt

from routines.lib import hl_candle_cache
from routines.lib.hl_candles import fetch_hl_candles_between, trading_pair_to_hl_coin


def test_trading_pair_to_hl_coin_pepe_alias():
    assert trading_pair_to_hl_coin("PEPE-USD") == "kPEPE"
    assert trading_pair_to_hl_coin("BTC-USD") == "BTC"
    assert trading_pair_to_hl_coin("ABCD:FOO-USD") == "ABCD:FOO"


def test_fetch_pepe_usd_candles_via_kpepe_alias():
    """PEPE-USD must resolve to kPEPE on HL (PEPE alone returns HTTP 500)."""
    start = dt.datetime(2026, 6, 8, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 6, 9, tzinfo=dt.timezone.utc)

    candles = asyncio.run(
        fetch_hl_candles_between("PEPE-USD", "5m", start, end),
    )
    assert len(candles) >= 100
    assert all(candle["close"] > 0 for candle in candles)


def test_fetch_pepe_usd_cached_roundtrip(tmp_path):
    start = dt.datetime(2026, 6, 8, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 6, 9, tzinfo=dt.timezone.utc)

    candles = asyncio.run(
        hl_candle_cache.fetch_hl_candles_between_cached(
            "PEPE-USD",
            "5m",
            start,
            end,
            cache_dir=tmp_path,
            use_cache=True,
            refresh_cache=True,
        )
    )
    assert len(candles) >= 100

    loaded = hl_candle_cache.load_candles("PEPE-USD", "5m", cache_dir=tmp_path)
    assert len(loaded) == len(candles)

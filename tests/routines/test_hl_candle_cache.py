"""Tests for Hyperliquid candle disk cache."""

from __future__ import annotations

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, patch

from routines.lib import hl_candle_cache


def _make_candle(timestamp_ms: int, close: float = 100.0) -> dict[str, float]:
    return {
        "timestamp_ms": float(timestamp_ms),
        "open": close - 1.0,
        "high": close + 1.0,
        "low": close - 2.0,
        "close": close,
        "volume": 10.0,
    }


def test_save_and_load_roundtrip(tmp_path):
    candles = [_make_candle(1_000_000, 101.0), _make_candle(1_300_000, 102.0)]
    hl_candle_cache.save_candles("BTC-USD", "5m", candles, cache_dir=tmp_path)

    loaded = hl_candle_cache.load_candles("BTC-USD", "5m", cache_dir=tmp_path)
    assert len(loaded) == 2
    assert loaded[0]["timestamp_ms"] == 1_000_000.0
    assert loaded[0]["close"] == 101.0
    assert loaded[1]["close"] == 102.0


def test_merge_extends_existing_range_without_duplicates(tmp_path):
    jan_start = int(dt.datetime(2026, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
    jan_mid = int(dt.datetime(2026, 1, 8, tzinfo=dt.UTC).timestamp() * 1000)
    feb_start = int(dt.datetime(2026, 2, 1, tzinfo=dt.UTC).timestamp() * 1000)

    hl_candle_cache.save_candles(
        "LIT-USD",
        "5m",
        [_make_candle(jan_start, 1.0), _make_candle(jan_mid, 2.0)],
        cache_dir=tmp_path,
    )
    hl_candle_cache.save_candles(
        "LIT-USD",
        "5m",
        [_make_candle(jan_mid, 2.5), _make_candle(feb_start, 3.0)],
        cache_dir=tmp_path,
    )

    loaded = hl_candle_cache.load_candles("LIT-USD", "5m", cache_dir=tmp_path)
    timestamps = [int(candle["timestamp_ms"]) for candle in loaded]
    assert timestamps == sorted(set(timestamps))
    assert jan_start in timestamps
    assert jan_mid in timestamps
    assert feb_start in timestamps
    assert loaded[1]["close"] == 2.5


def test_coverage_gaps_left_edge():
    interval_ms = 300_000
    start_ms = 1_000_000
    end_ms = 1_500_000
    cached = [_make_candle(1_200_000)]

    gaps = hl_candle_cache.coverage_gaps(cached, start_ms, end_ms, interval_ms)
    assert gaps == [(start_ms, 1_200_000)]


def test_coverage_gaps_right_edge():
    interval_ms = 300_000
    start_ms = 1_000_000
    end_ms = 2_000_000
    cached = [_make_candle(1_000_000), _make_candle(1_300_000)]

    gaps = hl_candle_cache.coverage_gaps(cached, start_ms, end_ms, interval_ms)
    assert gaps == [(1_600_000, end_ms)]


def test_coverage_gaps_interior_hole():
    interval_ms = 300_000
    start_ms = 1_000_000
    end_ms = 2_000_000
    cached = [
        _make_candle(1_000_000),
        _make_candle(1_300_000),
        _make_candle(1_900_000),
    ]

    gaps = hl_candle_cache.coverage_gaps(cached, start_ms, end_ms, interval_ms)
    assert (1_600_000, 1_900_000) in gaps


def test_coverage_gaps_full_coverage():
    interval_ms = 300_000
    start_ms = 1_000_000
    end_ms = 1_900_000
    cached = [
        _make_candle(1_000_000),
        _make_candle(1_300_000),
        _make_candle(1_600_000),
    ]

    gaps = hl_candle_cache.coverage_gaps(cached, start_ms, end_ms, interval_ms)
    assert gaps == []


def test_coverage_gaps_ignores_buffer_beyond_coverage_end():
    interval_ms = 300_000
    start_ms = 1_000_000
    end_ms = 2_000_000
    coverage_end_ms = 1_600_000
    cached = [
        _make_candle(1_000_000),
        _make_candle(1_300_000),
        _make_candle(1_600_000),
    ]

    gaps = hl_candle_cache.coverage_gaps(
        cached,
        start_ms,
        end_ms,
        interval_ms,
        coverage_end_ms=coverage_end_ms,
    )
    assert gaps == []


def test_fetch_cached_uses_disk_on_full_coverage(tmp_path):
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    end = dt.datetime(2026, 1, 1, 1, tzinfo=dt.UTC)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    interval_ms = 300_000
    cached = [
        _make_candle(start_ms + offset * interval_ms)
        for offset in range(((end_ms - start_ms) // interval_ms) + 1)
    ]
    hl_candle_cache.save_candles("BTC-USD", "5m", cached, cache_dir=tmp_path)

    fetch_mock = AsyncMock(return_value=[])
    with patch("routines.lib.hl_candle_cache.hl_candles.fetch_hl_candles_between", fetch_mock):
        result = asyncio.run(
            hl_candle_cache.fetch_hl_candles_between_cached(
                "BTC-USD",
                "5m",
                start,
                end,
                cache_dir=tmp_path,
            )
        )

    fetch_mock.assert_not_called()
    assert len(result) == len(cached)


def test_fetch_cached_fetches_only_gap_ranges(tmp_path):
    interval_ms = 300_000
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    end = dt.datetime(2026, 1, 1, 2, tzinfo=dt.UTC)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    hl_candle_cache.save_candles(
        "ETH-USD",
        "5m",
        [_make_candle(start_ms, 10.0), _make_candle(start_ms + interval_ms, 11.0)],
        cache_dir=tmp_path,
    )

    gap_start_ms = start_ms + (2 * interval_ms)
    fetched = [_make_candle(gap_start_ms, 12.0), _make_candle(end_ms, 13.0)]
    fetch_mock = AsyncMock(return_value=fetched)

    with patch("routines.lib.hl_candle_cache.hl_candles.fetch_hl_candles_between", fetch_mock):
        result = asyncio.run(
            hl_candle_cache.fetch_hl_candles_between_cached(
                "ETH-USD",
                "5m",
                start,
                end,
                cache_dir=tmp_path,
            )
        )

    assert fetch_mock.await_count == 1
    call_start = fetch_mock.await_args.args[2]
    call_end = fetch_mock.await_args.args[3]
    assert int(call_start.timestamp() * 1000) == gap_start_ms
    assert int(call_end.timestamp() * 1000) == end_ms
    assert len(result) == 4
    closes = sorted(candle["close"] for candle in result)
    assert closes == [10.0, 11.0, 12.0, 13.0]


def test_fetch_cached_refresh_ignores_disk(tmp_path):
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    end = dt.datetime(2026, 1, 1, 1, tzinfo=dt.UTC)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    hl_candle_cache.save_candles(
        "SOL-USD",
        "5m",
        [_make_candle(start_ms, 1.0)],
        cache_dir=tmp_path,
    )

    refreshed = [_make_candle(start_ms, 99.0), _make_candle(end_ms, 100.0)]
    fetch_mock = AsyncMock(return_value=refreshed)

    with patch("routines.lib.hl_candle_cache.hl_candles.fetch_hl_candles_between", fetch_mock):
        result = asyncio.run(
            hl_candle_cache.fetch_hl_candles_between_cached(
                "SOL-USD",
                "5m",
                start,
                end,
                cache_dir=tmp_path,
                refresh_cache=True,
            )
        )

    fetch_mock.assert_awaited_once()
    assert {candle["close"] for candle in result} == {99.0, 100.0}
    loaded = hl_candle_cache.load_candles("SOL-USD", "5m", cache_dir=tmp_path)
    assert {candle["close"] for candle in loaded} == {99.0, 100.0}

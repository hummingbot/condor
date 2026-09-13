"""The frame the fly sees: fixed size, right palette, no silent bad data."""

import hashlib

import numpy as np
import pytest

from condor.fly import chart


def _candles(n=72, start=100.0, step=0.5, up=True):
    rows = []
    price = start
    for i in range(n):
        nxt = price + step if up else price - step
        o, c = price, nxt
        rows.append(
            {
                "timestamp": i,
                "open": o,
                "high": max(o, c) + 0.2,
                "low": min(o, c) - 0.2,
                "close": c,
                "volume": 10 + i,
            }
        )
        price = nxt
    return rows


def test_frame_shape_dtype_and_background():
    frame = chart.market_frame("XYZ:DRAM-USD", _candles(), 135.0, 135.2)
    assert frame.shape == (chart.HEIGHT, chart.WIDTH, 3)
    assert frame.dtype == np.uint8
    assert tuple(frame[90, 2]) == chart.BACKGROUND  # left margin
    assert tuple(frame[5, 200]) == chart.HEADER


def test_up_candles_are_blue_and_down_candles_red():
    up = chart.market_frame("P:A-USD", _candles(up=True), 135.0, 135.2)
    down = chart.market_frame("P:A-USD", _candles(up=False), 64.0, 64.2)
    plot_up = up[chart.PLOT_TOP : chart.PLOT_BOTTOM, chart.PLOT_LEFT : chart.PLOT_RIGHT]
    plot_down = down[
        chart.PLOT_TOP : chart.PLOT_BOTTOM, chart.PLOT_LEFT : chart.PLOT_RIGHT
    ]
    assert (plot_up == chart.UP).all(axis=2).any()
    assert not (plot_up == chart.DOWN).all(axis=2).any()
    assert (plot_down == chart.DOWN).all(axis=2).any()
    assert not (plot_down == chart.UP).all(axis=2).any()


def test_bid_ask_ticks_at_right_edge():
    rows = _candles()
    frame = chart.market_frame("P:A-USD", rows, 120.0, 130.0)
    lo, span = chart.price_scale(chart.normalize_candles(rows))
    for price in (120.0, 130.0):
        y = int(chart._y(price, lo, span))
        strip = frame[y - 1 : y + 2, chart.PLOT_RIGHT + 3 : chart.WIDTH - 2]
        assert (strip == chart.TICK).all(axis=2).any()


def test_volume_strip_is_drawn():
    frame = chart.market_frame("P:A-USD", _candles(), 135.0, 135.2)
    strip = frame[
        chart.VOLUME_TOP : chart.VOLUME_BOTTOM + 1, chart.PLOT_LEFT : chart.PLOT_RIGHT
    ]
    assert (strip == chart.VOLUME).all(axis=2).any()


def test_deterministic():
    a = chart.market_frame("P:A-USD", _candles(), 135.0, 135.2)
    b = chart.market_frame("P:A-USD", _candles(), 135.0, 135.2)
    assert (
        hashlib.sha256(a.tobytes()).hexdigest()
        == hashlib.sha256(b.tobytes()).hexdigest()
    )


def test_flat_market_scale_floor():
    rows = [
        {"open": 100, "high": 100, "low": 100, "close": 100, "volume": 1}
        for _ in range(10)
    ]
    lo, span = chart.price_scale(chart.normalize_candles(rows))
    assert span == pytest.approx(100 * 0.002 * 1.24)
    assert lo == pytest.approx(100 - 100 * 0.002 * 0.12)


def test_keeps_only_last_n_and_right_aligns():
    rows = _candles(n=200)
    kept = chart.normalize_candles(rows, 72)
    assert len(kept) == 72 and kept[-1]["close"] == rows[-1]["close"]
    few = chart.market_frame("P:A-USD", _candles(n=5), 102.0, 102.2)
    plot = few[chart.PLOT_TOP : chart.PLOT_BOTTOM]
    colored = (plot == chart.UP).all(axis=2).any(axis=0)
    assert colored[: chart.PLOT_LEFT + 200].sum() == 0  # nothing on the left
    assert colored[chart.PLOT_RIGHT - 30 : chart.PLOT_RIGHT].any()


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [{"open": 1, "high": 2, "low": 0.5}],  # missing close/volume
        [{"open": 1, "high": 0.9, "low": 0.5, "close": 1, "volume": 1}],  # high < open
        [{"open": 0, "high": 1, "low": 0, "close": 1, "volume": 1}],  # zero price
        [{"open": float("nan"), "high": 1, "low": 0, "close": 1, "volume": 1}],
    ],
)
def test_bad_candles_raise(bad):
    with pytest.raises(ValueError):
        chart.market_frame("P:A-USD", bad, 1.0, 1.1)


def test_bad_quotes_raise():
    with pytest.raises(ValueError):
        chart.market_frame("P:A-USD", _candles(), 0, 1)
    with pytest.raises(ValueError):
        chart.market_frame("P:A-USD", _candles(), 2.0, 1.0)

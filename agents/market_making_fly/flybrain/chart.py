"""Render OHLCV candles into the 320×180 RGB frame the fly looks at.

Same canvas, light background and palette as stonkfly's ``display.market_frame``
so the vendored retinal projection is unchanged: R1–R6 cells read luminance,
the mapped R8p cells read the blue channel and R8y the green channel. Up
candles are blue and down candles red for that reason — colour is the only
chromatic input the fly gets, not decoration.

The frame never contains the bot's own quotes, inventory or P&L. Portfolio
value reaches the fly only through the dopamine pulse (``reinforcement.py``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

WIDTH, HEIGHT = 320, 180
N_CANDLES = 72

BACKGROUND = (235, 240, 249)
HEADER = (19, 36, 71)
HEADER_TEXT = (219, 229, 249)
GRID = (200, 212, 233)
UP = (0, 101, 183)
DOWN = (197, 37, 78)
VOLUME = (120, 140, 180)
TEXT = (28, 46, 82)
TICK = (27, 39, 81)

PLOT_LEFT, PLOT_RIGHT = 12, 306
PLOT_TOP, PLOT_BOTTOM = 34, 132
VOLUME_TOP, VOLUME_BOTTOM = 138, 158
FOOTER_Y = 165

_FIELDS = ("open", "high", "low", "close", "volume")


def _finite_positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")
    return number


def normalize_candles(candles: Sequence[Any], n_candles: int = N_CANDLES) -> list[dict]:
    """Keep the last ``n_candles`` and validate every field; raise on bad data."""
    if n_candles < 2:
        raise ValueError("n_candles must be at least 2")
    if not candles:
        raise ValueError("No candles to render")
    rows = []
    for raw in list(candles)[-n_candles:]:
        if not isinstance(raw, dict):
            raise ValueError(f"Candle must be a dict, got {type(raw).__name__}")
        row = {}
        for field in _FIELDS:
            if field not in raw:
                raise ValueError(f"Candle is missing {field!r}")
            value = float(raw[field])
            if not math.isfinite(value) or value < 0:
                raise ValueError(
                    f"Candle {field} must be finite and >= 0, got {value!r}"
                )
            row[field] = value
        for field in ("open", "high", "low", "close"):
            if row[field] <= 0:
                raise ValueError(f"Candle {field} must be > 0")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(
            row["open"], row["close"]
        ):
            raise ValueError("Candle high/low do not contain open/close")
        rows.append(row)
    return rows


def price_scale(rows: Sequence[dict]) -> tuple[float, float]:
    """``(lo, span)`` for the price axis: window padded 12 % each side, with a
    floor of 0.2 % of the mean price so a flat market is not blown up to
    full scale (stonkfly's rule)."""
    lows = np.asarray([r["low"] for r in rows], dtype=float)
    highs = np.asarray([r["high"] for r in rows], dtype=float)
    closes = np.asarray([r["close"] for r in rows], dtype=float)
    span = max(float(highs.max() - lows.min()), float(closes.mean()) * 0.002)
    lo = float(lows.min()) - span * 0.12
    return lo, span * 1.24


def _y(value: float, lo: float, span: float) -> float:
    return PLOT_BOTTOM - (value - lo) / span * (PLOT_BOTTOM - PLOT_TOP)


def market_frame(
    pair: str,
    candles: Sequence[Any],
    bid: float,
    ask: float,
    n_candles: int = N_CANDLES,
) -> np.ndarray:
    """OHLCV chart → ``np.uint8[HEIGHT, WIDTH, 3]``."""
    bid = _finite_positive(bid, "bid")
    ask = _finite_positive(ask, "ask")
    if ask < bid:
        raise ValueError(f"ask {ask} below bid {bid}")
    rows = normalize_candles(candles, n_candles)

    im = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, WIDTH - 1, 27), fill=HEADER)
    d.text((9, 8), str(pair), fill=HEADER_TEXT)
    for x in range(PLOT_LEFT, PLOT_RIGHT + 1, 30):
        d.line((x, PLOT_TOP, x, VOLUME_BOTTOM), fill=GRID)
    for y in range(PLOT_TOP + 4, PLOT_BOTTOM, 24):
        d.line((PLOT_LEFT - 2, y, PLOT_RIGHT + 2, y), fill=GRID)

    lo, span = price_scale(rows)
    slot = (PLOT_RIGHT - PLOT_LEFT) / n_candles
    body_w = max(1, int(slot) - 1)
    max_volume = max(r["volume"] for r in rows)
    # Right-align so the newest candle always sits at the right edge, where the
    # bid/ask ticks are, regardless of how many candles the feed returned.
    offset = n_candles - len(rows)
    for i, r in enumerate(rows):
        x0 = PLOT_LEFT + (offset + i) * slot
        xc = int(x0 + slot / 2)
        color = UP if r["close"] >= r["open"] else DOWN
        y_high, y_low = _y(r["high"], lo, span), _y(r["low"], lo, span)
        d.line((xc, y_high, xc, y_low), fill=color, width=1)
        y_open, y_close = _y(r["open"], lo, span), _y(r["close"], lo, span)
        top, bottom = min(y_open, y_close), max(y_open, y_close)
        if bottom - top < 1:
            bottom = top + 1
        d.rectangle((int(x0), top, int(x0) + body_w, bottom), fill=color)
        if max_volume > 0:
            h = r["volume"] / max_volume * (VOLUME_BOTTOM - VOLUME_TOP)
            d.rectangle(
                (int(x0), VOLUME_BOTTOM - h, int(x0) + body_w, VOLUME_BOTTOM),
                fill=VOLUME,
            )

    for price in (bid, ask):
        y = _y(price, lo, span)
        if PLOT_TOP <= y <= PLOT_BOTTOM:
            d.line((PLOT_RIGHT + 2, y, WIDTH - 2, y), fill=TICK, width=2)
    d.text((9, FOOTER_Y), f"BID {bid:g}  ASK {ask:g}"[:50], fill=TEXT)
    return np.asarray(im, dtype=np.uint8)

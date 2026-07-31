"""Bollinger Band state across timeframes: squeeze, expansion, band walk, or reversion.

The primary read for the Bollinger Band Trader. For each of three timeframes it
computes BB(period, std), %B, bandwidth, the percentile rank of that bandwidth within
its own history (the squeeze detector), and a Keltner-containment check (the classic
TTM squeeze). It then classifies each timeframe and combines them into ONE setup with
concrete entry / stop / target levels derived from the bands themselves.

The classification exists because a band touch is ambiguous on its own: tagging the
upper band is a fade in a flat range and a buy signal out of a squeeze. Everything here
is built to tell those two apart before any level is quoted.
"""

import logging

from pydantic import BaseModel, ConfigDict, Field
from telegram.ext import ContextTypes

from condor.reports.footprint import candle_timestamps
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

# (label, interval, candles to request) — entry / context / trend
_TIMEFRAMES = [
    ("entry_15m", "15m", 300),
    ("context_1h", "1h", 300),
    ("trend_4h", "4h", 300),
]

_KELTNER_MULT = 1.5
_EXPANSION_LOOKBACK = 3  # candles back used to detect bandwidth expansion
_EXPANSION_FACTOR = 1.15  # bandwidth must grow this much to count as expanding
_RANK_LOOKBACK = 200  # bandwidth history depth for the percentile rank
_TREND_ADX = 25.0  # above this the market trends (walk); below it reverts
_WALK_PCT_B = 0.90  # a close in the top/bottom decile of the band counts toward a walk

_CONTINUATION_SETUPS = {
    "squeeze_breakout_long",
    "squeeze_breakout_short",
    "band_walk_long",
    "band_walk_short",
    "failed_breakout_long",
    "failed_breakout_short",
}


class Config(BaseModel):
    """Classify Bollinger Band state and derive entry/stop/target for one pair."""

    # Reject unknown keys. Without this, a model passing `symbol` instead of
    # `trading_pair` would silently fall back to the default pair/connector and
    # return a confident answer about the wrong market.
    model_config = ConfigDict(extra="forbid")

    trading_pair: str = Field(default="BTC-USDT", description="Trading pair to analyze")
    connector_name: str = Field(
        default="binance_perpetual", description="Exchange connector"
    )
    bb_period: int = Field(default=20, description="Bollinger moving-average period")
    bb_std: float = Field(
        default=2.0, description="Bollinger standard-deviation multiplier"
    )
    squeeze_rank: float = Field(
        default=20.0, description="bw_rank at or below which the band is squeezed"
    )
    walk_window: int = Field(default=6, description="Candles inspected for a band walk")
    walk_min_closes: int = Field(
        default=3, description="Closes outside the band needed to call a walk"
    )
    min_rr_continuation: float = Field(
        default=1.5, description="Minimum reward:risk for breakout / walk setups"
    )
    min_rr_reversion: float = Field(
        default=1.2, description="Minimum reward:risk for reversion setups"
    )


# ── Series helpers ───────────────────────────────────────────────────────────


def _sma_series(values: list, period: int) -> list:
    if period <= 0:
        return [None] * len(values)
    out, running = [], 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        out.append(running / period if i >= period - 1 else None)
    return out


def _ema_series(values: list, period: int) -> list:
    if len(values) < period or period <= 0:
        return [None] * len(values)
    mult = 2 / (period + 1)
    ema = sum(values[:period]) / period
    out = [None] * (period - 1) + [ema]
    for v in values[period:]:
        ema = (v - ema) * mult + ema
        out.append(ema)
    return out


def _stdev(window: list) -> float:
    n = len(window)
    if n < 2:
        return 0.0
    mean = sum(window) / n
    return (sum((x - mean) ** 2 for x in window) / n) ** 0.5


def _bollinger_series(closes: list, period: int, k: float) -> list:
    """Per-index {mid, upper, lower, bandwidth_pct, pct_b}, None until the window fills."""
    mids = _sma_series(closes, period)
    out = []
    for i, mid in enumerate(mids):
        if mid is None or mid <= 0:
            out.append(None)
            continue
        std = _stdev(closes[i - period + 1 : i + 1])
        upper, lower = mid + k * std, mid - k * std
        width = upper - lower
        out.append(
            {
                "mid": mid,
                "upper": upper,
                "lower": lower,
                "bandwidth_pct": width / mid * 100,
                "pct_b": (closes[i] - lower) / width if width > 0 else 0.5,
            }
        )
    return out


def _true_range_series(candles: list) -> list:
    trs = []
    for i, c in enumerate(candles):
        high, low = float(c.get("high", 0)), float(c.get("low", 0))
        if i == 0:
            trs.append(high - low)
            continue
        prev_close = float(candles[i - 1].get("close", 0))
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return trs


def _keltner_series(closes: list, atrs: list, period: int, mult: float) -> list:
    emas = _ema_series(closes, period)
    out = []
    for ema, atr in zip(emas, atrs):
        if ema is None or atr is None:
            out.append(None)
        else:
            out.append({"upper": ema + mult * atr, "lower": ema - mult * atr})
    return out


def _percentile_rank(history: list, value: float) -> float:
    """Where `value` sits inside `history`, 0–100. 0 = tightest ever seen."""
    if not history:
        return 50.0
    below = sum(1 for h in history if h < value)
    ties = sum(1 for h in history if h == value)
    return (below + 0.5 * ties) / len(history) * 100


def _calc_adx(candles: list, period: int = 14) -> float:
    """Wilder's ADX — the DX smoothed over `period`, not the raw single-window DX.

    The smoothing is what makes the textbook 20 / 25 thresholds mean anything. Raw DX
    pins near 100 on any sustained directional push, and price reaching a Bollinger band
    is *always* a directional push — so with raw DX the `reversion_range` verdict is
    unreachable in practice and every band tag reads as a trend.
    """
    if len(candles) < period * 2 + 1:
        return 0.0

    plus_dms, minus_dms, trs = [], [], []
    for i in range(1, len(candles)):
        high, low = float(candles[i].get("high", 0)), float(candles[i].get("low", 0))
        prev_high = float(candles[i - 1].get("high", 0))
        prev_low = float(candles[i - 1].get("low", 0))
        prev_close = float(candles[i - 1].get("close", 0))
        up_move, down_move = high - prev_high, prev_low - low
        plus_dms.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dms.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    if len(trs) < period * 2:
        return 0.0

    # Wilder smoothing: seed with the first `period` sum, then decay one period's worth.
    smooth_tr = sum(trs[:period])
    smooth_plus = sum(plus_dms[:period])
    smooth_minus = sum(minus_dms[:period])

    dxs = []
    for i in range(period, len(trs)):
        smooth_tr = smooth_tr - smooth_tr / period + trs[i]
        smooth_plus = smooth_plus - smooth_plus / period + plus_dms[i]
        smooth_minus = smooth_minus - smooth_minus / period + minus_dms[i]
        if smooth_tr <= 0:
            continue
        plus_di = smooth_plus / smooth_tr * 100
        minus_di = smooth_minus / smooth_tr * 100
        di_sum = plus_di + minus_di
        dxs.append(abs(plus_di - minus_di) / di_sum * 100 if di_sum else 0.0)

    if len(dxs) < period:
        return sum(dxs) / len(dxs) if dxs else 0.0

    adx = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx = (adx * (period - 1) + dx) / period
    return adx


def _fmt(value) -> str:
    """Price formatting that survives both BTC and 8-decimal micro-caps."""
    if value is None:
        return "n/a"
    av = abs(value)
    if av >= 1000:
        return f"{value:,.2f}"
    if av >= 1:
        return f"{value:,.4f}"
    if av >= 0.01:
        return f"{value:.6f}"
    return f"{value:.8f}"


# ── Classification ───────────────────────────────────────────────────────────


def _band_walk(bbs: list, window: int, min_closes: int) -> tuple:
    """Closes clustered in the top/bottom decile of the band — the trend-riding tell."""
    recent = [b for b in bbs[-window:] if b]
    up = sum(1 for b in recent if b["pct_b"] >= _WALK_PCT_B)
    down = sum(1 for b in recent if b["pct_b"] <= 1 - _WALK_PCT_B)
    return up >= min_closes, down >= min_closes


def _failed_breakout(bbs: list) -> str:
    """Closed back inside the band after breaking out — the W/M reversal tell."""
    recent = [b for b in bbs[-3:] if b]
    if len(recent) < 3:
        return ""
    if not 0.0 <= recent[-1]["pct_b"] <= 1.0:
        return ""
    if any(b["pct_b"] > 1.0 for b in recent[:-1]):
        return "bearish"
    if any(b["pct_b"] < 0.0 for b in recent[:-1]):
        return "bullish"
    return ""


def _verdict(
    bb,
    bb_prev,
    bw_rank: float,
    squeeze_on: bool,
    walk_up: bool,
    walk_down: bool,
    adx: float,
    squeeze_rank: float,
) -> str:
    expanding = (
        bool(bb_prev)
        and bb["bandwidth_pct"] > bb_prev["bandwidth_pct"] * _EXPANSION_FACTOR
    )

    # Expansion is checked BEFORE the squeeze: a coil that is firing right now is no
    # longer a coil, and its bandwidth rank is still low on the candle that breaks.
    # Ordering this the other way round reports `squeeze_pending` on the exact bar the
    # trade should be taken.
    if expanding and bb["pct_b"] > 1.0:
        return "expansion_up"
    if expanding and bb["pct_b"] < 0.0:
        return "expansion_down"
    if squeeze_on or bw_rank <= squeeze_rank:
        return "squeeze"
    if walk_up and adx > _TREND_ADX:
        return "band_walk_up"
    if walk_down and adx > _TREND_ADX:
        return "band_walk_down"
    # Reversion needs a wide band that is NOT growing, in a non-trending market.
    if bw_rank >= 40 and not expanding and adx < _TREND_ADX:
        return "reversion_range"
    return "neutral"


def _analyze_timeframe(candles: list, label: str, cfg: Config) -> dict:
    """Full band read for one timeframe. Returns {'error': ...} when data is too thin."""
    if not candles or len(candles) < cfg.bb_period + _EXPANSION_LOOKBACK + 1:
        return {"timeframe": label, "error": "insufficient candles"}

    closes = [float(c.get("close", 0)) for c in candles]
    volumes = [float(c.get("volume", 0)) for c in candles]

    bbs = _bollinger_series(closes, cfg.bb_period, cfg.bb_std)
    bb = bbs[-1]
    if bb is None:
        return {"timeframe": label, "error": "band not formed"}
    bb_prev = bbs[-1 - _EXPANSION_LOOKBACK] if len(bbs) > _EXPANSION_LOOKBACK else None

    history = [b["bandwidth_pct"] for b in bbs[:-1] if b][-_RANK_LOOKBACK:]
    bw_rank = _percentile_rank(history, bb["bandwidth_pct"])

    atrs = _sma_series(_true_range_series(candles), cfg.bb_period)
    kcs = _keltner_series(closes, atrs, cfg.bb_period, _KELTNER_MULT)
    kc = kcs[-1]
    squeeze_on = bool(kc) and bb["upper"] < kc["upper"] and bb["lower"] > kc["lower"]

    walk_up, walk_down = _band_walk(bbs, cfg.walk_window, cfg.walk_min_closes)
    adx = _calc_adx(candles)

    avg_vol = sum(volumes[-cfg.bb_period :]) / cfg.bb_period if volumes else 0.0
    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

    window = candles[-3:]
    return {
        "timeframe": label,
        "verdict": _verdict(
            bb, bb_prev, bw_rank, squeeze_on, walk_up, walk_down, adx, cfg.squeeze_rank
        ),
        "price": closes[-1],
        "upper": bb["upper"],
        "mid": bb["mid"],
        "lower": bb["lower"],
        "pct_b": bb["pct_b"],
        "bandwidth_pct": bb["bandwidth_pct"],
        "bw_rank": bw_rank,
        "squeeze_on": squeeze_on,
        "walk_up": walk_up,
        "walk_down": walk_down,
        "failed_breakout": _failed_breakout(bbs),
        "adx": adx,
        "vol_ratio": vol_ratio,
        "atr": atrs[-1] or 0.0,
        "recent_low": min(float(c.get("low", 0)) for c in window),
        "recent_high": max(float(c.get("high", 0)) for c in window),
        "bb_series": bbs,
        "candles": candles,
    }


# ── Setup derivation ─────────────────────────────────────────────────────────


def _no_trade(reason: str) -> dict:
    return {"setup": "no_trade", "bias": "none", "reason": reason}


def _derive_setup(entry: dict, trend: dict, cfg: Config) -> dict:
    """Combine the entry timeframe verdict with the trend timeframe veto into one setup."""
    if "error" in entry:
        return _no_trade(f"entry timeframe unavailable: {entry['error']}")

    verdict = entry["verdict"]
    trend_verdict = trend.get("verdict", "neutral")
    price, mid, upper, lower = (
        entry["price"],
        entry["mid"],
        entry["upper"],
        entry["lower"],
    )
    atr = entry["atr"] or (upper - lower) / 4

    # 1. Failed breakout — highest-quality signal, it overrides the rest.
    if entry["failed_breakout"] == "bullish" and trend_verdict != "band_walk_down":
        setup = {
            "setup": "failed_breakout_long",
            "bias": "long",
            "entry": price,
            "stop": entry["recent_low"] - 0.25 * atr,
            "target": mid + (mid - lower) * 0.5,
            "reason": "closed back inside the band after breaking below it — no follow-through on the break",
        }
    elif entry["failed_breakout"] == "bearish" and trend_verdict != "band_walk_up":
        setup = {
            "setup": "failed_breakout_short",
            "bias": "short",
            "entry": price,
            "stop": entry["recent_high"] + 0.25 * atr,
            "target": mid - (upper - mid) * 0.5,
            "reason": "closed back inside the band after breaking above it — no follow-through on the break",
        }

    # 2. Squeeze — coiled, direction unknown. Report the triggers, do not pre-position.
    elif verdict == "squeeze":
        return {
            "setup": "squeeze_pending",
            "bias": "none",
            "long_trigger": upper,
            "short_trigger": lower,
            "invalidation": mid,
            "reason": f"bandwidth at rank {entry['bw_rank']:.0f} (squeeze_on={entry['squeeze_on']}) — wait for a close outside the band",
        }

    # 3. Squeeze fired — expansion with price outside the band.
    elif verdict == "expansion_up" and trend_verdict not in (
        "expansion_down",
        "band_walk_down",
    ):
        stop = mid
        setup = {
            "setup": "squeeze_breakout_long",
            "bias": "long",
            "entry": price,
            "stop": stop,
            "target": price + 2 * (price - stop),
            "reason": "bandwidth expanding with a close above the upper band",
        }
    elif verdict == "expansion_down" and trend_verdict not in (
        "expansion_up",
        "band_walk_up",
    ):
        stop = mid
        setup = {
            "setup": "squeeze_breakout_short",
            "bias": "short",
            "entry": price,
            "stop": stop,
            "target": price - 2 * (stop - price),
            "reason": "bandwidth expanding with a close below the lower band",
        }

    # 4. Band walk — enter on the pullback to the mean, never fade it.
    elif verdict == "band_walk_up":
        setup = {
            "setup": "band_walk_long",
            "bias": "long",
            "entry": mid,
            "stop": mid - 1.0 * atr,
            "target": upper,
            "reason": "price walking the upper band — buy the pullback to the middle band",
        }
    elif verdict == "band_walk_down":
        setup = {
            "setup": "band_walk_short",
            "bias": "short",
            "entry": mid,
            "stop": mid + 1.0 * atr,
            "target": lower,
            "reason": "price walking the lower band — sell the pullback to the middle band",
        }

    # 5. Range reversion — fade the band touch back to the mean.
    elif verdict == "reversion_range" and entry["pct_b"] <= 0.05:
        if trend_verdict in ("band_walk_down", "expansion_down"):
            return _no_trade(
                f"lower-band touch but {trend['timeframe']} is {trend_verdict} — do not fade a downtrend"
            )
        setup = {
            "setup": "reversion_long",
            "bias": "long",
            "entry": lower,
            "stop": lower - 0.5 * atr,
            "target": mid,
            "reason": "lower-band tag in a flat, wide band with ADX below the trend threshold",
        }
    elif verdict == "reversion_range" and entry["pct_b"] >= 0.95:
        if trend_verdict in ("band_walk_up", "expansion_up"):
            return _no_trade(
                f"upper-band touch but {trend['timeframe']} is {trend_verdict} — do not fade an uptrend"
            )
        setup = {
            "setup": "reversion_short",
            "bias": "short",
            "entry": upper,
            "stop": upper + 0.5 * atr,
            "target": mid,
            "reason": "upper-band tag in a flat, wide band with ADX below the trend threshold",
        }
    else:
        return _no_trade(
            f"{verdict} on {entry['timeframe']} with %B at {entry['pct_b']:.2f} — no edge"
        )

    risk = abs(setup["entry"] - setup["stop"])
    reward = abs(setup["target"] - setup["entry"])
    if risk <= 0:
        return _no_trade("band-derived stop collapsed onto the entry")
    setup["rr"] = reward / risk

    is_continuation = setup["setup"] in _CONTINUATION_SETUPS
    min_rr = cfg.min_rr_continuation if is_continuation else cfg.min_rr_reversion
    if setup["rr"] < min_rr:
        return _no_trade(
            f"{setup['setup']} rejected: R:R {setup['rr']:.2f} below the {min_rr:.1f} minimum"
        )

    # Breakouts need participation. A quiet break is a retest waiting to happen.
    if setup["setup"].startswith("squeeze_breakout") and entry["vol_ratio"] < 1.0:
        return _no_trade(
            f"{setup['setup']} rejected: breakout volume {entry['vol_ratio']:.2f}x average — wait for the retest"
        )

    return setup


# ── Report ───────────────────────────────────────────────────────────────────


def _band_chart(tf: dict, pair: str):
    import plotly.graph_objects as go

    candles = tf["candles"][-120:]
    bbs = tf["bb_series"][-120:]
    ts = candle_timestamps(candles)
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=ts,
            open=[float(c.get("open", 0)) for c in candles],
            high=[float(c.get("high", 0)) for c in candles],
            low=[float(c.get("low", 0)) for c in candles],
            close=[float(c.get("close", 0)) for c in candles],
            name=tf["timeframe"],
            increasing_line_color="#22c55e",
            decreasing_line_color="#ef4444",
        )
    )
    for key, color, dash, name in [
        ("upper", "#60a5fa", "solid", "Upper band"),
        ("mid", "#f59e0b", "dot", "Middle band (SMA)"),
        ("lower", "#60a5fa", "solid", "Lower band"),
    ]:
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=[b[key] if b else None for b in bbs],
                mode="lines",
                name=name,
                line=dict(color=color, width=1.4, dash=dash),
            )
        )
    fig.update_layout(
        title=f"Bollinger Bands ({tf['timeframe']}) — {pair}",
        xaxis_title="Time",
        yaxis_title="Price",
        template="plotly_dark",
        height=460,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )
    return fig


def _bandwidth_chart(tf: dict, pair: str, squeeze_rank: float):
    import plotly.graph_objects as go

    bbs = [b for b in tf["bb_series"] if b][-_RANK_LOOKBACK:]
    if len(bbs) < 5:
        return None
    widths = [b["bandwidth_pct"] for b in bbs]
    ordered = sorted(widths)
    threshold = ordered[
        max(0, min(len(ordered) - 1, int(len(ordered) * squeeze_rank / 100)))
    ]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=list(range(len(widths))),
            y=widths,
            mode="lines",
            name="Bandwidth %",
            line=dict(color="#a78bfa", width=1.6),
        )
    )
    fig.add_hline(
        y=threshold,
        line_dash="dot",
        line_color="#f43f5e",
        annotation_text=f"squeeze threshold (p{squeeze_rank:.0f})",
    )
    fig.update_layout(
        title=f"Bandwidth History ({tf['timeframe']}) — {pair}",
        xaxis_title=f"Candles ago → now (last {len(widths)})",
        yaxis_title="Bandwidth (% of middle band)",
        template="plotly_dark",
        height=320,
        legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="center", x=0.5),
    )
    return fig


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    pair, connector = config.trading_pair, config.connector_name

    import asyncio

    async def fetch(interval: str, records: int):
        try:
            raw = await client.market_data.get_candles(
                connector, pair, interval=interval, max_records=records
            )
        except Exception as e:
            logger.warning(f"Candle fetch failed for {pair} {interval}: {e}")
            return []
        if isinstance(raw, list):
            return raw
        return raw.get("data", raw.get("candles", [])) if isinstance(raw, dict) else []

    candle_sets = await asyncio.gather(*[fetch(iv, n) for _, iv, n in _TIMEFRAMES])
    if not any(candle_sets):
        return f"No candle data for {pair} on {connector}"

    frames = [
        _analyze_timeframe(candles, label, config)
        for (label, _, _), candles in zip(_TIMEFRAMES, candle_sets)
    ]
    entry, contextual, trend = frames
    setup = _derive_setup(entry, trend, config)

    rows = []
    for tf in frames:
        if "error" in tf:
            rows.append({"Timeframe": tf["timeframe"], "Verdict": f"— ({tf['error']})"})
            continue
        rows.append(
            {
                "Timeframe": tf["timeframe"],
                "Verdict": tf["verdict"],
                "Price": _fmt(tf["price"]),
                "%B": f"{tf['pct_b']:.2f}",
                "Bandwidth": f"{tf['bandwidth_pct']:.2f}%",
                "BW Rank": f"{tf['bw_rank']:.0f}",
                "Squeeze": "yes" if tf["squeeze_on"] else "no",
                "ADX": f"{tf['adx']:.1f}",
                "Vol x": f"{tf['vol_ratio']:.2f}",
                "Upper": _fmt(tf["upper"]),
                "Middle": _fmt(tf["mid"]),
                "Lower": _fmt(tf["lower"]),
            }
        )

    lines = [
        f"**Bollinger Band State: {pair} on {connector}** — BB({config.bb_period}, {config.bb_std})",
        "",
        f"setup: {setup['setup']}",
        f"bias: {setup['bias']}",
    ]
    if setup["setup"] == "squeeze_pending":
        lines += [
            f"long_trigger: {_fmt(setup['long_trigger'])}",
            f"short_trigger: {_fmt(setup['short_trigger'])}",
            f"invalidation: {_fmt(setup['invalidation'])}",
        ]
    elif setup["setup"] != "no_trade":
        lines += [
            f"entry: {_fmt(setup['entry'])}",
            f"stop: {_fmt(setup['stop'])}",
            f"target: {_fmt(setup['target'])}",
            f"rr: {setup['rr']:.2f}",
        ]
    lines.append(f"reason: {setup['reason']}")

    if "error" not in entry:
        lines += [
            "",
            f"entry_tf: {entry['verdict']} | %B {entry['pct_b']:.2f} | bw {entry['bandwidth_pct']:.2f}% (rank {entry['bw_rank']:.0f}) | ADX {entry['adx']:.1f}",
        ]
    if "error" not in contextual:
        lines.append(
            f"context_tf: {contextual['verdict']} | %B {contextual['pct_b']:.2f} | bw rank {contextual['bw_rank']:.0f}"
        )
    if "error" not in trend:
        lines.append(
            f"trend_tf: {trend['verdict']} | %B {trend['pct_b']:.2f} | bw rank {trend['bw_rank']:.0f}"
        )

    summary = "\n".join(lines)

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Bollinger Band State: {pair}")
        builder.source("routine", "band_state").tags(
            ["bollinger", "volatility", pair.lower()]
        )
        builder.manual_order()

        builder.section(
            "01 / SETUP", "What the bands say to do right now, and at which price."
        )
        builder.kpi("Setup", setup["setup"])
        builder.kpi("Bias", setup["bias"])
        if setup["setup"] == "squeeze_pending":
            builder.kpi("Long Trigger", _fmt(setup["long_trigger"]))
            builder.kpi("Short Trigger", _fmt(setup["short_trigger"]))
        elif setup["setup"] != "no_trade":
            builder.kpi("Entry", _fmt(setup["entry"]))
            builder.kpi("Stop", _fmt(setup["stop"]))
            builder.kpi("Target", _fmt(setup["target"]))
            builder.kpi("R:R", f"{setup['rr']:.2f}")
        if "error" not in entry:
            builder.kpi("%B (entry TF)", f"{entry['pct_b']:.2f}")
            builder.kpi("BW Rank (entry TF)", f"{entry['bw_rank']:.0f}")
            builder.kpi("Squeeze", "on" if entry["squeeze_on"] else "off")

        builder.section(
            "02 / TIMEFRAMES",
            "Band read per timeframe. The trend row vetoes the entry row.",
        )
        builder.table(
            rows,
            [
                "Timeframe",
                "Verdict",
                "Price",
                "%B",
                "Bandwidth",
                "BW Rank",
                "Squeeze",
                "ADX",
                "Vol x",
                "Upper",
                "Middle",
                "Lower",
            ],
        )

        if "error" not in entry:
            builder.section(
                "03 / CHARTS",
                "Bands on the entry timeframe, and how tight the band is versus its own history.",
            )
            builder.plotly(_band_chart(entry, pair))
            bw_fig = _bandwidth_chart(entry, pair, config.squeeze_rank)
            if bw_fig is not None:
                builder.plotly(bw_fig)

        builder.markdown(summary)
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return summary

"""Rank a watchlist by Bollinger squeeze tightness — where a setup is brewing.

`band_state` answers "what is this pair doing?". This routine answers "which pair is
about to do something?". It computes the bandwidth percentile rank for every pair on the
list and sorts ascending, so the most compressed bands surface first. A squeeze does not
say which way price will break, only that the break is likely to be violent — so the
output is a watchlist, not a set of trades.

Routines are loaded standalone by file path, so the indicator helpers here are
intentionally self-contained rather than imported from a sibling routine.
"""

import asyncio
import logging

from pydantic import BaseModel, ConfigDict, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

_KELTNER_MULT = 1.5
_EXPANSION_LOOKBACK = 3
_EXPANSION_FACTOR = 1.15
_MAX_CONCURRENCY = 10

_DEFAULT_WATCHLIST = (
    "BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,DOGE-USDT,AVAX-USDT,LINK-USDT,SUI-USDT"
)


class Config(BaseModel):
    """Rank a watchlist by Bollinger bandwidth percentile — tightest squeeze first."""

    # Reject unknown keys. Without this, a model passing `symbol` instead of
    # `trading_pair` would silently fall back to the default pair/connector and
    # return a confident answer about the wrong market.
    model_config = ConfigDict(extra="forbid")

    trading_pairs: str = Field(
        default=_DEFAULT_WATCHLIST, description="Comma-separated pairs to screen"
    )
    connector_name: str = Field(
        default="binance_perpetual", description="Exchange connector"
    )
    interval: str = Field(default="1h", description="Candle interval for the screen")
    bb_period: int = Field(default=20, description="Bollinger moving-average period")
    bb_std: float = Field(
        default=2.0, description="Bollinger standard-deviation multiplier"
    )
    max_records: int = Field(
        default=300, description="Candles per pair (also the rank history depth)"
    )
    squeeze_rank: float = Field(
        default=20.0, description="bw_rank at or below which a pair counts as squeezed"
    )
    top_n: int = Field(default=15, description="Rows to show")


# ── Indicator helpers ────────────────────────────────────────────────────────


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


def _percentile_rank(history: list, value: float) -> float:
    if not history:
        return 50.0
    below = sum(1 for h in history if h < value)
    ties = sum(1 for h in history if h == value)
    return (below + 0.5 * ties) / len(history) * 100


def _fmt(value) -> str:
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


def _screen(pair: str, candles: list, cfg: Config) -> dict | None:
    if not candles or len(candles) < cfg.bb_period + _EXPANSION_LOOKBACK + 1:
        return None

    closes = [float(c.get("close", 0)) for c in candles]
    bbs = _bollinger_series(closes, cfg.bb_period, cfg.bb_std)
    bb = bbs[-1]
    if bb is None:
        return None
    bb_prev = bbs[-1 - _EXPANSION_LOOKBACK]

    history = [b["bandwidth_pct"] for b in bbs[:-1] if b]
    bw_rank = _percentile_rank(history, bb["bandwidth_pct"])

    atrs = _sma_series(_true_range_series(candles), cfg.bb_period)
    emas = _ema_series(closes, cfg.bb_period)
    squeeze_on = False
    if atrs[-1] is not None and emas[-1] is not None:
        kc_upper = emas[-1] + _KELTNER_MULT * atrs[-1]
        kc_lower = emas[-1] - _KELTNER_MULT * atrs[-1]
        squeeze_on = bb["upper"] < kc_upper and bb["lower"] > kc_lower

    expanding = (
        bool(bb_prev)
        and bb["bandwidth_pct"] > bb_prev["bandwidth_pct"] * _EXPANSION_FACTOR
    )
    if squeeze_on or bw_rank <= cfg.squeeze_rank:
        state = "firing" if expanding else "squeeze"
    elif expanding:
        state = "expanding"
    elif bw_rank >= 80:
        state = "stretched"
    else:
        state = "normal"

    return {
        "pair": pair,
        "state": state,
        "price": closes[-1],
        "pct_b": bb["pct_b"],
        "bandwidth_pct": bb["bandwidth_pct"],
        "bw_rank": bw_rank,
        "squeeze_on": squeeze_on,
        "upper": bb["upper"],
        "lower": bb["lower"],
    }


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return RoutineResult(text="No server available")

    pairs = [p.strip().upper() for p in config.trading_pairs.split(",") if p.strip()]
    if not pairs:
        return RoutineResult(text="No trading pairs configured")

    connector = config.connector_name
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def fetch(pair: str):
        async with sem:
            try:
                raw = await client.market_data.get_candles(
                    connector,
                    pair,
                    interval=config.interval,
                    max_records=config.max_records,
                )
            except Exception as e:
                logger.warning(f"Candle fetch failed for {pair}: {e}")
                return pair, []
            if isinstance(raw, list):
                return pair, raw
            return pair, (
                raw.get("data", raw.get("candles", [])) if isinstance(raw, dict) else []
            )

    fetched = await asyncio.gather(*[fetch(p) for p in pairs])

    results = []
    skipped = []
    for pair, candles in fetched:
        row = _screen(pair, candles, config)
        if row is None:
            skipped.append(pair)
        else:
            results.append(row)

    if not results:
        return RoutineResult(
            text=f"No usable candle data for any of: {', '.join(pairs)}"
        )

    results.sort(key=lambda r: r["bw_rank"])
    shown = results[: config.top_n]

    table = [
        {
            "Pair": r["pair"],
            "State": r["state"],
            "BW Rank": f"{r['bw_rank']:.0f}",
            "Bandwidth": f"{r['bandwidth_pct']:.2f}%",
            "%B": f"{r['pct_b']:.2f}",
            "TTM Squeeze": "yes" if r["squeeze_on"] else "no",
            "Price": _fmt(r["price"]),
            "Upper": _fmt(r["upper"]),
            "Lower": _fmt(r["lower"]),
        }
        for r in shown
    ]
    columns = [
        "Pair",
        "State",
        "BW Rank",
        "Bandwidth",
        "%B",
        "TTM Squeeze",
        "Price",
        "Upper",
        "Lower",
    ]

    squeezed = [r for r in results if r["state"] in ("squeeze", "firing")]
    firing = [r for r in results if r["state"] == "firing"]

    lines = [
        f"**Squeeze Screener** — {len(results)} pairs on {connector} @ {config.interval}, BB({config.bb_period}, {config.bb_std})",
        "",
        f"squeezed: {len(squeezed)} ({', '.join(r['pair'] for r in squeezed) or 'none'})",
        f"firing: {len(firing)} ({', '.join(r['pair'] for r in firing) or 'none'})",
        f"tightest: {shown[0]['pair']} at bw_rank {shown[0]['bw_rank']:.0f} ({shown[0]['bandwidth_pct']:.2f}%)",
    ]
    if skipped:
        lines.append(f"skipped (insufficient data): {', '.join(skipped)}")
    lines += [
        "",
        "A squeeze is a *watch* signal — it gives no direction. Run `band_state` on the",
        "candidates and wait for a close outside the band before taking a side.",
    ]
    summary = "\n".join(lines)

    try:
        import plotly.graph_objects as go

        from condor.reports import ReportBuilder

        builder = ReportBuilder("Bollinger Squeeze Screener")
        builder.source("routine", "squeeze_screener").tags(
            ["bollinger", "squeeze", "screener"]
        )
        builder.manual_order()

        builder.section(
            "01 / WATCHLIST",
            "Bandwidth percentile rank per pair. Lower rank = tighter band.",
        )
        builder.kpi("Pairs Screened", str(len(results)))
        builder.kpi("Squeezed", str(len(squeezed)))
        builder.kpi("Firing", str(len(firing)))
        builder.kpi("Tightest", f"{shown[0]['pair']} (rank {shown[0]['bw_rank']:.0f})")
        builder.table(table, columns)

        colors = {
            "squeeze": "#f43f5e",
            "firing": "#f59e0b",
            "expanding": "#38bdf8",
            "stretched": "#a78bfa",
        }
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=[r["pair"] for r in shown],
                y=[r["bw_rank"] for r in shown],
                marker_color=[colors.get(r["state"], "#64748b") for r in shown],
                name="BW rank",
            )
        )
        fig.add_hline(
            y=config.squeeze_rank,
            line_dash="dot",
            line_color="#f43f5e",
            annotation_text=f"squeeze threshold (p{config.squeeze_rank:.0f})",
        )
        fig.update_layout(
            title=f"Bandwidth Percentile Rank — {connector} @ {config.interval}",
            xaxis_title="Pair",
            yaxis_title="Bandwidth percentile rank (0 = tightest)",
            yaxis_range=[0, 100],
            template="plotly_dark",
            height=380,
            legend=dict(
                orientation="h", yanchor="top", y=-0.25, xanchor="center", x=0.5
            ),
        )
        builder.plotly(fig)

        builder.markdown(summary)
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return RoutineResult(text=summary, table_data=table, table_columns=columns)

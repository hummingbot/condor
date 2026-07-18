"""Fetch OHLCV candles for a Solana pool from GeckoTerminal and compute
RSI, EMA bands, Bollinger Bands, and MACD for a per-token momentum read.

GeckoTerminal backend selection mirrors scan_trending_memecoins: env vars
COINGECKO_API_KEY and CONDOR_COINGECKO_PLAN control which base URL is used.
"""

import datetime
import logging
import os

import aiohttp
import numpy as np
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

GT_PUBLIC_BASE = "https://api.geckoterminal.com/api/v2"
GT_DEMO_BASE = "https://api.coingecko.com/api/v3/onchain"
GT_PRO_BASE = "https://pro-api.coingecko.com/api/v3/onchain"

# MACD needs EMA-26 warm-up + EMA-9 signal warm-up = 35 minimum
MIN_CANDLES_REQUIRED = 35


def _gt_backend() -> tuple[str, dict]:
    key = os.environ.get("COINGECKO_API_KEY", "").strip()
    if not key:
        return GT_PUBLIC_BASE, {"Accept": "application/json;version=20230302"}
    plan = os.environ.get("CONDOR_COINGECKO_PLAN", "demo").strip().lower()
    if plan == "pro":
        return GT_PRO_BASE, {"accept": "application/json", "x-cg-pro-api-key": key}
    return GT_DEMO_BASE, {"accept": "application/json", "x-cg-demo-api-key": key}


class Config(BaseModel):
    """Fetch OHLCV candles and compute RSI, EMA bands, Bollinger Bands, and MACD for a Solana pool."""

    pool_address: str = Field(description="GeckoTerminal pool address on Solana")
    timeframe: str = Field(
        default="hour",
        description="Candle timeframe: 'minute', 'hour', or 'day'",
    )
    aggregate: int = Field(
        default=1,
        description="Candle aggregation multiplier (e.g. 5 for 5m, 1 for 1h)",
    )
    limit: int = Field(
        default=100,
        description="Number of candles to fetch (minimum 35 required for MACD)",
    )
    rsi_period: int = Field(default=14, description="RSI lookback period")
    ema_fast: int = Field(default=9, description="Fast EMA period")
    ema_slow: int = Field(default=21, description="Slow EMA period")
    bb_period: int = Field(default=20, description="Bollinger Band SMA period")
    bb_std: float = Field(default=2.0, description="Bollinger Band std multiplier")
    context_bars: int = Field(default=10, description="Recent bars to show in output table")


# ---------------------------------------------------------------------------
# Indicator math — pure numpy, no pandas-ta dependency
# ---------------------------------------------------------------------------

def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """EMA initialised at series[0]; multiplier k = 2 / (period + 1)."""
    k = 2.0 / (period + 1)
    out = np.empty(len(series), dtype=float)
    out[0] = float(series[0])
    for i in range(1, len(series)):
        out[i] = series[i] * k + out[i - 1] * (1.0 - k)
    return out


def _rsi(series: np.ndarray, period: int) -> np.ndarray:
    """Wilder RSI. First period+1 values are NaN."""
    delta = np.diff(series.astype(float))
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    out = np.full(len(series), np.nan)
    if len(gains) < period:
        return out

    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        out[i + 1] = 100.0 - 100.0 / (1.0 + rs)

    return out


def _bollinger(
    series: np.ndarray, period: int, num_std: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rolling SMA ± num_std * population std. Returns (middle, upper, lower)."""
    n = len(series)
    mid = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        window = series[i - period + 1 : i + 1]
        sma = float(np.mean(window))
        std = float(np.std(window, ddof=0))
        mid[i] = sma
        upper[i] = sma + num_std * std
        lower[i] = sma - num_std * std
    return mid, upper, lower


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run(config: Config, context=None) -> str:
    from condor.reports import ReportBuilder
    from routines.base import RoutineResult

    base, headers = _gt_backend()
    path = f"networks/solana/pools/{config.pool_address}/ohlcv/{config.timeframe}"
    params = {"aggregate": config.aggregate, "limit": config.limit, "currency": "usd"}

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{base}/{path}", params=params, headers=headers
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"GeckoTerminal OHLCV fetch failed HTTP {resp.status}: {body[:400]}"
                )
            data = await resp.json()

    ohlcv_list = (
        (data.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
    )
    if not ohlcv_list:
        raise RuntimeError(
            f"GeckoTerminal returned no OHLCV data for pool {config.pool_address!r} "
            f"(timeframe={config.timeframe}, aggregate={config.aggregate}). "
            "Verify the pool address and timeframe."
        )

    # GT returns newest-first; reverse to chronological order
    ohlcv_list = list(reversed(ohlcv_list))
    n = len(ohlcv_list)

    if n < MIN_CANDLES_REQUIRED:
        raise RuntimeError(
            f"Only {n} candles returned — need at least {MIN_CANDLES_REQUIRED} to compute "
            f"MACD (EMA-26 warm-up + EMA-9 signal). Increase 'limit' or use a shorter timeframe."
        )

    timestamps = [int(c[0]) for c in ohlcv_list]
    closes = np.array([float(c[4]) for c in ohlcv_list])

    # --- indicators ---
    rsi_vals = _rsi(closes, config.rsi_period)
    ema_fast_vals = _ema(closes, config.ema_fast)
    ema_slow_vals = _ema(closes, config.ema_slow)
    bb_mid, bb_upper, bb_lower = _bollinger(closes, config.bb_period, config.bb_std)

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = ema12 - ema26
    macd_signal = _ema(macd_line, 9)
    macd_hist = macd_line - macd_signal

    # --- latest scalars ---
    price = float(closes[-1])
    rsi = float(rsi_vals[-1])
    ef = float(ema_fast_vals[-1])
    es = float(ema_slow_vals[-1])
    bb_m = float(bb_mid[-1])
    bb_u = float(bb_upper[-1])
    bb_l = float(bb_lower[-1])
    macd = float(macd_line[-1])
    sig = float(macd_signal[-1])
    hist = float(macd_hist[-1])

    bb_range = bb_u - bb_l
    bb_pct_b = (price - bb_l) / bb_range if bb_range != 0 else float("nan")
    ema_cross = "fast>slow" if ef > es else "fast<slow"

    # --- signal reads ---
    reads: list[str] = []
    if not np.isnan(rsi):
        tag = "OVERBOUGHT" if rsi > 70 else ("oversold" if rsi < 30 else "neutral")
        reads.append(f"RSI {rsi:.1f} — {tag}")
    reads.append(f"EMA {ema_cross}")
    reads.append(f"MACD hist {'▲ bullish' if hist > 0 else '▼ bearish'} ({hist:+.2e})")
    if not np.isnan(bb_pct_b):
        if bb_pct_b > 0.8:
            reads.append(f"BB %B={bb_pct_b:.2f} — near upper (extended)")
        elif bb_pct_b < 0.2:
            reads.append(f"BB %B={bb_pct_b:.2f} — near lower (oversold zone)")
        else:
            reads.append(f"BB %B={bb_pct_b:.2f} — mid-band")

    # --- context table (last N bars) ---
    table_cols = [
        "Time (UTC)", "Close", "RSI",
        f"EMA{config.ema_fast}", f"EMA{config.ema_slow}", "BB %B", "MACD hist",
    ]
    ctx_rows = []
    for i in range(max(0, n - config.context_bars), n):
        ts_str = datetime.datetime.utcfromtimestamp(timestamps[i]).strftime("%m-%d %H:%M")
        b_range = float(bb_upper[i]) - float(bb_lower[i])
        pct_b_str = (
            f"{(closes[i] - bb_lower[i]) / b_range:.2f}"
            if not np.isnan(bb_lower[i]) and b_range != 0
            else "-"
        )
        ctx_rows.append({
            "Time (UTC)": ts_str,
            "Close": f"{closes[i]:.6f}",
            "RSI": f"{rsi_vals[i]:.1f}" if not np.isnan(rsi_vals[i]) else "-",
            f"EMA{config.ema_fast}": f"{ema_fast_vals[i]:.6f}",
            f"EMA{config.ema_slow}": f"{ema_slow_vals[i]:.6f}",
            "BB %B": pct_b_str,
            "MACD hist": f"{macd_hist[i]:+.2e}",
        })

    timeframe_label = f"{config.aggregate}{config.timeframe[0].upper()}"
    title = f"TA Read — {config.pool_address[:10]}… {timeframe_label} ({n} bars)"

    # --- report ---
    builder = ReportBuilder(title)
    builder.source("routine", "token_ta_read")
    builder.tags(["solana", "technical-analysis", "memecoin"])
    builder.kpi("Price (USD)", f"${price:.6f}")
    builder.kpi(f"RSI ({config.rsi_period})", f"{rsi:.1f}" if not np.isnan(rsi) else "N/A")
    builder.kpi(f"EMA {config.ema_fast}", f"{ef:.6f}")
    builder.kpi(f"EMA {config.ema_slow}", f"{es:.6f}")
    builder.kpi("EMA Cross", ema_cross)
    builder.kpi("BB %B", f"{bb_pct_b:.2f}" if not np.isnan(bb_pct_b) else "N/A")
    builder.kpi("BB Upper", f"{bb_u:.6f}")
    builder.kpi("BB Lower", f"{bb_l:.6f}")
    builder.kpi("MACD", f"{macd:+.2e}")
    builder.kpi("Signal", f"{sig:+.2e}")
    builder.kpi("Hist", f"{hist:+.2e}")
    builder.markdown("**Signals:** " + " | ".join(reads))
    builder.table(ctx_rows, table_cols)
    builder.manual_order()
    try:
        await builder.save()
    except Exception as e:
        logger.warning(f"Report save failed: {e}")

    summary = (
        f"{title}\n"
        f"Price: ${price:.6f}\n"
        f"RSI({config.rsi_period}): {rsi:.1f}  |  "
        f"EMA{config.ema_fast}: {ef:.6f}  EMA{config.ema_slow}: {es:.6f}  [{ema_cross}]\n"
        f"BB({config.bb_period}): lower={bb_l:.6f}  mid={bb_m:.6f}  upper={bb_u:.6f}  %B={bb_pct_b:.2f}\n"
        f"MACD(12,26): {macd:+.2e}  Signal(9): {sig:+.2e}  Hist: {hist:+.2e}\n"
        + "\n".join(f"  • {r}" for r in reads)
    )

    return RoutineResult(
        text=summary,
        table_data=ctx_rows,
        table_columns=table_cols,
    )

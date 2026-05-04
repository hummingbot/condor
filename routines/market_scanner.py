"""Scan top perpetual markets by volume, analyze volatility and volume patterns."""

import asyncio
import logging
import time
from typing import Any

import aiohttp
import numpy as np
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

BINANCE_FUTURES_TICKER = "https://fapi.binance.com/fapi/v1/ticker/24hr"
MAX_CONCURRENT = 10  # concurrent candle fetches


class Config(BaseModel):
    """Scan top perpetual markets for volume/volatility profiles and classify as mature or degen."""

    connector: str = Field(
        default="binance_perpetual", description="Exchange connector"
    )
    top_n: int = Field(default=100, description="Number of top pairs by 24h volume")
    lookback_hours: int = Field(default=4, description="Hours of 1m candle data")
    min_volume_usd: float = Field(
        default=5_000_000, description="Min 24h quote volume (USD)"
    )
    mature_count: int = Field(default=10, description="Top N mature markets to show")
    degen_count: int = Field(default=10, description="Top N degen markets to show")


# ---------------------------------------------------------------------------
# Step 1: Get top pairs by 24h volume from Binance directly
# ---------------------------------------------------------------------------

async def fetch_top_pairs(top_n: int, min_volume: float) -> list[dict]:
    """Fetch 24h tickers from Binance Futures and return top N by quote volume."""
    async with aiohttp.ClientSession() as session:
        async with session.get(BINANCE_FUTURES_TICKER) as resp:
            resp.raise_for_status()
            tickers = await resp.json()

    # Filter USDT pairs, sort by quote volume
    usdt_tickers = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        quote_vol = float(t.get("quoteVolume", 0))
        if quote_vol < min_volume:
            continue
        # Convert symbol format: BTCUSDT -> BTC-USDT
        base = symbol.replace("USDT", "")
        usdt_tickers.append({
            "trading_pair": f"{base}-USDT",
            "symbol": symbol,
            "volume_24h_usd": quote_vol,
            "price": float(t.get("lastPrice", 0)),
            "price_change_pct": float(t.get("priceChangePercent", 0)),
        })

    usdt_tickers.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
    return usdt_tickers[:top_n]


# ---------------------------------------------------------------------------
# Step 2: Fetch 1m candles via hummingbot API client
# ---------------------------------------------------------------------------

async def fetch_candles_for_pair(
    client, connector: str, trading_pair: str, max_records: int, semaphore: asyncio.Semaphore
) -> list[dict] | None:
    """Fetch 1m candles for a single pair with concurrency control."""
    async with semaphore:
        try:
            result = await client.market_data.get_candles(
                connector_name=connector,
                trading_pair=trading_pair,
                interval="1m",
                max_records=max_records,
            )
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("data", [])
            return None
        except Exception as e:
            logger.debug(f"Candles failed for {trading_pair}: {e}")
            return None


async def fetch_all_candles(
    client, connector: str, pairs: list[dict], max_records: int
) -> dict[str, list[dict]]:
    """Fetch candles for all pairs with bounded concurrency."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = {
        p["trading_pair"]: fetch_candles_for_pair(
            client, connector, p["trading_pair"], max_records, semaphore
        )
        for p in pairs
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    candles_map = {}
    for pair_name, result in zip(tasks.keys(), results):
        if isinstance(result, list) and len(result) > 0:
            candles_map[pair_name] = result
    return candles_map


# ---------------------------------------------------------------------------
# Step 3: Analyze each pair
# ---------------------------------------------------------------------------

def analyze_pair(candles: list[dict], pair_info: dict) -> dict[str, Any] | None:
    """Analyze volume patterns and volatility for a single pair's candles."""
    if len(candles) < 30:
        return None

    try:
        closes = np.array([float(c["close"]) for c in candles])
        highs = np.array([float(c["high"]) for c in candles])
        lows = np.array([float(c["low"]) for c in candles])
        volumes = np.array([float(c["volume"]) for c in candles])
        quote_volumes = volumes * closes  # approximate quote volume per candle
    except (KeyError, ValueError):
        return None

    if closes[-1] == 0 or np.mean(volumes) == 0:
        return None

    # --- Volume analysis ---
    # Coefficient of variation of per-candle quote volume
    vol_mean = np.mean(quote_volumes)
    vol_std = np.std(quote_volumes)
    volume_cv = vol_std / vol_mean if vol_mean > 0 else 0

    # Volume consistency: split into 15-min buckets (15 candles each for 1m)
    bucket_size = 15
    n_buckets = len(quote_volumes) // bucket_size
    if n_buckets >= 2:
        bucket_sums = [
            np.sum(quote_volumes[i * bucket_size : (i + 1) * bucket_size])
            for i in range(n_buckets)
        ]
        bucket_arr = np.array(bucket_sums)
        bucket_cv = np.std(bucket_arr) / np.mean(bucket_arr) if np.mean(bucket_arr) > 0 else 0
    else:
        bucket_cv = volume_cv

    # --- NATR (Normalized Average True Range) ---
    # True Range per candle
    prev_closes = np.roll(closes, 1)
    prev_closes[0] = closes[0]
    tr = np.maximum(
        highs - lows,
        np.maximum(np.abs(highs - prev_closes), np.abs(lows - prev_closes)),
    )
    # Rolling NATR over 14-period windows
    natr_period = 14
    if len(tr) >= natr_period * 2:
        natr_values = []
        for i in range(natr_period, len(tr)):
            window = tr[i - natr_period : i]
            atr = np.mean(window)
            natr = (atr / closes[i]) * 100 if closes[i] > 0 else 0
            natr_values.append(natr)
        natr_arr = np.array(natr_values)
        natr_mean = float(np.mean(natr_arr))
        natr_std = float(np.std(natr_arr))
        natr_cv = natr_std / natr_mean if natr_mean > 0 else 0
        natr_max = float(np.max(natr_arr))
        # Spike ratio: max NATR / mean NATR
        natr_spike_ratio = natr_max / natr_mean if natr_mean > 0 else 0
    else:
        atr = np.mean(tr)
        natr_mean = (atr / closes[-1]) * 100 if closes[-1] > 0 else 0
        natr_cv = 0
        natr_spike_ratio = 1.0

    # --- Price range ---
    price_range_pct = ((np.max(highs) - np.min(lows)) / closes[-1]) * 100

    return {
        "trading_pair": pair_info["trading_pair"],
        "price": pair_info["price"],
        "price_change_24h": pair_info["price_change_pct"],
        "volume_24h_usd": pair_info["volume_24h_usd"],
        "candle_count": len(candles),
        # Volume metrics
        "volume_cv": round(volume_cv, 3),
        "bucket_cv": round(bucket_cv, 3),
        # Volatility metrics
        "natr_mean": round(natr_mean, 4),
        "natr_cv": round(natr_cv, 3),
        "natr_spike_ratio": round(natr_spike_ratio, 2),
        "price_range_pct": round(price_range_pct, 2),
    }


# ---------------------------------------------------------------------------
# Step 4: Score and classify
# ---------------------------------------------------------------------------

def classify_markets(analyses: list[dict], mature_count: int, degen_count: int) -> dict:
    """Score and classify markets into mature and degen buckets."""
    if not analyses:
        return {"mature": [], "degen": []}

    # Normalize metrics to 0-1 range for scoring
    vol_24h = np.array([a["volume_24h_usd"] for a in analyses])
    natr_means = np.array([a["natr_mean"] for a in analyses])
    natr_cvs = np.array([a["natr_cv"] for a in analyses])
    volume_cvs = np.array([a["volume_cv"] for a in analyses])
    bucket_cvs = np.array([a["bucket_cv"] for a in analyses])

    def normalize(arr):
        mn, mx = arr.min(), arr.max()
        return (arr - mn) / (mx - mn) if mx > mn else np.zeros_like(arr)

    vol_norm = normalize(vol_24h)
    natr_norm = normalize(natr_means)
    natr_cv_norm = normalize(natr_cvs)
    vol_cv_norm = normalize(volume_cvs)
    bucket_cv_norm = normalize(bucket_cvs)

    # Mature score: high volume, low NATR variation, continuous volume
    # Higher = more mature
    mature_scores = (
        vol_norm * 0.35
        + (1 - natr_cv_norm) * 0.25
        + (1 - bucket_cv_norm) * 0.20
        + (1 - vol_cv_norm) * 0.20
    )

    # Degen score: high NATR, high volatility spikes, can have lower volume
    # Higher = more degen
    degen_scores = (
        natr_norm * 0.35
        + natr_cv_norm * 0.25
        + bucket_cv_norm * 0.15
        + vol_cv_norm * 0.10
        + (1 - vol_norm) * 0.15  # lower volume adds to degen-ness
    )

    for i, a in enumerate(analyses):
        a["mature_score"] = round(float(mature_scores[i]), 3)
        a["degen_score"] = round(float(degen_scores[i]), 3)

    mature_sorted = sorted(analyses, key=lambda x: x["mature_score"], reverse=True)
    degen_sorted = sorted(analyses, key=lambda x: x["degen_score"], reverse=True)

    return {
        "mature": mature_sorted[:mature_count],
        "degen": degen_sorted[:degen_count],
        "total_analyzed": len(analyses),
    }


# ---------------------------------------------------------------------------
# Step 5: Format output
# ---------------------------------------------------------------------------

def format_volume(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    return f"${v / 1_000:.0f}K"


def format_results(result: dict, lookback_hours: int) -> str:
    lines = [
        f"Market Scanner ({lookback_hours}h lookback, 1m candles)",
        f"Analyzed: {result['total_analyzed']} pairs",
        "",
    ]

    # Mature markets
    lines.append("MATURE MARKETS (high volume, stable volatility)")
    lines.append("─" * 40)
    for i, m in enumerate(result["mature"], 1):
        chg = m["price_change_24h"]
        chg_str = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
        lines.append(
            f"{i}. {m['trading_pair']}"
            f"  vol: {format_volume(m['volume_24h_usd'])}"
            f"  24h: {chg_str}"
        )
        lines.append(
            f"   NATR: {m['natr_mean']:.3f}%"
            f"  NATR-CV: {m['natr_cv']:.2f}"
            f"  vol-CV: {m['bucket_cv']:.2f}"
            f"  range: {m['price_range_pct']:.1f}%"
        )

    lines.append("")
    lines.append("DEGEN MARKETS (high volatility, spiky activity)")
    lines.append("─" * 40)
    for i, d in enumerate(result["degen"], 1):
        chg = d["price_change_24h"]
        chg_str = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
        lines.append(
            f"{i}. {d['trading_pair']}"
            f"  vol: {format_volume(d['volume_24h_usd'])}"
            f"  24h: {chg_str}"
        )
        lines.append(
            f"   NATR: {d['natr_mean']:.3f}%"
            f"  NATR-CV: {d['natr_cv']:.2f}"
            f"  vol-CV: {d['bucket_cv']:.2f}"
            f"  spike: {d['natr_spike_ratio']:.1f}x"
        )

    lines.append("")
    lines.append("Legend:")
    lines.append("  NATR = avg normalized ATR (higher = more volatile)")
    lines.append("  NATR-CV = NATR consistency (lower = steadier volatility)")
    lines.append("  vol-CV = volume consistency (lower = more continuous)")
    lines.append("  spike = max NATR / avg NATR (higher = sharper spikes)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Scan top markets and classify by volume/volatility profile."""
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available. Configure servers in /config."

    max_records = config.lookback_hours * 60  # 1m candles

    # Step 1: Get top pairs by 24h volume
    try:
        top_pairs = await fetch_top_pairs(config.top_n, config.min_volume_usd)
    except Exception as e:
        return f"Failed to fetch tickers: {e}"

    if not top_pairs:
        return "No pairs found matching volume criteria."

    # Step 2: Fetch 1m candles for all pairs
    candles_map = await fetch_all_candles(client, config.connector, top_pairs, max_records)

    if not candles_map:
        return "Failed to fetch candles for any pair."

    # Step 3: Analyze each pair
    pair_lookup = {p["trading_pair"]: p for p in top_pairs}
    analyses = []
    for pair_name, candles in candles_map.items():
        result = analyze_pair(candles, pair_lookup[pair_name])
        if result:
            analyses.append(result)

    if not analyses:
        return "Analysis failed — no valid candle data."

    # Step 4: Classify
    classified = classify_markets(analyses, config.mature_count, config.degen_count)

    # Step 5: Format
    text = format_results(classified, config.lookback_hours)

    try:
        from condor.reports import ReportBuilder

        def _to_table(items):
            return [
                {"Pair": m["trading_pair"], "Volume 24h": format_volume(m["volume_24h_usd"]),
                 "24h Chg": f"{m['price_change_24h']:+.1f}%", "NATR": f"{m['natr_mean']:.3f}%",
                 "NATR-CV": f"{m['natr_cv']:.2f}", "Vol-CV": f"{m['bucket_cv']:.2f}",
                 "Range": f"{m['price_range_pct']:.1f}%"}
                for m in items
            ]

        builder = ReportBuilder(f"Market Scanner ({config.lookback_hours}h)")
        builder.source("routine", "market_scanner").tags(["scanner", "volatility"])
        builder.markdown(f"Analyzed {classified['total_analyzed']} pairs · {config.lookback_hours}h lookback · 1m candles")
        builder.markdown("### Mature Markets\nHigh volume, stable volatility")
        builder.table(_to_table(classified["mature"]))
        builder.markdown("### Degen Markets\nHigh volatility, spiky activity")
        builder.table(_to_table(classified["degen"]))
        builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return text

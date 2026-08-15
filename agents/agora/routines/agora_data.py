"""Fetch OHLCV + fundamentals for Agora's debate pairs and cache them for the tick.

Signal layer only: this routine NEVER places an order and never talks to a
Hummingbot connector. It pulls public market data, formats it as LLM-readable
text, stores it in the agent memory store, and renders a dashboard report.

Venue-agnostic by construction — `market_source` only decides where the
*candles* come from. Where the trade executes is set in the strategy's
`default_trading_context`.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"

# Bitget USDT-M futures granularity codes.
_BITGET_GRANULARITY = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "4h": "4H", "1d": "1D",
}

# CoinGecko ids for the crypto leg. Commodity perps (XAU/CL) have no CoinGecko
# entry — they fall back to pure price action, which is expected, not an error.
_COINGECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}

_COMMODITY_CONTEXT = {
    "XAU": (
        "XAU is spot gold quoted in USDT. Drivers: real yields, DXY, Fed policy "
        "expectations, and safe-haven flow. It is NOT a crypto asset — treat "
        "crypto-native sentiment as irrelevant here."
    ),
    "CL": (
        "CL is WTI crude oil quoted in USDT. Drivers: OPEC+ supply policy, "
        "inventory data, geopolitical risk premium, and global demand. It is NOT "
        "a crypto asset — treat crypto-native sentiment as irrelevant here."
    ),
    "XAG": (
        "XAG is spot silver quoted in USDT. Drivers: industrial demand plus the "
        "same monetary factors that move gold, with higher beta."
    ),
}


class Config(BaseModel):
    """Fetch candles and fundamentals for Agora's debate pairs."""

    pairs: str = Field(
        default="BTC-USDT,XAU-USDT,CL-USDT",
        description="Comma-separated pairs to gather data for (Hummingbot format)",
    )
    market_source: str = Field(
        default="bitget",
        description="Public market-data source for candles: bitget or gate_io",
    )
    kline_period: str = Field(
        default="4h", description="Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 1d)"
    )
    kline_limit: int = Field(default=100, description="Number of candles to fetch")
    coingecko_enabled: bool = Field(
        default=True, description="Fetch CoinGecko fundamentals for crypto pairs"
    )


def _parse_pairs(raw: str) -> list[str]:
    return [p.strip().upper() for p in raw.split(",") if p.strip()]


def _to_venue_symbol(pair: str, source: str) -> str:
    """BTC-USDT -> BTCUSDT (bitget) | BTC_USDT (gate_io)."""
    base, _, quote = pair.partition("-")
    return f"{base}{quote}" if source == "bitget" else f"{base}_{quote}"


async def _fetch_candles(
    session: aiohttp.ClientSession, pair: str, cfg: Config
) -> list[dict]:
    """Return newest-last candles as dicts. Empty list on any failure."""
    symbol = _to_venue_symbol(pair, cfg.market_source)
    try:
        if cfg.market_source == "bitget":
            url = "https://api.bitget.com/api/v2/mix/market/candles"
            params = {
                "symbol": symbol,
                "productType": "usdt-futures",
                "granularity": _BITGET_GRANULARITY.get(cfg.kline_period, "4H"),
                "limit": str(cfg.kline_limit),
            }
            async with session.get(url, params=params, timeout=20) as resp:
                payload = await resp.json()
            # Bitget: [ts, open, high, low, close, baseVol, quoteVol], oldest-first
            rows = payload.get("data") or []
            return [
                {
                    "ts": int(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                }
                for r in rows
            ]

        url = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
        params = {
            "contract": symbol,
            "interval": cfg.kline_period,
            "limit": str(cfg.kline_limit),
        }
        async with session.get(url, params=params, timeout=20) as resp:
            rows = await resp.json()
        if not isinstance(rows, list):
            return []
        # Gate: {t,o,h,l,c,v}, oldest-first
        return [
            {
                "ts": int(r["t"]) * 1000,
                "open": float(r["o"]),
                "high": float(r["h"]),
                "low": float(r["l"]),
                "close": float(r["c"]),
                "volume": float(r.get("v", 0)),
            }
            for r in rows
        ]
    except Exception as exc:  # noqa: BLE001 - never raise into the tick
        logger.warning("agora_data: candle fetch failed for %s: %s", pair, exc)
        return []


async def _fetch_fundamentals(
    session: aiohttp.ClientSession, base: str, enabled: bool
) -> dict:
    """CoinGecko snapshot for crypto; static macro context for commodities."""
    if base in _COMMODITY_CONTEXT:
        return {"text": _COMMODITY_CONTEXT[base], "change_24h": None, "source": "macro"}

    coin_id = _COINGECKO_IDS.get(base)
    if not enabled or not coin_id:
        return {"text": "", "change_24h": None, "source": "none"}

    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": coin_id,
            "vs_currencies": "usd",
            "include_market_cap": "true",
            "include_24hr_vol": "true",
            "include_24hr_change": "true",
        }
        async with session.get(url, params=params, timeout=20) as resp:
            data = (await resp.json()).get(coin_id, {})
        if not data:
            return {"text": "", "change_24h": None, "source": "none"}
        change = data.get("usd_24h_change")
        text = (
            f"{base} fundamentals (CoinGecko): price ${data.get('usd', 0):,.2f}, "
            f"market cap ${data.get('usd_market_cap', 0):,.0f}, "
            f"24h volume ${data.get('usd_24h_vol', 0):,.0f}, "
            f"24h change {change:+.2f}%." if change is not None else ""
        )
        return {"text": text, "change_24h": change, "source": "coingecko"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("agora_data: coingecko failed for %s: %s", base, exc)
        return {"text": "", "change_24h": None, "source": "error"}


def _sma(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    window = values[-period:] if len(values) >= period else values
    return sum(window) / len(window)


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _format_market_data(candles: list[dict], pair: str, period: str) -> tuple[str, dict]:
    """Render candles as LLM-readable text plus a metrics dict for the dashboard."""
    if not candles:
        return f"No candle data available for {pair}.", {}

    closes = [c["close"] for c in candles]
    last, first = candles[-1], candles[0]
    change = ((last["close"] - first["close"]) / first["close"]) * 100 if first["close"] else 0.0
    high = max(c["high"] for c in candles)
    low = min(c["low"] for c in candles)
    sma20, sma50 = _sma(closes, 20), _sma(closes, 50)
    rsi = _rsi(closes)

    if sma20 > sma50 * 1.001:
        trend = "UPTREND (SMA20 above SMA50)"
    elif sma20 < sma50 * 0.999:
        trend = "DOWNTREND (SMA20 below SMA50)"
    else:
        trend = "RANGING (SMA20 flat vs SMA50)"

    metrics = {
        "price": last["close"],
        "change_pct": change,
        "rsi": rsi,
        "trend": trend,
        "high": high,
        "low": low,
        "candles": len(candles),
    }

    text = (
        f"Technical market data for {pair} ({period} candles, last {len(candles)} periods):\n"
        f"Current price: {last['close']:,.4f}\n"
        f"Period change: {change:+.2f}%\n"
        f"Period high / low: {high:,.4f} / {low:,.4f}\n"
        f"SMA20: {sma20:,.4f} | SMA50: {sma50:,.4f} | Trend: {trend}\n"
        f"RSI(14): {rsi:.1f}\n"
        f"Last candle O/H/L/C: {last['open']:,.4f} / {last['high']:,.4f} / "
        f"{last['low']:,.4f} / {last['close']:,.4f} (vol {last['volume']:,.0f})"
    )
    return text, metrics


async def _gather_pair(session, pair: str, cfg: Config) -> dict:
    base = pair.split("-")[0]
    candles, fundamentals = await asyncio.gather(
        _fetch_candles(session, pair, cfg),
        _fetch_fundamentals(session, base, cfg.coingecko_enabled),
    )
    market_text, metrics = _format_market_data(candles, pair, cfg.kline_period)
    return {
        "pair": pair,
        "base": base,
        "market_data": market_text,
        "fundamentals": fundamentals["text"],
        "change_24h": fundamentals["change_24h"],
        "fundamentals_source": fundamentals["source"],
        "metrics": metrics,
        "candle_count": len(candles),
        "source": cfg.market_source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE):
    """Gather data for every debate pair, persist it, and render the report."""
    pairs = _parse_pairs(config.pairs)
    if not pairs:
        return "agora_data: no pairs configured."

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(_gather_pair(session, p, config) for p in pairs)
        )

    # Persist for agora_debate + the tick. manage_memory is the current store;
    # the deprecated notes shim writes here anyway.
    from mcp_servers.condor.tools import memory

    ok = 0
    for item in results:
        if item["candle_count"]:
            ok += 1
        try:
            await memory.manage_memory(
                action="write",
                name=f"agora_market_{item['pair'].replace('-', '_')}",
                content=json.dumps(item),
                description=f"Agora market snapshot for {item['pair']}",
                type="reference",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("agora_data: memory write failed for %s: %s", item["pair"], exc)

    rows = []
    for item in results:
        m = item["metrics"]
        rows.append(
            {
                "Pair": item["pair"],
                "Price": f"{m.get('price', 0):,.4f}" if m else "—",
                "Period Δ": f"{m.get('change_pct', 0):+.2f}%" if m else "—",
                "RSI": f"{m.get('rsi', 0):.0f}" if m else "—",
                "Trend": m.get("trend", "no data").split(" (")[0] if m else "NO DATA",
                "Candles": item["candle_count"],
                "Fundamentals": item["fundamentals_source"],
            }
        )
    columns = ["Pair", "Price", "Period Δ", "RSI", "Trend", "Candles", "Fundamentals"]

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Agora — Market Intelligence")
        builder.source("routine", "agora_data")
        builder.tags(["agora", "market-data", config.market_source])
        builder.kpi("Pairs Tracked", str(len(pairs)))
        builder.kpi("Pairs With Data", f"{ok}/{len(pairs)}")
        builder.kpi("Interval", config.kline_period)
        builder.kpi("Source", config.market_source)
        builder.section(
            "01 / PAIR SNAPSHOT",
            "Price, momentum and trend for each asset entering the debate.",
        )
        builder.table(rows, columns)
        builder.section(
            "02 / BRIEFING PACKETS",
            "Exact text handed to the analyst agents on the debate floor.",
        )
        for item in results:
            body = item["market_data"]
            if item["fundamentals"]:
                body += f"\n\n{item['fundamentals']}"
            builder.markdown(f"### {item['pair']}\n```\n{body}\n```")
        builder.manual_order()
        await builder.save()
    except Exception as exc:  # noqa: BLE001 - report must never break the tick
        logger.warning("agora_data: report generation failed: %s", exc)

    from routines.base import RoutineResult

    summary = (
        f"Agora data refreshed from {config.market_source}: {ok}/{len(pairs)} pairs "
        f"with candles ({config.kline_period})."
    )
    return RoutineResult(text=summary, table_data=rows, table_columns=columns)

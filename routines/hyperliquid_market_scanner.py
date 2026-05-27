"""Scan top Hyperliquid perpetual markets by volume, analyze volatility and volume patterns."""

CATEGORY = "Market Data"

import asyncio
import logging
import time
from typing import Any

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.market_scanner import analyze_pair, classify_markets, format_volume

logger = logging.getLogger(__name__)

HL_INFO_URL = "https://api.hyperliquid.xyz/info"


class Config(BaseModel):
    """Scan top Hyperliquid perpetual markets for volume/volatility profiles."""

    connector: str = Field(
        default="hyperliquid_perpetual",
        description="Exchange connector for candles",
    )
    top_n: int = Field(default=30, description="Number of top pairs by 24h volume")
    lookback_hours: int = Field(default=6, description="Hours of 1m candle data")
    min_volume_usd: float = Field(
        default=2_000_000, description="Min 24h notional volume (USD)"
    )
    mature_count: int = Field(default=8, description="Top N mature markets to show")
    degen_count: int = Field(default=8, description="Top N degen markets to show")
    exclude_hip3: bool = Field(
        default=False, description="If true, exclude issuer:symbol HIP-3 pairs"
    )
    quote_suffix: str = Field(
        default="USD", description="Quote suffix filter for trading_rules keys"
    )
    max_concurrent_candles: int = Field(
        default=20,
        ge=1,
        le=30,
        description="Max parallel HL REST candleSnapshot requests",
    )


def _build_rules_lookup(trading_rules: dict[str, Any]) -> dict[str, str]:
    """Map normalized pair keys to canonical trading_rules keys."""
    lookup: dict[str, str] = {}
    for pair in trading_rules:
        norm = pair.upper().replace("_", "-")
        lookup[norm] = pair
    return lookup


def _resolve_trading_pair(
    asset_name: str, rules_lookup: dict[str, str], quote_suffix: str
) -> str | None:
    """Map Hyperliquid universe asset name to a trading_rules pair key."""
    quote = quote_suffix.upper()
    candidates: list[str] = []

    if ":" in asset_name:
        candidates.append(f"{asset_name}-{quote}")
        candidates.append(f"{asset_name}-USDC")
    else:
        candidates.append(f"{asset_name}-{quote}")
        candidates.append(f"{asset_name}-USDC")

    for cand in candidates:
        norm = cand.upper().replace("_", "-")
        if norm in rules_lookup:
            return rules_lookup[norm]

    return None


def _price_change_pct(ctx: dict) -> float:
    """Estimate 24h % change from markPx and prevDayPx when available."""
    try:
        mark = float(ctx.get("markPx", 0))
        prev = float(ctx.get("prevDayPx", 0))
        if mark > 0 and prev > 0:
            return ((mark - prev) / prev) * 100
    except (TypeError, ValueError):
        pass
    return 0.0


async def fetch_top_hl_pairs(
    client,
    connector: str,
    top_n: int,
    min_volume: float,
    exclude_hip3: bool,
    quote_suffix: str,
) -> list[dict]:
    """Fetch top pairs by 24h notional volume from Hyperliquid metaAndAssetCtxs."""
    try:
        trading_rules = await client.connectors.get_trading_rules(
            connector_name=connector
        )
    except Exception as e:
        raise RuntimeError(f"Failed to fetch trading rules for {connector}: {e}") from e

    if not isinstance(trading_rules, dict) or not trading_rules:
        raise RuntimeError(f"No trading rules returned for {connector}")

    rules_lookup = _build_rules_lookup(trading_rules)
    quote = quote_suffix.upper()
    suffix = f"-{quote}"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            HL_INFO_URL,
            json={"type": "metaAndAssetCtxs"},
            headers={"Content-Type": "application/json"},
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()

    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError("Unexpected metaAndAssetCtxs response shape")

    meta, asset_ctxs = payload[0], payload[1]
    universe = meta.get("universe", []) if isinstance(meta, dict) else []
    if not universe or not isinstance(asset_ctxs, list):
        raise RuntimeError("Missing universe or assetCtxs in Hyperliquid response")

    candidates: list[dict] = []
    for i, asset in enumerate(universe):
        if i >= len(asset_ctxs):
            break
        if not isinstance(asset, dict):
            continue

        asset_name = asset.get("name", "")
        if not asset_name:
            continue
        if exclude_hip3 and ":" in asset_name:
            continue

        ctx = asset_ctxs[i] if isinstance(asset_ctxs[i], dict) else {}
        try:
            volume_24h = float(ctx.get("dayNtlVlm", 0))
        except (TypeError, ValueError):
            volume_24h = 0.0

        if volume_24h < min_volume:
            continue

        trading_pair = _resolve_trading_pair(asset_name, rules_lookup, quote_suffix)
        if not trading_pair:
            logger.debug("No trading_rules match for HL asset %s", asset_name)
            continue

        if not trading_pair.upper().endswith(
            suffix
        ) and not trading_pair.upper().endswith("-USDC"):
            continue

        try:
            price = float(ctx.get("markPx", 0))
        except (TypeError, ValueError):
            price = 0.0

        if price <= 0:
            continue

        candidates.append(
            {
                "trading_pair": trading_pair,
                "asset_name": asset_name,
                "volume_24h_usd": volume_24h,
                "price": price,
                "price_change_pct": _price_change_pct(ctx),
            }
        )

    candidates.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
    return candidates[:top_n]


def format_results(result: dict, lookback_hours: int) -> str:
    tradeable = sorted(
        {m["trading_pair"] for m in result.get("mature", []) + result.get("degen", [])}
        | {a["trading_pair"] for a in result.get("all_analyzed", [])}
    )
    lines = [
        f"Hyperliquid Market Scanner ({lookback_hours}h lookback, 1m candles)",
        f"Analyzed: {result['total_analyzed']} pairs",
        "",
        "TRADEABLE PAIRS (use only these exact connector names this tick):",
        ", ".join(tradeable) if tradeable else "(none)",
        "",
    ]

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


def _pair_label(trading_pair: str) -> str:
    """Short label for chart text."""
    if ":" in trading_pair:
        try:
            issuer_symbol, _ = trading_pair.rsplit("-", 1)
            return issuer_symbol.split(":", 1)[-1]
        except ValueError:
            pass
    return trading_pair.rsplit("-", 1)[0]


def _parse_hl_candle_snapshot(raw: list) -> list[dict]:
    """Convert HL candleSnapshot rows to market_scanner analyze_pair format."""
    candles = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            candles.append(
                {
                    "close": float(row["c"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "volume": float(row["v"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return candles


async def _fetch_candle_snapshot_rest(
    session: aiohttp.ClientSession,
    coin: str,
    start_ms: int,
    end_ms: int,
) -> tuple[list[dict] | None, str | None]:
    """Fetch 1m candles via HL REST (no hummingbot WS)."""
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": "1m",
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }
    try:
        async with session.post(HL_INFO_URL, json=payload) as resp:
            if resp.status != 200:
                return None, f"http_{resp.status}"
            data = await resp.json()
        if not isinstance(data, list) or not data:
            return None, "empty_snapshot"
        parsed = _parse_hl_candle_snapshot(data)
        if len(parsed) >= 30:
            return parsed, None
        return None, f"too_few_bars:{len(parsed)}"
    except Exception as e:
        return None, f"{type(e).__name__}:{str(e)[:80]}"


async def _fetch_all_candles_hl_rest(
    session: aiohttp.ClientSession,
    pairs: list[dict],
    lookback_hours: int,
    max_concurrent: int,
) -> dict[str, list[dict]]:
    """Fetch candles for all pairs via parallel HL REST candleSnapshot."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - lookback_hours * 3600 * 1000

    semaphore = asyncio.Semaphore(max_concurrent)
    candles_map: dict[str, list[dict]] = {}
    failures: list[dict] = []

    async def _fetch_one(pair_info: dict) -> None:
        trading_pair = pair_info["trading_pair"]
        coin = pair_info["asset_name"]
        async with semaphore:
            candles, err = await _fetch_candle_snapshot_rest(
                session, coin, start_ms, end_ms
            )
            if candles:
                candles_map[trading_pair] = candles
            elif err:
                failures.append({"pair": trading_pair, "coin": coin, "reason": err})

    await asyncio.gather(*[_fetch_one(p) for p in pairs], return_exceptions=True)
    if failures:
        logger.warning(
            "HL candleSnapshot failures: %d/%d — %s",
            len(failures),
            len(pairs),
            failures[:5],
        )
    return candles_map


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Scan top Hyperliquid markets and classify by volume/volatility profile."""
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available. Configure servers in /config."

    try:
        top_pairs = await fetch_top_hl_pairs(
            client,
            config.connector,
            config.top_n,
            config.min_volume_usd,
            config.exclude_hip3,
            config.quote_suffix,
        )
    except Exception as e:
        return f"Failed to fetch Hyperliquid market data: {e}"

    if not top_pairs:
        return "No pairs found matching volume criteria on Hyperliquid."

    async with aiohttp.ClientSession() as session:
        candles_map = await _fetch_all_candles_hl_rest(
            session,
            top_pairs,
            config.lookback_hours,
            config.max_concurrent_candles,
        )

    if not candles_map:
        return "Failed to fetch candles for any pair."

    pair_lookup = {p["trading_pair"]: p for p in top_pairs}
    analyses = []
    for pair_name, candles in candles_map.items():
        result = analyze_pair(candles, pair_lookup[pair_name])
        if result:
            analyses.append(result)

    if not analyses:
        return "Analysis failed — no valid candle data."

    classified = classify_markets(analyses, config.mature_count, config.degen_count)
    classified["all_analyzed"] = analyses
    text = format_results(classified, config.lookback_hours)

    try:
        import plotly.graph_objects as go

        from condor.reports import ReportBuilder

        def _to_table(items):
            return [
                {
                    "Pair": m["trading_pair"],
                    "Volume 24h": format_volume(m["volume_24h_usd"]),
                    "24h Chg": f"{m['price_change_24h']:+.1f}%",
                    "NATR": f"{m['natr_mean']:.3f}%",
                    "NATR-CV": f"{m['natr_cv']:.2f}",
                    "Vol-CV": f"{m['bucket_cv']:.2f}",
                    "Range": f"{m['price_range_pct']:.1f}%",
                }
                for m in items
            ]

        mature_set = {m["trading_pair"] for m in classified["mature"]}
        degen_set = {m["trading_pair"] for m in classified["degen"]}

        fig = go.Figure()
        for a in analyses:
            pair = a["trading_pair"]
            if pair in mature_set:
                color, group, name = "#3fb950", "mature", "Mature"
            elif pair in degen_set:
                color, group, name = "#f85149", "degen", "Degen"
            else:
                color, group, name = "#8b949e", "other", "Other"

            fig.add_trace(
                go.Scatter(
                    x=[a["natr_mean"]],
                    y=[a["volume_24h_usd"]],
                    mode="markers+text",
                    marker=dict(
                        color=color,
                        size=max(6, 20 - a["bucket_cv"] * 10),
                        line=dict(width=1, color="#0d1117"),
                    ),
                    text=[_pair_label(pair)],
                    textposition="top center",
                    textfont=dict(size=8, color="#8b949e"),
                    name=name,
                    legendgroup=group,
                    showlegend=False,
                    hovertemplate=(
                        f"{pair}<br>NATR: {a['natr_mean']:.3f}%"
                        f"<br>Vol: {format_volume(a['volume_24h_usd'])}<extra></extra>"
                    ),
                )
            )

        for label, color in [
            ("Mature", "#3fb950"),
            ("Degen", "#f85149"),
            ("Other", "#8b949e"),
        ]:
            fig.add_trace(
                go.Scatter(
                    x=[None],
                    y=[None],
                    mode="markers",
                    marker=dict(color=color, size=10),
                    name=label,
                )
            )

        fig.update_layout(
            title=f"Hyperliquid Market Scanner — NATR vs Volume ({config.lookback_hours}h)",
            xaxis_title="NATR Mean (%)",
            yaxis_title="24h Volume (USD)",
            yaxis_type="log",
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9", size=10),
            margin=dict(l=60, r=30, t=100, b=40),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1
            ),
        )
        fig.update_xaxes(gridcolor="#21262d")
        fig.update_yaxes(gridcolor="#21262d")

        builder = ReportBuilder(
            f"Hyperliquid Market Scanner ({config.lookback_hours}h)"
        )
        builder.source("routine", "hyperliquid_market_scanner").tags(
            ["scanner", "volatility", "hyperliquid"]
        )
        builder.markdown(
            f"Analyzed {classified['total_analyzed']} Hyperliquid pairs "
            f"with {config.lookback_hours}h lookback on 1m candles"
        )
        builder.plotly(fig)
        builder.markdown("### Mature Markets\nHigh volume, stable volatility")
        builder.table(_to_table(classified["mature"]))
        builder.markdown("### Degen Markets\nHigh volatility, spiky activity")
        builder.table(_to_table(classified["degen"]))
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return text

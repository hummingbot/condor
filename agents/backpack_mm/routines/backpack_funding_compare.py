import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"


class Config(BaseModel):
    """Compare Backpack perpetual funding rates vs Binance and Bybit."""

    show_top_n: int = Field(
        default=20, description="Show top N assets by absolute Backpack funding rate"
    )


def _extract_base(symbol: str, exchange: str) -> str:
    """Normalize a perp symbol to its base asset ticker."""
    if exchange == "backpack":
        # BTC_USDC_PERP → BTC
        return symbol.replace("_PERP", "").rsplit("_", 1)[0]
    elif exchange in ("binance", "bybit"):
        # BTCUSDT / BTCUSDC → BTC
        for quote in ("USDT", "USDC", "BUSD", "USD"):
            if symbol.endswith(quote):
                return symbol[: -len(quote)]
    return symbol


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    async with aiohttp.ClientSession() as session:

        async def fetch_backpack():
            async with session.get(
                "https://api.backpack.exchange/api/v1/markPrices",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Backpack markPrices: HTTP {resp.status}")
                return await resp.json()

        async def fetch_binance():
            async with session.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Binance premiumIndex: HTTP {resp.status}")
                return await resp.json()

        async def fetch_bybit():
            async with session.get(
                "https://api.bybit.com/v5/market/tickers?category=linear",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Bybit tickers: HTTP {resp.status}")
                data = await resp.json()
                return data.get("result", {}).get("list", [])

        try:
            bp_raw, binance_raw, bybit_raw = await asyncio.gather(
                fetch_backpack(), fetch_binance(), fetch_bybit()
            )
        except Exception as e:
            logger.warning(f"Funding rate fetch failed: {e}")
            return f"Failed to fetch funding rates: {e}"

    # ── Parse Backpack (perps only) ───────────────────────────────────────────
    backpack_rates: dict[str, float] = {}
    for item in bp_raw or []:
        symbol = item.get("symbol", "")
        if not symbol.endswith("_PERP"):
            continue
        fr = item.get("fundingRate")
        if fr is None:
            continue
        try:
            base = _extract_base(symbol, "backpack")
            backpack_rates[base] = float(fr) * 100  # decimal → %
        except (ValueError, TypeError):
            pass

    # ── Parse Binance ─────────────────────────────────────────────────────────
    binance_rates: dict[str, float] = {}
    for item in binance_raw or []:
        symbol = item.get("symbol", "")
        fr = item.get("lastFundingRate")
        if fr is None:
            continue
        try:
            base = _extract_base(symbol, "binance")
            # Keep only USDT-margined (no BUSD dups)
            if base and base not in binance_rates:
                binance_rates[base] = float(fr) * 100
        except (ValueError, TypeError):
            pass

    # ── Parse Bybit ──────────────────────────────────────────────────────────
    bybit_rates: dict[str, float] = {}
    for item in bybit_raw or []:
        symbol = item.get("symbol", "")
        fr = item.get("fundingRate")
        if fr is None:
            continue
        try:
            base = _extract_base(symbol, "bybit")
            if base and base not in bybit_rates:
                bybit_rates[base] = float(fr) * 100
        except (ValueError, TypeError):
            pass

    # ── Build merged rows (Backpack assets as the anchor) ────────────────────
    wide_rows: list[dict] = []
    for base, bp_rate in backpack_rates.items():
        bi_rate = binance_rates.get(base)
        by_rate = bybit_rates.get(base)

        others = [v for v in [bi_rate, by_rate] if v is not None]
        avg_others = sum(others) / len(others) if others else None
        delta = round(bp_rate - avg_others, 4) if avg_others is not None else None

        wide_rows.append({
            "asset": base,
            "backpack_pct": round(bp_rate, 4),
            "binance_pct": round(bi_rate, 4) if bi_rate is not None else None,
            "bybit_pct": round(by_rate, 4) if by_rate is not None else None,
            "avg_others_pct": round(avg_others, 4) if avg_others is not None else None,
            "delta_vs_avg_pct": delta,
        })

    # Sort by |Backpack rate| descending
    wide_rows.sort(key=lambda r: abs(r["backpack_pct"]), reverse=True)
    wide_rows = wide_rows[: config.show_top_n]

    # Long-format rows for grouped bar chart: one row per (asset, exchange)
    chart_rows: list[dict] = []
    for r in wide_rows:
        chart_rows.append({"asset": r["asset"], "exchange": "Backpack", "rate_pct": r["backpack_pct"]})
        if r["binance_pct"] is not None:
            chart_rows.append({"asset": r["asset"], "exchange": "Binance", "rate_pct": r["binance_pct"]})
        if r["bybit_pct"] is not None:
            chart_rows.append({"asset": r["asset"], "exchange": "Bybit", "rate_pct": r["bybit_pct"]})

    # ── Stats ─────────────────────────────────────────────────────────────────
    n_matched_binance = sum(1 for r in wide_rows if r["binance_pct"] is not None)
    n_matched_bybit = sum(1 for r in wide_rows if r["bybit_pct"] is not None)
    n_positive = sum(1 for r in wide_rows if r["backpack_pct"] > 0)
    n_negative = sum(1 for r in wide_rows if r["backpack_pct"] < 0)
    # Highest positive delta = Backpack funding much higher than others (more bullish here)
    best_bp_premium = max(
        (r for r in wide_rows if r["delta_vs_avg_pct"] is not None),
        key=lambda r: r["delta_vs_avg_pct"],
        default=None,
    )
    # Most negative delta = Backpack funding much lower (more bearish / better for shorts here)
    best_bp_discount = min(
        (r for r in wide_rows if r["delta_vs_avg_pct"] is not None),
        key=lambda r: r["delta_vs_avg_pct"],
        default=None,
    )

    # ── ReportBuilder ─────────────────────────────────────────────────────────
    from condor.reports import ReportBuilder
    from routines.base import RoutineResult

    builder = ReportBuilder("Backpack Funding Rate Comparison")
    builder.source("routine", "backpack_funding_compare").tags(
        ["backpack", "funding", "perps", "comparison"]
    )
    builder.manual_order()

    builder.section("Overview", f"Perpetual funding rates: Backpack vs Binance & Bybit — {timestamp}")
    builder.kpi("Backpack Perp Markets", str(len(backpack_rates)))
    builder.kpi("Matched on Binance", str(n_matched_binance))
    builder.kpi("Matched on Bybit", str(n_matched_bybit))
    builder.kpi("Positive Funding (longs pay)", str(n_positive))
    builder.kpi("Negative Funding (shorts pay)", str(n_negative))
    if best_bp_premium:
        builder.kpi(
            "Biggest BP Premium",
            f"{best_bp_premium['asset']} +{best_bp_premium['delta_vs_avg_pct']:.4f}%",
        )
    if best_bp_discount:
        builder.kpi(
            "Biggest BP Discount",
            f"{best_bp_discount['asset']} {best_bp_discount['delta_vs_avg_pct']:.4f}%",
        )

    builder.markdown(
        "**Reading the delta:** "
        "*Positive delta* = Backpack funding is higher than other exchanges → longs on Backpack pay more. "
        "*Negative delta* = Backpack funding is lower → better to be long on Backpack (or short on others). "
        "Funding periods are typically 8h — annualise: rate × 3 × 365."
    )

    if chart_rows:
        builder.dataset("chart", chart_rows)
        builder.dataset("comparison", wide_rows)

        builder.section(
            "Funding Rates by Asset",
            "Grouped by exchange — positive = longs pay shorts (bullish sentiment)",
        )
        builder.select_filter("asset-filter", "chart", "asset", label="Asset")
        builder.chart(
            "bar",
            "Funding Rate % per 8h (by exchange)",
            "chart",
            x="asset",
            y="rate_pct",
            color="exchange",
            x_label="Asset",
            y_label="Funding Rate %",
            reference_lines=[{"axis": "y", "value": 0, "label": "Zero"}],
        )

        builder.section(
            "Backpack vs Market Consensus",
            "Delta = Backpack − avg(Binance, Bybit). Positive = Backpack more bullish.",
        )
        builder.chart(
            "bar",
            "Delta: Backpack vs Avg Others (%)",
            "comparison",
            x="asset",
            y="delta_vs_avg_pct",
            x_label="Asset",
            y_label="Delta %",
            reference_lines=[{"axis": "y", "value": 0, "label": "No difference"}],
        )

        builder.section("Detailed Comparison Table")
        builder.data_table(
            "comparison",
            title="Funding Rates by Asset",
            columns=[
                "asset",
                "backpack_pct",
                "binance_pct",
                "bybit_pct",
                "avg_others_pct",
                "delta_vs_avg_pct",
            ],
        )

    await builder.save()

    # ── Inline text output ────────────────────────────────────────────────────
    header = f"{'Asset':<10} {'Backpack':>10} {'Binance':>10} {'Bybit':>10} {'Delta':>10}"
    sep = "─" * 55
    lines = [
        f"Funding Rate Comparison — {timestamp}",
        "Positive = longs pay shorts. Delta = Backpack − avg others.",
        "",
        header,
        sep,
    ]
    for r in wide_rows:
        bp = f"{r['backpack_pct']:+.4f}%"
        bi = f"{r['binance_pct']:+.4f}%" if r["binance_pct"] is not None else "    N/A"
        by = f"{r['bybit_pct']:+.4f}%" if r["bybit_pct"] is not None else "    N/A"
        delta = f"{r['delta_vs_avg_pct']:+.4f}%" if r["delta_vs_avg_pct"] is not None else "    N/A"
        lines.append(f"{r['asset']:<10} {bp:>10} {bi:>10} {by:>10} {delta:>10}")

    return RoutineResult(text="\n".join(lines))

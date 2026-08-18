"""Compare funding rates between Backpack and Binance perpetuals — interval-normalized carry comparison."""

import asyncio
import logging

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

CATEGORY = "Arbitrage"

BACKPACK_API = "https://api.backpack.exchange/api/v1"
BINANCE_FAPI = "https://fapi.binance.com/fapi/v1"

HOURS_PER_YEAR = 8760


class Config(BaseModel):
    """Compare Backpack vs Binance perpetual funding rates, normalized by each exchange's payment interval."""

    backpack_interval_h: int = Field(
        default=1, description="Backpack funding interval in hours (default: 1h)"
    )
    binance_interval_h: int = Field(
        default=8, description="Binance funding interval in hours (default: 8h)"
    )
    min_spread_apr_pct: float = Field(
        default=10.0, description="Min annualized spread % to highlight as opportunity"
    )
    top_n: int = Field(default=20, description="Max rows in opportunities table")


def _normalize_base(base: str) -> str:
    """Strip kilo-denomination prefix for cross-exchange matching (1000SHIB / kSHIB → SHIB)."""
    if base.startswith("1000"):
        return base[4:]
    if base and base[0] == "k" and len(base) > 1 and base[1].isupper():
        return base[1:]
    return base


async def _fetch_backpack_rates(
    session: aiohttp.ClientSession,
) -> dict[str, tuple[float, str]]:
    """Returns {canonical_base: (rate_per_payment, raw_base)} for all Backpack perp markets."""
    async with session.get(f"{BACKPACK_API}/markets") as resp:
        markets = await resp.json()

    perp_symbols = [m["symbol"] for m in markets if m.get("marketType") == "PERP"]

    sem = asyncio.Semaphore(8)

    async def _fetch_one(symbol: str):
        async with sem:
            try:
                async with session.get(
                    f"{BACKPACK_API}/fundingRates",
                    params={"symbol": symbol, "limit": 1},
                ) as r:
                    data = await r.json()
                    if isinstance(data, list) and data:
                        rate = data[0].get("fundingRate")
                        if rate is not None:
                            return symbol, float(rate)
            except Exception as e:
                logger.warning(f"Backpack funding fetch failed for {symbol}: {e}")
        return symbol, None

    results = await asyncio.gather(*[_fetch_one(s) for s in perp_symbols])

    out: dict[str, tuple[float, str]] = {}
    for symbol, rate in results:
        if rate is not None:
            raw_base = symbol.replace("_USDC_PERP", "").replace("_USDT_PERP", "")
            canonical = _normalize_base(raw_base)
            out[canonical] = (rate, raw_base)

    return out


async def _fetch_binance_rates(session: aiohttp.ClientSession) -> dict[str, float]:
    """Returns {canonical_base: rate_per_payment} for all Binance USDT perp markets."""
    async with session.get(f"{BINANCE_FAPI}/premiumIndex") as resp:
        data = await resp.json()

    out: dict[str, float] = {}
    for item in data:
        symbol = item.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        raw_base = symbol[:-4]
        canonical = _normalize_base(raw_base)
        rate = item.get("lastFundingRate")
        if rate is not None:
            out[canonical] = float(rate)

    return out


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    async with aiohttp.ClientSession() as session:
        bp_raw, bn_raw = await asyncio.gather(
            _fetch_backpack_rates(session),
            _fetch_binance_rates(session),
        )

    common = sorted(set(bp_raw) & set(bn_raw))

    def to_apr(rate_per_payment: float, interval_h: int) -> float:
        """Convert a per-payment rate to annualized rate (APR)."""
        payments_per_year = HOURS_PER_YEAR / interval_h
        return rate_per_payment * payments_per_year

    rows = []
    for base in common:
        bp_rate_raw, raw_base = bp_raw[base]
        bn_rate_raw = bn_raw[base]

        bp_apr = to_apr(bp_rate_raw, config.backpack_interval_h)
        bn_apr = to_apr(bn_rate_raw, config.binance_interval_h)
        spread_apr = bp_apr - bn_apr

        # Per-hour rates for a granular comparison column
        bp_per_h_bps = (bp_rate_raw / config.backpack_interval_h) * 10000
        bn_per_h_bps = (bn_rate_raw / config.binance_interval_h) * 10000
        spread_per_h_bps = bp_per_h_bps - bn_per_h_bps

        if spread_apr > 0.0001:
            trade = "Long BN / Short BP"
        elif spread_apr < -0.0001:
            trade = "Long BP / Short BN"
        else:
            trade = "Neutral"

        rows.append(
            {
                "Asset": base,
                "BP Symbol": raw_base,
                "BP APR": f"{bp_apr * 100:.2f}%",
                "BN APR": f"{bn_apr * 100:.2f}%",
                "Spread APR": f"{spread_apr * 100:+.2f}%",
                "Spread bps/h": f"{spread_per_h_bps:+.3f}",
                "Trade": trade,
                "_spread_apr": spread_apr,
                "_spread_abs_apr": abs(spread_apr),
                "_bp_apr": bp_apr,
                "_bn_apr": bn_apr,
                "_spread_per_h_bps": spread_per_h_bps,
            }
        )

    rows.sort(key=lambda r: r["_spread_abs_apr"], reverse=True)

    min_spread = config.min_spread_apr_pct / 100
    opps = [r for r in rows if r["_spread_abs_apr"] >= min_spread]
    top_rows = opps[: config.top_n]

    table_cols = [
        "Asset",
        "BP Symbol",
        "BP APR",
        "BN APR",
        "Spread APR",
        "Spread bps/h",
        "Trade",
    ]
    clean_top = [{k: r[k] for k in table_cols} for r in top_rows]
    clean_all = [{k: r[k] for k in table_cols} for r in rows[:60]]

    # ── Report ─────────────────────────────────────────────────────────────
    builder = ReportBuilder("Backpack vs Binance Funding (Interval-Normalized)")
    builder.source("routine", "backpack_binance_funding_comparison")
    builder.tags(["funding", "arbitrage", "backpack", "binance", "carry"])
    builder.manual_order()

    builder.section("01 / OVERVIEW")
    builder.kpi("Backpack Perps", str(len(bp_raw)))
    builder.kpi("Binance Perps", str(len(bn_raw)))
    builder.kpi("Common Markets", str(len(common)))
    builder.kpi(f"Opps ≥{config.min_spread_apr_pct}% APR", str(len(opps)))
    builder.kpi("BP Interval", f"{config.backpack_interval_h}h")
    builder.kpi("BN Interval", f"{config.binance_interval_h}h")

    builder.markdown(
        f"Rates are **annualized**: Backpack raw rate ÷ {config.backpack_interval_h}h × 8,760 h/yr; "
        f"Binance raw rate ÷ {config.binance_interval_h}h × 8,760 h/yr. "
        f"**Spread bps/h** = (BP per-hour rate − BN per-hour rate) × 10,000 — "
        f"the carry you earn or pay per hour of holding the delta-neutral pair."
    )

    dataset_rows = [
        {
            "asset": r["Asset"],
            "bp_apr_pct": round(r["_bp_apr"] * 100, 2),
            "bn_apr_pct": round(r["_bn_apr"] * 100, 2),
            "spread_apr_pct": round(r["_spread_apr"] * 100, 2),
            "abs_spread_apr_pct": round(r["_spread_abs_apr"] * 100, 2),
            "spread_bps_per_h": round(r["_spread_per_h_bps"], 3),
        }
        for r in rows
    ]
    builder.dataset("funding", dataset_rows)
    builder.chart(
        "bar",
        "Spread (APR %) by Asset",
        "funding",
        "asset",
        "spread_apr_pct",
        y_label="Spread APR (%)",
        x_label="Asset",
        reference_lines=[{"axis": "y", "value": 0, "label": "Zero"}],
    )

    builder.section(
        "02 / OPPORTUNITIES",
        f"Sorted by |spread APR|. Threshold: {config.min_spread_apr_pct}% APR. "
        f"BP pays every {config.backpack_interval_h}h, BN every {config.binance_interval_h}h.",
    )
    if clean_top:
        builder.table(clean_top, table_cols)
    else:
        builder.markdown(
            f"No common markets with |spread| ≥ {config.min_spread_apr_pct}% APR."
        )

    builder.section("03 / ALL COMMON MARKETS", "Full list sorted by |spread APR|.")
    builder.table(clean_all, table_cols)

    report_id = await builder.save()

    # ── Inline summary ──────────────────────────────────────────────────────
    top3 = top_rows[:3]
    lines = [
        f"Backpack vs Binance Funding  (BP={config.backpack_interval_h}h / BN={config.binance_interval_h}h intervals)",
        f"{len(common)} common markets | {len(opps)} opps ≥{config.min_spread_apr_pct}% APR",
        "",
    ]
    if top3:
        for r in top3:
            lines.append(
                f"  {r['Asset']:8s}  BP={r['BP APR']:>9s}  BN={r['BN APR']:>9s}"
                f"  spread={r['Spread APR']:>9s}  → {r['Trade']}"
            )
    else:
        lines.append("  No opportunities above threshold.")
    lines.append(f"\nReport: {report_id}")

    return RoutineResult(
        text="\n".join(lines),
        table_data=clean_top[:10],
        table_columns=table_cols,
    )

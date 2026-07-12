import logging

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from routines.base import RoutineResult

logger = logging.getLogger(__name__)

CATEGORY = "Monitoring"

HL_API = "https://api.hyperliquid.xyz/info"


class Config(BaseModel):
    """Fetch current funding rate for a perp and classify as neutral/elevated/extreme."""

    trading_pair: str = Field(default="SOL-USD", description="Trading pair (e.g. SOL-USD)")
    connector_name: str = Field(default="hyperliquid_perpetual", description="Connector name")


def _coin(pair: str) -> str:
    base = pair.split("-")[0]
    if ":" in base:
        dex, sym = base.split(":", 1)
        return f"{dex.lower()}:{sym}"
    return base


def _classify(rate_8h_pct: float) -> str:
    abs_r = abs(rate_8h_pct)
    if abs_r < 0.005:
        return "neutral"
    elif abs_r < 0.02:
        return "elevated"
    return "extreme"


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    coin = _coin(config.trading_pair)

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(HL_API, json={"type": "metaAndAssetCtxs"}) as resp:
            resp.raise_for_status()
            meta, ctxs = await resp.json()

    universe = meta["universe"]
    idx = next((i for i, a in enumerate(universe) if a["name"] == coin), None)
    if idx is None:
        return RoutineResult(text=f"Coin `{coin}` not found in HL universe.")

    ctx = ctxs[idx]
    # HL `funding` field is the 1-hour rate; convert to 8h for standard display
    rate_1h = float(ctx["funding"])
    rate_8h_pct = rate_1h * 8 * 100
    mark_px = float(ctx["markPx"])

    direction = "longs → shorts" if rate_1h > 0 else "shorts → longs"
    status = _classify(rate_8h_pct)

    txt = (
        f"*{config.trading_pair} Funding Check*\n\n"
        f"• Rate (8h): `{rate_8h_pct:+.4f}%` ({direction})\n"
        f"• Mark price: `${mark_px:,.4f}`\n"
        f"• Status: `{status.upper()}`"
    )

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Funding Check — {config.trading_pair}")
        builder.source("routine", "funding_check").tags(["funding", "monitoring"])
        builder.kpi("Rate (8h)", f"{rate_8h_pct:+.4f}%")
        builder.kpi("Mark Price", f"${mark_px:,.4f}")
        builder.kpi("Status", status.upper())
        builder.markdown(txt)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report: {e}")

    return RoutineResult(text=txt)

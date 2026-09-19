"""
risk_envelope — adaptive_grid_trader

Derives grid geometry, leverage and worst-case loss from LIVE volatility
instead of fixed constants.

Core identity this routine is built on:

    D           = ATR(1h) * sqrt(lifetime_hours)
    limit_price = price -/+ 1.5 * D
    avg_fill    = price -/+ 0.5 * D
    worst-case fractional loss = (avg_fill - limit) / avg_fill  ~=  D / price

So the only free lever is leverage:

    leverage = max_loss_pct / (D / price)

max_loss_pct is a USER preference and stays fixed. Volatility moves D, which
moves leverage, which keeps the dollar loss constant. That is the whole point.

CHANGELOG
18092026 - Initial — ATR-derived leverage, grid geometry, liquidation guard
"""

import logging
import math

import pandas as pd
import pandas_ta
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder
from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

# Maintenance margin rate used for the liquidation estimate. Binance's lowest
# BTC tier is 0.4%; higher tiers are worse, so this is the optimistic end and
# the guard below keeps a margin of safety on top of it.
MAINT_MARGIN_RATE = 0.004
# Maker fee per side. A round trip crosses it twice.
MAKER_FEE_PER_SIDE = 0.0002


class Config(BaseModel):
    """Derive grid leverage, geometry and worst-case loss from live volatility."""

    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    connector_name: str = Field(default="binance_perpetual", description="Exchange connector")
    side: str = Field(default="LONG", description="LONG or SHORT")
    budget: float = Field(default=400.0, description="Total quote budget for the strategy")
    reserve_pct: float = Field(default=10.0, description="Percent of budget held back, never traded")
    max_loss_pct: float = Field(default=6.0, description="Max acceptable loss on one grid, % of budget")
    max_leverage: int = Field(default=10, description="Hard leverage ceiling")
    lifetime_hours: int = Field(default=9, description="Target grid lifetime in hours — sets range width")
    atr_period: int = Field(default=14, description="Candles for ATR")
    min_levels: int = Field(default=6, description="Fewest levels that still counts as a grid")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    side = (config.side or "").upper().strip()
    if side not in ("LONG", "SHORT"):
        return f"side must be LONG or SHORT, got {config.side!r}"

    # ---- live candles -> ATR ------------------------------------------------
    raw = await client.market_data.get_candles(
        config.connector_name, config.trading_pair, interval="1h", max_records=200
    )
    records = raw if isinstance(raw, list) else (raw or {}).get("data", (raw or {}).get("candles", []))
    if not records or len(records) < config.atr_period + 2:
        return f"Not enough 1h candles for {config.trading_pair} on {config.connector_name}"

    df = pd.DataFrame(records)
    for col in ("high", "low", "close"):
        if col not in df.columns:
            return f"Candle payload missing '{col}' for {config.trading_pair}"
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["high", "low", "close"])
    if len(df) < config.atr_period + 2:
        return f"Too few clean candles for {config.trading_pair} after parsing"

    atr_series = pandas_ta.atr(df["high"], df["low"], df["close"], length=config.atr_period)
    atr_clean = atr_series.dropna() if atr_series is not None else None
    if atr_clean is None or atr_clean.empty:
        return f"ATR({config.atr_period}) could not be computed for {config.trading_pair}"
    atr = float(atr_clean.iloc[-1])
    price = float(df["close"].iloc[-1])
    if atr <= 0 or price <= 0:
        return f"Bad ATR/price read for {config.trading_pair}: atr={atr}, price={price}"

    # ---- exchange granularity ----------------------------------------------
    rules = await client.connectors.get_trading_rules(config.connector_name, [config.trading_pair])
    rule = (rules or {}).get(config.trading_pair) or {}
    min_notional = float(rule.get("min_notional_size") or 0)
    min_base = float(rule.get("min_order_size") or 0)
    min_level_quote = max(min_notional, min_base * price)
    if min_level_quote <= 0:
        return f"Could not read a usable minimum order size for {config.trading_pair}"

    # ---- the envelope -------------------------------------------------------
    trade_budget = config.budget * (1.0 - config.reserve_pct / 100.0)
    d = atr * math.sqrt(config.lifetime_hours)
    dd_frac = d / price  # worst-case fractional loss on full notional

    lev_risk = (config.max_loss_pct / 100.0) / dd_frac
    leverage = max(1, min(int(lev_risk), config.max_leverage))

    notional = trade_budget * leverage
    levels = int(notional // min_level_quote)

    # leverage the granularity target would have wanted, for the report
    lev_for_min_levels = (config.min_levels * min_level_quote) / trade_budget if trade_budget > 0 else 0.0

    if side == "LONG":
        start_price = price - d
        end_price = price + 3 * d
        limit_price = price - 1.5 * d
        avg_fill = price - 0.5 * d
        liq_price = avg_fill * (1 - 1 / leverage + MAINT_MARGIN_RATE)
        liq_ok = liq_price < limit_price
    else:
        start_price = price - 3 * d
        end_price = price + d
        limit_price = price + 1.5 * d
        avg_fill = price + 0.5 * d
        liq_price = avg_fill * (1 + 1 / leverage - MAINT_MARGIN_RATE)
        liq_ok = liq_price > limit_price

    worst_case_loss = notional * abs(avg_fill - limit_price) / avg_fill
    worst_case_pct = worst_case_loss / config.budget * 100.0

    spacing = (end_price - start_price) / levels if levels > 0 else 0.0
    tp_frac = spacing / price if price > 0 else 0.0
    net_per_round_trip = min_level_quote * (tp_frac - 2 * MAKER_FEE_PER_SIDE)

    # ---- verdict ------------------------------------------------------------
    blockers = []
    if levels < config.min_levels:
        blockers.append(
            f"only {levels} levels fit (need {config.min_levels}); "
            f"each level costs ${min_level_quote:,.2f} on this venue"
        )
    if worst_case_pct > config.max_loss_pct + 0.01:
        blockers.append(f"worst case {worst_case_pct:.2f}% exceeds max_loss_pct {config.max_loss_pct:.2f}%")
    if not liq_ok:
        blockers.append(f"liquidation ${liq_price:,.2f} sits inside limit_price ${limit_price:,.2f}")
    if tp_frac <= 2 * MAKER_FEE_PER_SIDE:
        blockers.append(f"take-profit {tp_frac * 100:.3f}% does not clear round-trip fees")

    deployable = not blockers
    verdict = "DEPLOYABLE" if deployable else "BLOCKED"

    # ---- report -------------------------------------------------------------
    builder = ReportBuilder(f"Risk Envelope — {config.trading_pair} {side}")
    builder.source("routine", "risk_envelope")
    builder.tags(["grid", "risk", "adaptive_grid_trader"])
    builder.manual_order()

    builder.section("01 / VERDICT", "Whether this grid may deploy, and on what terms.")
    builder.kpi("Verdict", verdict)
    builder.kpi("Leverage", f"{leverage}x")
    builder.kpi("Levels", str(levels))
    builder.kpi("Worst-case loss", f"${worst_case_loss:,.2f} ({worst_case_pct:.2f}%)")

    builder.section("02 / VOLATILITY", "Everything downstream is derived from these two numbers.")
    builder.kpi("Price", f"${price:,.2f}")
    builder.kpi(f"ATR(1h,{config.atr_period})", f"${atr:,.2f} ({atr / price * 100:.3f}%)")
    builder.kpi(f"D = ATR x sqrt({config.lifetime_hours}h)", f"${d:,.2f}")
    builder.kpi("Drawdown to limit", f"{dd_frac * 100:.3f}%")

    builder.section("03 / GRID GEOMETRY", "Prices to hand to create_grid_executor.")
    builder.table(
        [
            {"Field": "side", "Value": side},
            {"Field": "start_price", "Value": f"{start_price:,.2f}"},
            {"Field": "end_price", "Value": f"{end_price:,.2f}"},
            {"Field": "limit_price", "Value": f"{limit_price:,.2f}"},
            {"Field": "total_amount_quote", "Value": f"{notional:,.2f}"},
            {"Field": "leverage", "Value": str(leverage)},
            {"Field": "take_profit", "Value": f"{tp_frac:.5f} ({tp_frac * 100:.3f}%)"},
        ],
        ["Field", "Value"],
    )

    builder.section("04 / SIZING", "How the venue's granularity constrains the ladder.")
    builder.table(
        [
            {"Metric": "Trade budget (after reserve)", "Value": f"${trade_budget:,.2f}"},
            {"Metric": "Notional at leverage", "Value": f"${notional:,.2f}"},
            {"Metric": "Min level (venue floor)", "Value": f"${min_level_quote:,.2f}"},
            {"Metric": "Levels that fit", "Value": str(levels)},
            {"Metric": "Spacing", "Value": f"${spacing:,.2f}"},
            {"Metric": "Net per round trip", "Value": f"${net_per_round_trip:,.3f}"},
        ],
        ["Metric", "Value"],
    )

    builder.section("05 / GUARDS", "Liquidation and loss ceilings.")
    builder.table(
        [
            {"Guard": "Leverage from risk", "Value": f"{lev_risk:.2f}x -> capped {leverage}x", "Pass": "-"},
            {"Guard": f"Leverage for {config.min_levels} levels", "Value": f"{lev_for_min_levels:.2f}x", "Pass": "-"},
            {"Guard": "Avg fill (est.)", "Value": f"${avg_fill:,.2f}", "Pass": "-"},
            {"Guard": "Liquidation (est.)", "Value": f"${liq_price:,.2f}", "Pass": "YES" if liq_ok else "NO"},
            {
                "Guard": "Worst case vs max_loss_pct",
                "Value": f"{worst_case_pct:.2f}% vs {config.max_loss_pct:.2f}%",
                "Pass": "YES" if worst_case_pct <= config.max_loss_pct + 0.01 else "NO",
            },
        ],
        ["Guard", "Value", "Pass"],
    )

    if blockers:
        builder.markdown("## Blockers\n\n" + "\n".join(f"- {b}" for b in blockers))

    report_id = await builder.save()

    lines = [
        f"Risk Envelope — {config.trading_pair} {side} on {config.connector_name}",
        f"verdict: {verdict}",
        f"price: ${price:,.2f}  |  ATR(1h,{config.atr_period}): ${atr:,.2f} ({atr / price * 100:.3f}%)",
        f"D: ${d:,.2f}  |  drawdown_to_limit: {dd_frac * 100:.3f}%",
        f"leverage: {leverage}x (risk-implied {lev_risk:.2f}x, ceiling {config.max_leverage}x)",
        f"notional: ${notional:,.2f}  |  levels: {levels} x ${min_level_quote:,.2f}",
        f"start: {start_price:,.2f}  end: {end_price:,.2f}  limit: {limit_price:,.2f}",
        f"take_profit: {tp_frac * 100:.3f}%  |  net/round-trip: ${net_per_round_trip:,.3f}",
        f"worst_case_loss: ${worst_case_loss:,.2f} ({worst_case_pct:.2f}% of budget)",
        f"liq_guard: {'PASS' if liq_ok else 'FAIL'} (liq ${liq_price:,.2f} vs limit ${limit_price:,.2f})",
    ]
    if blockers:
        lines.append("blockers: " + "; ".join(blockers))
    lines.append(f"Report: {report_id}")

    return RoutineResult(
        text="\n".join(lines),
        table_data=[
            {"Field": "side", "Value": side},
            {"Field": "start_price", "Value": f"{start_price:,.2f}"},
            {"Field": "end_price", "Value": f"{end_price:,.2f}"},
            {"Field": "limit_price", "Value": f"{limit_price:,.2f}"},
            {"Field": "total_amount_quote", "Value": f"{notional:,.2f}"},
            {"Field": "leverage", "Value": str(leverage)},
            {"Field": "take_profit", "Value": f"{tp_frac:.5f}"},
        ],
        table_columns=["Field", "Value"],
    )

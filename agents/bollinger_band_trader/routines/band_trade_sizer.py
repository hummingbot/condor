"""Turn a band-derived stop into a sized, pre-flight-checked position_executor payload.

A Bollinger setup is only as good as the size behind it. This routine takes the entry
and stop the bands produced, converts fixed fractional risk into a base-currency
`amount`, and then refuses the trade if any guardrail fails: portfolio reserve, max
position cap, exchange minimum notional, stop sanity, leverage ceiling.

It never places an order. It returns a verdict and, on PASS, the exact payload the
agent should hand to `manage_executors`.

Routines are loaded standalone by file path, so the indicator helpers here are
intentionally self-contained rather than imported from a sibling routine.
"""

import json
import logging

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

CATEGORY = "Risk"

_MIN_STOP_PCT = 0.05  # below this the stop is inside the noise / fee band
_MAX_STOP_PCT = 20.0  # above this the "stop" is not a stop
_MAX_RISK_PCT = 1.0
_MAX_LEVERAGE = 5


class Config(BaseModel):
    """Size a Bollinger setup from its band-derived stop and run pre-trade guardrails."""

    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    connector_name: str = Field(
        default="binance_perpetual", description="Exchange connector"
    )
    side: str = Field(default="long", description="long or short")
    entry_price: float = Field(
        default=0.0, description="Entry price (0 = use the live price)"
    )
    stop_price: float = Field(
        default=0.0, description="Stop price (0 = derive from the bands)"
    )
    target_price: float = Field(
        default=0.0, description="Target price (0 = 2R from the stop)"
    )
    risk_pct: float = Field(
        default=0.5, description="Portfolio percent risked if the stop is hit"
    )
    max_position_pct: float = Field(
        default=10.0, description="Max position notional as percent of portfolio"
    )
    reserve_pct: float = Field(
        default=20.0, description="Portfolio percent that must stay unallocated"
    )
    leverage: int = Field(default=1, description="Leverage for the position executor")
    min_notional: float = Field(
        default=5.0, description="Exchange minimum order notional in quote"
    )
    portfolio_value_quote: float = Field(
        default=0.0, description="Override portfolio value (0 = fetch live)"
    )
    interval: str = Field(
        default="15m", description="Candle interval used to derive the stop"
    )
    bb_period: int = Field(default=20, description="Bollinger moving-average period")
    bb_std: float = Field(
        default=2.0, description="Bollinger standard-deviation multiplier"
    )


# ── Indicator helpers ────────────────────────────────────────────────────────


def _sma(values: list, period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def _stdev(window: list) -> float:
    n = len(window)
    if n < 2:
        return 0.0
    mean = sum(window) / n
    return (sum((x - mean) ** 2 for x in window) / n) ** 0.5


def _atr(candles: list, period: int) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        high, low = float(candles[i].get("high", 0)), float(candles[i].get("low", 0))
        prev_close = float(candles[i - 1].get("close", 0))
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period


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


def _derive_stop(
    side: str, entry: float, mid: float, lower: float, upper: float, atr: float
) -> tuple:
    """Nearest band-structure level on the protective side of the entry.

    Long: the middle band if price is above it, otherwise half an ATR below the lower
    band. Short is the mirror. Falls back to a 1-ATR stop when neither level qualifies,
    which happens when the entry is already outside the structure.
    """
    fallback = atr if atr > 0 else entry * 0.01
    if side == "long":
        candidates = [c for c in (mid, lower - 0.5 * atr) if c < entry]
        if candidates:
            level = max(candidates)
            return level, "middle band" if level == mid else "lower band − 0.5 ATR"
        return entry - fallback, "1 ATR below entry (fallback)"
    candidates = [c for c in (mid, upper + 0.5 * atr) if c > entry]
    if candidates:
        level = min(candidates)
        return level, "middle band" if level == mid else "upper band + 0.5 ATR"
    return entry + fallback, "1 ATR above entry (fallback)"


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return RoutineResult(text="No server available")

    side = config.side.strip().lower()
    if side in ("buy", "1"):
        side = "long"
    elif side in ("sell", "2"):
        side = "short"
    if side not in ("long", "short"):
        return RoutineResult(text=f"Invalid side '{config.side}' — use long or short")

    pair, connector = config.trading_pair.strip().upper(), config.connector_name

    # ── Market context: price + band levels ──────────────────────────────────
    candles = []
    try:
        raw = await client.market_data.get_candles(
            connector,
            pair,
            interval=config.interval,
            max_records=max(config.bb_period * 3, 100),
        )
        if isinstance(raw, list):
            candles = raw
        elif isinstance(raw, dict):
            candles = raw.get("data", raw.get("candles", []))
    except Exception as e:
        logger.warning(f"Candle fetch failed for {pair}: {e}")

    closes = [float(c.get("close", 0)) for c in candles]
    mid = _sma(closes, config.bb_period)
    std = (
        _stdev(closes[-config.bb_period :]) if len(closes) >= config.bb_period else 0.0
    )
    upper = mid + config.bb_std * std if mid else None
    lower = mid - config.bb_std * std if mid else None
    atr = _atr(candles, config.bb_period)

    entry = config.entry_price
    if entry <= 0:
        try:
            prices = await client.market_data.get_prices(
                connector, trading_pairs=[pair]
            )
            entry = float((prices or {}).get(pair, 0) or 0)
        except Exception as e:
            logger.warning(f"Price fetch failed for {pair}: {e}")
        if entry <= 0 and closes:
            entry = closes[-1]
    if entry <= 0:
        return RoutineResult(
            text=f"Could not determine an entry price for {pair} on {connector}"
        )

    stop, stop_source = config.stop_price, "supplied"
    if stop <= 0:
        if mid is None:
            return RoutineResult(
                text=f"No stop supplied and not enough candles to derive one for {pair}"
            )
        stop, stop_source = _derive_stop(side, entry, mid, lower, upper, atr)

    # ── Portfolio ────────────────────────────────────────────────────────────
    portfolio = config.portfolio_value_quote
    portfolio_source = "override"
    if portfolio <= 0:
        portfolio_source = "live"
        try:
            portfolio = float(await client.portfolio.get_total_value() or 0)
        except Exception as e:
            logger.warning(f"Portfolio value fetch failed: {e}")
            portfolio = 0.0

    # ── Sizing ───────────────────────────────────────────────────────────────
    stop_distance = abs(entry - stop)
    stop_pct = stop_distance / entry * 100 if entry else 0.0
    correct_side = stop < entry if side == "long" else stop > entry

    target = config.target_price
    if target <= 0:
        target = (
            entry + 2 * stop_distance if side == "long" else entry - 2 * stop_distance
        )
    reward = abs(target - entry)
    rr = reward / stop_distance if stop_distance > 0 else 0.0

    risk_quote = portfolio * config.risk_pct / 100
    position_cap = portfolio * config.max_position_pct / 100
    deployable = portfolio * (1 - config.reserve_pct / 100)

    raw_notional = risk_quote / (stop_pct / 100) if stop_pct > 0 else 0.0
    notional = min(raw_notional, position_cap, deployable)
    capped_by = ""
    if raw_notional > 0 and notional < raw_notional - 1e-9:
        capped_by = "max_position_pct" if notional == position_cap else "reserve_pct"

    amount_base = notional / entry if entry else 0.0
    margin = notional / config.leverage if config.leverage else notional
    effective_risk = notional * stop_pct / 100

    # ── Guardrails ───────────────────────────────────────────────────────────
    checks = []

    def check(name: str, ok: bool, detail: str, fatal: bool = True) -> bool:
        checks.append(
            {
                "Check": name,
                "Result": "PASS" if ok else ("FAIL" if fatal else "WARN"),
                "Detail": detail,
            }
        )
        return ok or not fatal

    def warn(name: str, detail: str) -> None:
        """Informational row — always a WARN, never blocks the trade."""
        checks.append({"Check": name, "Result": "WARN", "Detail": detail})

    passed = True
    passed &= check(
        "Portfolio value known",
        portfolio > 0,
        f"${portfolio:,.2f} ({portfolio_source})",
    )
    passed &= check(
        "Stop on the protective side",
        correct_side,
        f"{side} entry {_fmt(entry)} / stop {_fmt(stop)} ({stop_source})",
    )
    passed &= check(
        "Stop distance sane",
        _MIN_STOP_PCT <= stop_pct <= _MAX_STOP_PCT,
        f"{stop_pct:.3f}% (allowed {_MIN_STOP_PCT}–{_MAX_STOP_PCT}%)",
    )
    passed &= check(
        "Risk per trade within policy",
        0 < config.risk_pct <= _MAX_RISK_PCT,
        f"{config.risk_pct:.2f}% of portfolio (max {_MAX_RISK_PCT}%)",
    )
    passed &= check(
        "Leverage within policy",
        1 <= config.leverage <= _MAX_LEVERAGE,
        f"{config.leverage}x (max {_MAX_LEVERAGE}x)",
    )
    passed &= check(
        "Meets exchange minimum notional",
        notional >= config.min_notional,
        f"${notional:,.2f} vs ${config.min_notional:,.2f} minimum",
    )
    passed &= check(
        "Margin fits inside the reserve",
        margin <= deployable,
        f"margin ${margin:,.2f} vs ${deployable:,.2f} deployable ({100 - config.reserve_pct:.0f}% of portfolio)",
    )
    check("Reward:risk", rr >= 1.2, f"{rr:.2f} (target {_fmt(target)})", fatal=False)
    if capped_by:
        warn(
            "Size capped",
            f"reduced from ${raw_notional:,.2f} by {capped_by} — risks less than the target",
        )

    verdict = "PASS" if passed else "FAIL"

    payload = {
        "connector_name": connector,
        "trading_pair": pair,
        "side": 1 if side == "long" else 2,
        "amount": round(amount_base, 8),
        "entry_price": round(entry, 8),
        "leverage": config.leverage,
        "triple_barrier_config": {
            "stop_loss": round(stop_pct / 100, 6),
            "take_profit": round(reward / entry, 6),
            "open_order_type": 2,
            "stop_loss_order_type": 1,
            "take_profit_order_type": 2,
        },
    }

    lines = [
        f"**Band Trade Sizer: {pair} on {connector}**",
        "",
        f"verdict: {verdict}",
        f"side: {side}",
        f"entry: {_fmt(entry)}",
        f"stop: {_fmt(stop)} ({stop_source}, {stop_pct:.3f}%)",
        f"target: {_fmt(target)}",
        f"rr: {rr:.2f}",
        f"amount_base: {amount_base:.8f}",
        f"notional_quote: ${notional:,.2f}",
        f"margin_quote: ${margin:,.2f} at {config.leverage}x",
        f"risk_if_stopped: ${effective_risk:,.2f} ({(effective_risk / portfolio * 100) if portfolio else 0:.2f}% of portfolio)",
    ]
    if capped_by:
        lines.append(
            f"note: size capped by {capped_by} (uncapped would be ${raw_notional:,.2f})"
        )
    failures = [c["Check"] for c in checks if c["Result"] == "FAIL"]
    if failures:
        lines.append(f"blocked_by: {', '.join(failures)}")
    else:
        lines += [
            "",
            "position_executor payload:",
            "```json",
            json.dumps(payload, indent=2),
            "```",
        ]
    lines += [
        "",
        "Amounts are pre-quantization — the exchange applies its own step size and may round down.",
    ]
    summary = "\n".join(lines)

    columns = ["Check", "Result", "Detail"]
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Band Trade Sizer: {pair}")
        builder.source("routine", "band_trade_sizer").tags(
            ["bollinger", "risk", "sizing", pair.lower()]
        )
        builder.manual_order()

        builder.section(
            "01 / VERDICT",
            "Whether this Bollinger setup may be deployed, and at what size.",
        )
        builder.kpi("Verdict", verdict)
        builder.kpi("Side", side)
        builder.kpi("Entry", _fmt(entry))
        builder.kpi("Stop", f"{_fmt(stop)} ({stop_pct:.2f}%)")
        builder.kpi("Target", _fmt(target))
        builder.kpi("R:R", f"{rr:.2f}")
        builder.kpi("Amount (base)", f"{amount_base:.8f}")
        builder.kpi("Notional", f"${notional:,.2f}")
        builder.kpi("Margin", f"${margin:,.2f}")
        builder.kpi("Risk if Stopped", f"${effective_risk:,.2f}")

        builder.section(
            "02 / GUARDRAILS",
            "Every check must pass before the payload is considered valid.",
        )
        builder.table(checks, columns)

        builder.markdown(summary)
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return RoutineResult(text=summary, table_data=checks, table_columns=columns)

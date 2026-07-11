from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client
from collections import defaultdict
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


class Config(BaseModel):
    """Deterministic candle-simulation backtest of a symmetric grid on one pair."""
    connector_name: str = Field(default="hyperliquid_perpetual", description="Exchange connector")
    trading_pair: str = Field(default="SOL-USD", description="Trading pair")
    days: int = Field(default=3, description="Lookback window in days")
    interval: str = Field(default="3m", description="Candle resolution")
    band_pct: float = Field(default=2.0, description="Total grid band width, percent, centered on window-start price")
    levels: int = Field(default=10, description="Total grid levels (levels per side = levels//2)")
    tp_pct: float = Field(default=0.2, description="Take-profit distance per fill, percent")
    fee_pct: float = Field(default=0.02, description="Maker fee per side, percent (0.02 = 0.02%)")
    capital_quote: float = Field(default=300.0, description="Capital in quote currency")


def _interval_minutes(interval: str) -> float:
    s = interval.strip().lower()
    if s.endswith("m"):
        return float(s[:-1])
    if s.endswith("h"):
        return float(s[:-1]) * 60
    if s.endswith("d"):
        return float(s[:-1]) * 1440
    return 1.0


def _linspace(start: float, stop: float, n: int) -> list:
    if n <= 1:
        return [start]
    return [start + (stop - start) * i / (n - 1) for i in range(n)]


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    try:
        from condor.reports import ReportBuilder

        # ── Fetch candles ──────────────────────────────────────────────────────
        interval_min = _interval_minutes(config.interval)
        max_records = int(config.days * 24 * 60 / interval_min) + 50

        raw = await client.market_data.get_candles(
            config.connector_name, config.trading_pair, config.interval, max_records
        )
        records = raw if isinstance(raw, list) else raw.get("data", raw.get("candles", []))

        if not records:
            return f"No candles returned for {config.trading_pair} on {config.connector_name}"

        candles = sorted(
            [
                {
                    "timestamp": r.get("timestamp", 0),
                    "open":  float(r.get("open",  0)),
                    "high":  float(r.get("high",  0)),
                    "low":   float(r.get("low",   0)),
                    "close": float(r.get("close", 0)),
                }
                for r in records
            ],
            key=lambda x: x["timestamp"],
        )

        target = int(config.days * 24 * 60 / interval_min)
        candles = candles[-target:]
        n_candles = len(candles)

        if n_candles < 2:
            return f"Not enough candles ({n_candles}) for simulation"

        # ── Grid setup ─────────────────────────────────────────────────────────
        center = candles[0]["open"]
        levels_per_side = max(1, config.levels // 2)
        total_levels = levels_per_side * 2
        order_quote = config.capital_quote / total_levels
        base_qty = order_quote / center          # fixed base size for all orders

        half_band = config.band_pct / 2 / 100
        buy_low   = center * (1 - half_band)
        sell_high = center * (1 + half_band)

        # buy_prices ascending  → index 0 = furthest outside (lowest)
        buy_prices  = _linspace(buy_low, center, levels_per_side + 1)[:-1]
        # sell_prices ascending → index -1 = furthest outside (highest)
        sell_prices = _linspace(center, sell_high, levels_per_side + 1)[1:]

        # State per level: resting=True means grid limit active; tp=float means TP pending.
        # Entry and TP are mutually exclusive states, so at most one fill per level per candle.
        buy_state  = [{"resting": True, "tp": None} for _ in buy_prices]
        sell_state = [{"resting": True, "tp": None} for _ in sell_prices]

        # ── Simulation ─────────────────────────────────────────────────────────
        fee_rate       = config.fee_pct / 100
        cash           = config.capital_quote
        inventory_base = 0.0
        fees_paid      = 0.0
        round_trips    = 0
        max_equity     = config.capital_quote
        max_drawdown   = 0.0
        daily_equity: dict = {}         # last equity seen per UTC date

        for candle in candles:
            lo    = candle["low"]
            hi    = candle["high"]
            close = candle["close"]

            # ── Buy levels: outside-in = ascending (lowest price first) ────────
            for j in range(len(buy_prices)):
                st = buy_state[j]
                bp = buy_prices[j]

                if st["resting"]:
                    if lo < bp:                      # entry buy fills
                        notional = bp * base_qty
                        cash -= notional * (1 + fee_rate)
                        inventory_base += base_qty
                        fees_paid += notional * fee_rate
                        st["resting"] = False
                        st["tp"] = bp * (1 + config.tp_pct / 100)
                elif st["tp"] is not None:
                    if hi > st["tp"]:                # TP sell fills (later candle guaranteed)
                        notional = st["tp"] * base_qty
                        cash += notional * (1 - fee_rate)
                        inventory_base -= base_qty
                        fees_paid += notional * fee_rate
                        round_trips += 1
                        st["resting"] = True
                        st["tp"] = None

            # ── Sell levels: outside-in = descending (highest price first) ─────
            for j in range(len(sell_prices) - 1, -1, -1):
                st = sell_state[j]
                sp = sell_prices[j]

                if st["resting"]:
                    if hi > sp:                      # entry sell fills
                        notional = sp * base_qty
                        cash += notional * (1 - fee_rate)
                        inventory_base -= base_qty
                        fees_paid += notional * fee_rate
                        st["resting"] = False
                        st["tp"] = sp * (1 - config.tp_pct / 100)
                elif st["tp"] is not None:
                    if lo < st["tp"]:                # TP buy fills (later candle guaranteed)
                        notional = st["tp"] * base_qty
                        cash -= notional * (1 + fee_rate)
                        inventory_base += base_qty
                        fees_paid += notional * fee_rate
                        round_trips += 1
                        st["resting"] = True
                        st["tp"] = None

            # ── Equity / drawdown ──────────────────────────────────────────────
            equity = cash + inventory_base * close
            if equity > max_equity:
                max_equity = equity
            dd = max_equity - equity
            if dd > max_drawdown:
                max_drawdown = dd

            ts = candle["timestamp"]
            ts_sec = ts / 1000 if ts > 1e10 else ts
            day = datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime("%Y-%m-%d")
            daily_equity[day] = equity

        # ── Final mark-to-market ───────────────────────────────────────────────
        last_close          = candles[-1]["close"]
        final_equity        = cash + inventory_base * last_close
        open_inventory_quote = inventory_base * last_close
        net_pnl             = final_equity - config.capital_quote
        gross_pnl           = net_pnl + fees_paid

        # ── Per-day PnL table ──────────────────────────────────────────────────
        day_list   = sorted(daily_equity.keys())
        prev_eq    = config.capital_quote
        per_day_rows = []
        for day in day_list:
            eq      = daily_equity[day]
            day_pnl = eq - prev_eq
            per_day_rows.append({
                "Date":       day,
                "End Equity": f"${eq:.2f}",
                "Day PnL":    f"${day_pnl:+.2f}",
                "Cum PnL":    f"${eq - config.capital_quote:+.2f}",
            })
            prev_eq = eq

        # ── Report ─────────────────────────────────────────────────────────────
        try:
            builder = ReportBuilder(f"Grid backtest {config.trading_pair} {config.days}d")
            builder.source("routine", "grid_backtest").tags(["backtest", "grid", config.trading_pair])
            builder.kpi("Net PnL (after fees)",  f"${net_pnl:+.2f}")
            builder.kpi("Gross PnL",             f"${gross_pnl:+.2f}")
            builder.kpi("Round Trips",           str(round_trips))
            builder.kpi("Open Inventory (quote)",f"${open_inventory_quote:.2f}")
            builder.kpi("Max Drawdown",          f"${max_drawdown:.2f}")
            builder.kpi("Fees Paid",             f"${fees_paid:.2f}")
            builder.kpi("Window",                f"{config.days}d")
            builder.kpi("Resolution",            config.interval)
            builder.kpi("Candles",               str(n_candles))
            builder.kpi("Center Price",          f"${center:.4f}")
            builder.table(per_day_rows, ["Date", "End Equity", "Day PnL", "Cum PnL"])
            builder.manual_order()
            await builder.save()
        except Exception as e:
            logger.warning(f"Report generation failed: {e}")

        return (
            f"Grid backtest {config.trading_pair} | {config.days}d @ {config.interval}\n"
            f"Center: ${center:.4f} | Band: ±{config.band_pct/2:.1f}% | {levels_per_side}+{levels_per_side} grid\n"
            f"Net PnL: ${net_pnl:+.2f} | Gross PnL: ${gross_pnl:+.2f} | Fees: ${fees_paid:.2f}\n"
            f"Round trips: {round_trips} | Open inventory: ${open_inventory_quote:.2f}\n"
            f"Max drawdown: ${max_drawdown:.2f} | Candles: {n_candles}"
        )

    except Exception as e:
        logger.exception("grid_backtest error")
        return f"Error: {e}"

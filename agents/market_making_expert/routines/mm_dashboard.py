"""MM Dashboard: unified portfolio inventory + bot positions + controller performance.

Consolidates three former routines into one:
  • portfolio_scanner    → Portfolio / inventory section (get_state + get_total_value)
  • mm_bot_report        → Bots / positions / PnL section (bot_orchestration.get_active_bots_status)
  • bot_position_tracker → superseded (its executor-search grouping is replaced by the
                            strictly-richer bot_orchestration path used here)

Bot data comes from client.bot_orchestration.get_active_bots_status():
  bots_data[bot_name]["performance"][ctrl_name]["performance"]
    → realized_pnl_quote, unrealized_pnl_quote, volume_traded
    → positions_summary  (list of open positions with pair/side/amount/breakeven)
    → close_type_counts  (CloseType.XXX → count)
  bots_data[bot_name]["error_logs"]  → errors
"""
import logging
from collections import Counter
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Bot Analysis"

_CLOSE_LABELS = {
    "TAKE_PROFIT": "TP",
    "STOP_LOSS": "SL",
    "TRAILING_STOP": "Trail",
    "EARLY_STOP": "Early",
    "POSITION_HOLD": "Hold",
    "HOLD": "Hold",
    "EXPIRED": "Expired",
    "INSUFFICIENT_BALANCE": "Insuf$",
    "FAILED": "Failed",
    "UNKNOWN": "?",
}

_ERROR_LEVELS = {"ERROR", "CRITICAL", "FATAL"}


def _close_label(raw: str) -> str:
    """Convert 'CloseType.EARLY_STOP' → 'Early'."""
    code = raw.split(".")[-1] if "." in raw else raw
    return _CLOSE_LABELS.get(code.upper(), code)


def _side_label(raw: str) -> str:
    """Convert 'TradeType.BUY' → 'BUY'."""
    return raw.split(".")[-1] if "." in raw else raw


class Config(BaseModel):
    """Unified MM dashboard: portfolio inventory, bot positions, PnL, and errors."""
    connector_name: str = Field(default="binance_perpetual", description="Focus connector for portfolio (empty=all)")
    trading_pair: str = Field(default="", description="Filter bot positions by pair (empty = all)")
    min_value_usd: float = Field(default=1.0, description="Hide tokens below this USD value")
    include_errors: bool = Field(default=True, description="Include error log summary")


async def _fetch_portfolio(client, connector_name: str, min_value_usd: float):
    """Scan portfolio inventory. Returns (inv_rows, total_value, error_msg, summary_lines)."""
    state = None
    errors = []
    try:
        state = await client.portfolio.get_state()
    except Exception as e:
        errors.append(f"get_state: {e}")

    if not state or not isinstance(state, dict):
        try:
            state = await client.portfolio.get_portfolio_state()
        except Exception as e:
            errors.append(f"get_portfolio_state: {e}")

    if not state or not isinstance(state, dict):
        return [], None, " | ".join(errors) or "No portfolio state available", []

    inv_rows = []
    summary_lines = []
    for acct_name, acct_data in state.items():
        if not isinstance(acct_data, dict):
            continue
        for conn_name, tokens in acct_data.items():
            if connector_name and connector_name not in conn_name:
                continue
            if not isinstance(tokens, list):
                continue

            significant = [
                t for t in tokens
                if isinstance(t, dict) and float(t.get("value", 0)) >= min_value_usd
            ]
            if not significant:
                continue

            significant.sort(key=lambda t: float(t.get("value", 0)), reverse=True)
            total_value = sum(float(t.get("value", 0)) for t in significant)

            summary_lines.append(f"**{acct_name} / {conn_name}**")
            for t in significant:
                token = t.get("token", "?")
                units = float(t.get("units", 0))
                value = float(t.get("value", 0))
                available = float(t.get("available_units", units))
                pct = (value / total_value * 100) if total_value > 0 else 0
                in_use = units - available
                use_flag = f" (in_use: {in_use:.4f})" if in_use > 0.001 else ""
                summary_lines.append(f"  {token}: {units:,.4f} = ${value:,.2f} ({pct:.1f}%){use_flag}")
                inv_rows.append({
                    "Account": acct_name,
                    "Connector": conn_name,
                    "Token": token,
                    "Units": round(units, 4),
                    "Value (USD)": f"${value:,.2f}",
                    "Weight": f"{pct:.1f}%",
                    "In Use": f"{in_use:.4f}" if in_use > 0.001 else "-",
                })
            summary_lines.append(f"  Subtotal: ${total_value:,.2f}")
            summary_lines.append("")

    total_val = None
    try:
        tv = await client.portfolio.get_total_value()
        if tv:
            total_val = float(tv)
    except Exception:
        pass
    if total_val is None and inv_rows:
        total_val = sum(
            float(r["Value (USD)"].replace("$", "").replace(",", ""))
            for r in inv_rows
        )

    return inv_rows, total_val, None, summary_lines


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    # ── 1. Portfolio / Inventory ─────────────────────────────────────────────
    inv_rows, total_value, portfolio_error, portfolio_summary = await _fetch_portfolio(
        client, config.connector_name, config.min_value_usd
    )

    # ── 2. Bots / Positions / PnL (bot_orchestration — richer source) ────────
    bots_data: dict = {}
    bots_error = None
    try:
        resp = await client.bot_orchestration.get_active_bots_status()
        raw = resp if isinstance(resp, dict) else {}
        bots_data = raw.get("data", raw) if isinstance(raw, dict) else {}
        if not isinstance(bots_data, dict):
            bots_data = {}
    except Exception as e:
        bots_error = f"Failed to fetch bot status: {e}"

    ctrl_rows = []       # per-controller perf table
    pos_rows = []        # open positions table
    close_totals: Counter = Counter()
    total_realized = total_unrealized = total_volume = 0.0
    total_errors = 0
    error_lines = []

    for bot_name, bot_data in bots_data.items():
        if not isinstance(bot_data, dict):
            continue

        # Error logs
        if config.include_errors:
            errs = [
                e for e in (bot_data.get("error_logs") or [])
                if isinstance(e, dict)
                and str(e.get("level_name", "")).upper() in _ERROR_LEVELS
            ]
            if errs:
                total_errors += len(errs)
                error_lines.append(f"  • {bot_name}: {len(errs)} error(s)")

        perf_dict = bot_data.get("performance", {})
        if not isinstance(perf_dict, dict):
            continue

        for ctrl_name, ctrl_data in perf_dict.items():
            if not isinstance(ctrl_data, dict):
                continue
            inner = ctrl_data.get("performance", ctrl_data)
            if not isinstance(inner, dict):
                continue

            # Parse open positions to get pair/connector for filtering
            positions = inner.get("positions_summary") or []
            pair = ""
            connector = ""
            if positions and isinstance(positions, list) and isinstance(positions[0], dict):
                pair = positions[0].get("trading_pair", "")
                connector = positions[0].get("connector_name", "")

            # Filter by trading pair (empty = all controllers)
            if config.trading_pair and config.trading_pair not in pair:
                continue

            realized = float(inner.get("realized_pnl_quote", 0) or 0)
            unrealized = float(inner.get("unrealized_pnl_quote", 0) or 0)
            volume = float(inner.get("volume_traded", 0) or 0)
            total_realized += realized
            total_unrealized += unrealized
            total_volume += volume

            # Close type breakdown
            close_counts: dict = inner.get("close_type_counts", {}) or {}
            ctrl_closes: Counter = Counter()
            for raw_ct, cnt in close_counts.items():
                label = _close_label(str(raw_ct))
                ctrl_closes[label] += int(cnt)
            close_totals.update(ctrl_closes)
            close_str = " | ".join(f"{k}:{v}" for k, v in ctrl_closes.most_common()) or "—"

            ctrl_rows.append({
                "Controller": ctrl_name,
                "Pair": pair or "—",
                "Realized PnL": f"${realized:,.4f}",
                "Unrealized PnL": f"${unrealized:,.4f}",
                "Volume": f"${volume:,.2f}",
                "Closes": close_str,
            })

            # Open positions
            for pos in positions:
                if not isinstance(pos, dict):
                    continue
                pos_pair = pos.get("trading_pair", pair)
                pos_conn = pos.get("connector_name", connector)
                side = _side_label(str(pos.get("side", "")))
                amount = float(pos.get("amount", 0) or 0)
                bp = float(pos.get("breakeven_price", 0) or 0)
                pos_unreal = float(pos.get("unrealized_pnl_quote", 0) or 0)
                pos_real = float(pos.get("realized_pnl_quote", 0) or 0)
                pos_rows.append({
                    "Controller": ctrl_name,
                    "Pair": pos_pair,
                    "Connector": pos_conn,
                    "Side": side,
                    "Amount": f"{amount:,.2f}",
                    "Breakeven": f"${bp:,.6f}",
                    "Unrealized PnL": f"${pos_unreal:,.4f}",
                    "Realized PnL": f"${pos_real:,.4f}",
                })

    net_pnl = total_realized + total_unrealized

    # ── Summary text ─────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    close_summary = " | ".join(f"{k}:{v}" for k, v in close_totals.most_common()) or "none"

    lines = [f"**MM Dashboard** — {now}", ""]

    # Portfolio block
    if portfolio_error and not inv_rows:
        lines.append(f"Portfolio: {portfolio_error}")
    else:
        total_str = f"${total_value:,.2f}" if total_value else "unknown"
        lines.append(f"Portfolio total: {total_str} | {len(inv_rows)} asset(s) on {config.connector_name or 'all'}")
    if portfolio_summary:
        lines.append("")
        lines.extend(portfolio_summary)

    # Bots block
    if bots_error:
        lines.append(bots_error)
    else:
        lines.append(f"Controllers: {len(ctrl_rows)} | Open positions: {len(pos_rows)}")
        lines.append(f"Realized PnL: ${total_realized:,.4f} | Unrealized: ${total_unrealized:,.4f} | Net: ${net_pnl:,.4f}")
        lines.append(f"Volume: ${total_volume:,.2f}")
        lines.append(f"Closes: {close_summary}")

    if config.include_errors and not bots_error:
        if total_errors:
            lines.append("")
            lines.append(f"⚠️ {total_errors} error(s) across active bots:")
            lines.extend(error_lines[:8])
        else:
            lines.append("")
            lines.append("✅ No errors in active bot logs")

    summary = "\n".join(lines)

    # ── Persistent report ─────────────────────────────────────────────────────
    try:
        from condor.reports import ReportBuilder
        builder = ReportBuilder("MM Dashboard")
        builder.source("routine", "mm_dashboard").tags(
            ["portfolio", "bots", "positions", "market-making"]
        )

        if total_value:
            builder.kpi("Portfolio Value", f"${total_value:,.2f}")
        builder.kpi("Active Controllers", str(len(ctrl_rows)))
        builder.kpi("Open Positions", str(len(pos_rows)))
        builder.kpi("Realized PnL", f"${total_realized:,.4f}")
        builder.kpi("Unrealized PnL", f"${total_unrealized:,.4f}")
        builder.kpi("Net PnL", f"${net_pnl:,.4f}")
        builder.kpi("Volume", f"${total_volume:,.2f}")
        if config.include_errors:
            builder.kpi("Errors", str(total_errors))

        if pos_rows:
            builder.table(pos_rows, ["Controller", "Pair", "Connector", "Side", "Amount", "Breakeven", "Unrealized PnL", "Realized PnL"])
        if ctrl_rows:
            builder.table(ctrl_rows, ["Controller", "Pair", "Realized PnL", "Unrealized PnL", "Volume", "Closes"])
        if inv_rows:
            builder.table(inv_rows, ["Account", "Connector", "Token", "Units", "Value (USD)", "Weight", "In Use"])
        elif portfolio_error:
            builder.markdown(f"**Portfolio unavailable**: {portfolio_error}")

        builder.markdown(summary)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return summary

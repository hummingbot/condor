"""
Morning Brief — Daily portfolio snapshot with action recommendations.

Runs once per day at a configurable time. Fetches:
  - CEX account balances
  - On-chain wallet balances (Gateway)
  - Open LP positions (with OOR/fee status)
  - Active bots (running/stopped with P&L)

Then surfaces the top actions to take: collect fees, rebalance OOR
positions, restart stopped bots, or review underperformers.
"""

import asyncio
import logging
import time
from datetime import datetime

from pydantic import BaseModel, Field
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config_manager import get_client
from utils.telegram_formatters import (
    KNOWN_TOKENS,
    escape_markdown_v2,
    resolve_token_symbol,
)

logger = logging.getLogger(__name__)

# One-shot daily routine — the scheduler calls run() at the configured time
CONTINUOUS = False


class Config(BaseModel):
    """Daily morning portfolio brief with action recommendations."""

    run_hour: int = Field(
        default=8,
        ge=0,
        le=23,
        description="Hour to run the brief (0-23, local time)",
    )
    run_minute: int = Field(
        default=0,
        ge=0,
        le=59,
        description="Minute to run the brief (0-59)",
    )
    fee_alert_usd: float = Field(
        default=5.0,
        description="Alert if pending LP fees exceed this USD amount",
    )
    include_bots: bool = Field(
        default=True,
        description="Include active/stopped bots in the brief",
    )
    include_lp: bool = Field(
        default=True,
        description="Include LP positions in the brief",
    )
    include_cex: bool = Field(
        default=True,
        description="Include CEX balances in the brief",
    )
    include_wallet: bool = Field(
        default=True,
        description="Include on-chain wallet balances in the brief",
    )

    @property
    def schedule_daily(self) -> str:
        """Cron expression for APScheduler."""
        return f"{self.run_minute} {self.run_hour} * * *"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_price(price: float) -> str:
    if price == 0:
        return "0.00"
    if price >= 100:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:.2f}"
    if price >= 0.001:
        return f"{price:.4f}"
    return f"{price:.8f}"


def _fmt_usd(value: float) -> str:
    if abs(value) >= 1000:
        return f"${value:,.0f}"
    return f"${value:.2f}"


def _pnl_str(pnl: float) -> str:
    sign = "\\+" if pnl >= 0 else "\\-"
    return f"{sign}{_fmt_usd(abs(pnl))}"


async def _fetch_token_prices(client) -> dict:
    """Pull token prices from the portfolio state."""
    prices = {}
    try:
        result = await client.portfolio.get_state()
        if result:
            for account_data in result.values():
                for balances in account_data.values():
                    if balances:
                        for b in balances:
                            token = b.get("token") or b.get("asset")
                            price = b.get("price") or b.get("usd_price")
                            if token and price:
                                prices[token.upper()] = float(price)
    except Exception as e:
        logger.debug(f"Token price fetch failed: {e}")
    return prices


def _get_price(symbol: str, prices: dict) -> float:
    sym = symbol.upper()
    if sym in prices:
        return prices[sym]
    # Wrapped variants
    variants = {"SOL": "WSOL", "WSOL": "SOL", "ETH": "WETH", "WETH": "ETH"}
    alt = variants.get(sym)
    if alt and alt in prices:
        return prices[alt]
    # Stables default to 1.0
    if sym in ("USDC", "USDT", "DAI", "BUSD", "TUSD"):
        return 1.0
    return 0.0


# ---------------------------------------------------------------------------
# Section: CEX + Wallet Balances
# ---------------------------------------------------------------------------

async def _fetch_balances(client) -> tuple[float, list[str]]:
    """Returns (total_usd, formatted_lines)."""
    lines = []
    total_usd = 0.0

    try:
        state = await client.portfolio.get_state()
        if not state:
            return 0.0, ["_No balance data available_"]

        for account_name, connectors in state.items():
            for connector_name, holdings in connectors.items():
                if not holdings:
                    continue

                connector_total = 0.0
                connector_lines = []

                for h in holdings:
                    token = h.get("token") or h.get("asset", "")
                    amount = float(h.get("total") or h.get("balance") or 0)
                    price = float(h.get("price") or h.get("usd_price") or 0)
                    value = amount * price

                    if value < 0.50:  # Skip dust
                        continue

                    connector_total += value
                    if token:
                        connector_lines.append(
                            f"    {escape_markdown_v2(token.upper())}: "
                            f"{escape_markdown_v2(_fmt_price(amount))} "
                            f"\\({escape_markdown_v2(_fmt_usd(value))}\\)"
                        )

                if connector_total >= 0.50:
                    total_usd += connector_total
                    lines.append(
                        f"  *{escape_markdown_v2(connector_name.capitalize())}* "
                        f"— {escape_markdown_v2(_fmt_usd(connector_total))}"
                    )
                    lines.extend(connector_lines[:5])  # Cap at 5 tokens per connector

    except Exception as e:
        logger.error(f"Balance fetch error: {e}")
        lines = [f"_Error fetching balances: {escape_markdown_v2(str(e)[:80])}_"]

    return total_usd, lines


# ---------------------------------------------------------------------------
# Section: LP Positions
# ---------------------------------------------------------------------------

async def _fetch_lp_positions(client, fee_alert_usd: float, prices: dict) -> tuple[float, float, list[str], list[dict]]:
    """
    Returns (total_value_usd, total_fees_usd, formatted_lines, action_positions).
    action_positions: positions that need attention (OOR or collectible fees).
    """
    lines = []
    total_value = 0.0
    total_fees = 0.0
    action_positions = []

    try:
        result = await client.gateway_clmm.search_positions(
            limit=100, offset=0, status="OPEN", refresh=True
        )
        if not result:
            return 0.0, 0.0, ["_No LP positions_"], []

        positions = [
            p for p in result.get("data", [])
            if p.get("status") != "CLOSED"
            and float(p.get("liquidity", p.get("current_liquidity", 1)) or 1) > 0
        ]

        if not positions:
            return 0.0, 0.0, ["_No active LP positions_"], []

        token_cache = dict(KNOWN_TOKENS)

        in_range = 0
        oor = 0

        for pos in positions:
            base_token = pos.get("base_token", pos.get("token_a", ""))
            quote_token = pos.get("quote_token", pos.get("token_b", ""))
            base_sym = resolve_token_symbol(base_token, token_cache)
            quote_sym = resolve_token_symbol(quote_token, token_cache)
            pair = f"{base_sym}\\-{quote_sym}"

            status = pos.get("in_range", "")
            status_icon = "🟢" if status == "IN_RANGE" else "🔴"

            if status == "IN_RANGE":
                in_range += 1
            else:
                oor += 1

            # Value
            pnl_summary = pos.get("pnl_summary", {})
            quote_price = _get_price(quote_sym, prices) or 1.0
            base_price = _get_price(base_sym, prices)

            value_usd = float(pnl_summary.get("current_lp_value_quote", 0) or 0) * quote_price
            pnl_usd = float(pnl_summary.get("total_pnl_quote", 0) or 0) * quote_price

            base_fee = float(pos.get("base_fee_pending", 0) or 0)
            quote_fee = float(pos.get("quote_fee_pending", 0) or 0)
            fees_usd = (base_fee * base_price) + (quote_fee * quote_price)

            total_value += value_usd
            total_fees += fees_usd

            # Flag for action
            needs_action = status == "OUT_OF_RANGE" or fees_usd >= fee_alert_usd
            if needs_action:
                action_positions.append(pos)

            fee_str = f" 🎁 {escape_markdown_v2(_fmt_usd(fees_usd))}" if fees_usd >= 0.5 else ""
            pnl_str = _pnl_str(pnl_usd) if abs(pnl_usd) >= 0.01 else ""

            connector = pos.get("connector", "").capitalize()
            lines.append(
                f"  {status_icon} *{escape_markdown_v2(pair)}* \\({escape_markdown_v2(connector)}\\) "
                f"{escape_markdown_v2(_fmt_usd(value_usd))}"
                + (f" \\| PnL: {pnl_str}" if pnl_str else "")
                + fee_str
            )

        # Summary line
        summary = f"  {in_range} in range"
        if oor:
            summary += f" \\| 🔴 {oor} out of range"
        if total_fees >= 0.5:
            summary += f" \\| 🎁 {escape_markdown_v2(_fmt_usd(total_fees))} pending fees"
        lines.insert(0, summary)

    except Exception as e:
        logger.error(f"LP position fetch error: {e}")
        lines = [f"_Error fetching LP positions: {escape_markdown_v2(str(e)[:80])}_"]

    return total_value, total_fees, lines, action_positions


# ---------------------------------------------------------------------------
# Section: Active Bots
# ---------------------------------------------------------------------------

async def _fetch_bots(client) -> tuple[list[str], list[dict]]:
    """Returns (formatted_lines, stopped_bots_needing_attention)."""
    lines = []
    stopped = []

    try:
        result = await client.bots.get_bots()
        if not result:
            return ["_No bots configured_"], []

        bots = result if isinstance(result, list) else result.get("data", [])
        if not bots:
            return ["_No bots found_"], []

        running = [b for b in bots if b.get("status") == "running"]
        not_running = [b for b in bots if b.get("status") != "running"]

        for b in running:
            name = escape_markdown_v2(b.get("bot_name", b.get("id", "?")))
            strategy = escape_markdown_v2(b.get("strategy", b.get("controller", "")))
            lines.append(f"  🟢 *{name}* — {strategy}")

        for b in not_running:
            name = escape_markdown_v2(b.get("bot_name", b.get("id", "?")))
            strategy = escape_markdown_v2(b.get("strategy", b.get("controller", "")))
            lines.append(f"  ⚫ *{name}* — {strategy}")
            stopped.append(b)

        if not lines:
            lines = ["_No bots_"]

    except Exception as e:
        logger.error(f"Bot fetch error: {e}")
        lines = [f"_Error fetching bots: {escape_markdown_v2(str(e)[:80])}_"]

    return lines, stopped


# ---------------------------------------------------------------------------
# Action Recommendations
# ---------------------------------------------------------------------------

def _build_recommendations(
    oor_positions: list,
    high_fee_positions: list,
    stopped_bots: list,
    total_fees: float,
    fee_alert_usd: float,
) -> list[str]:
    recs = []

    if oor_positions:
        pairs = []
        for pos in oor_positions[:3]:
            token_cache = dict(KNOWN_TOKENS)
            base_sym = resolve_token_symbol(pos.get("base_token", ""), token_cache)
            quote_sym = resolve_token_symbol(pos.get("quote_token", ""), token_cache)
            pairs.append(f"{base_sym}\\-{quote_sym}")
        pairs_str = ", ".join(pairs)
        recs.append(f"🔴 {len(oor_positions)} position\\(s\\) out of range: {pairs_str}")
        recs.append("   → Consider closing or rebalancing via /lp")

    if total_fees >= fee_alert_usd:
        recs.append(f"🎁 {escape_markdown_v2(_fmt_usd(total_fees))} in uncollected LP fees")
        recs.append("   → Collect via /lp → Collect All Fees")

    if stopped_bots:
        names = [escape_markdown_v2(b.get("bot_name", b.get("id", "?"))) for b in stopped_bots[:2]]
        recs.append(f"⚫ {len(stopped_bots)} bot\\(s\\) not running: {', '.join(names)}")
        recs.append("   → Restart via /bots")

    if not recs:
        recs.append("✅ Everything looks good — no immediate actions needed")

    return recs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Fetch portfolio snapshot and send morning brief."""
    chat_id = getattr(context, "_chat_id", None)
    if not chat_id:
        return "No chat_id"

    client = await get_client(chat_id, context=context)
    if not client:
        return "No Hummingbot server available"

    now = datetime.now().strftime("%a, %b %d · %I:%M %p")

    # Send a "loading" message
    try:
        loading_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"☀️ *Morning Brief* — {escape_markdown_v2(now)}\n\n_Fetching data\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error(f"Failed to send loading message: {e}")
        return f"Failed to send message: {e}"

    # Fetch token prices first (needed for LP value calculations)
    prices = await _fetch_token_prices(client)

    async def _noop_balances():
        return (0.0, [])

    async def _noop_lp():
        return (0.0, 0.0, [], [])

    async def _noop_bots():
        return ([], [])

    results = await asyncio.gather(
        _fetch_balances(client) if (config.include_cex or config.include_wallet) else _noop_balances(),
        _fetch_lp_positions(client, config.fee_alert_usd, prices) if config.include_lp else _noop_lp(),
        _fetch_bots(client) if config.include_bots else _noop_bots(),
        return_exceptions=True,
    )

    # Unpack
    balance_result = results[0] if not isinstance(results[0], Exception) else (0.0, ["_Error fetching balances_"])
    lp_result = results[1] if not isinstance(results[1], Exception) else (0.0, 0.0, ["_Error fetching LP positions_"], [])
    bot_result = results[2] if not isinstance(results[2], Exception) else (["_Error fetching bots_"], [])

    total_balance_usd, balance_lines = balance_result
    total_lp_usd, total_fees_usd, lp_lines, action_positions = lp_result
    bot_lines, stopped_bots = bot_result

    # Separate OOR and high-fee positions for recommendations
    oor_positions = [p for p in action_positions if p.get("in_range") == "OUT_OF_RANGE"]
    high_fee_positions = [p for p in action_positions if p.get("in_range") != "OUT_OF_RANGE"]

    # Total portfolio value
    grand_total = total_balance_usd + total_lp_usd

    # Build message
    sections = []

    # Header
    sections.append(f"☀️ *Morning Brief*\n{escape_markdown_v2(now)}")
    sections.append(f"━━━━━━━━━━━━━━━━━━━━━")
    sections.append(f"💼 *Total Portfolio: {escape_markdown_v2(_fmt_usd(grand_total))}*")
    sections.append("")

    # Balances
    if (config.include_cex or config.include_wallet) and balance_lines:
        sections.append(f"🏦 *Balances* — {escape_markdown_v2(_fmt_usd(total_balance_usd))}")
        sections.extend(balance_lines[:8])  # Cap to keep message concise
        sections.append("")

    # LP Positions
    if config.include_lp and lp_lines:
        sections.append(f"💧 *LP Positions* — {escape_markdown_v2(_fmt_usd(total_lp_usd))}")
        sections.extend(lp_lines)
        sections.append("")

    # Bots
    if config.include_bots and bot_lines:
        sections.append("🤖 *Bots*")
        sections.extend(bot_lines)
        sections.append("")

    # Recommendations
    sections.append("📋 *Recommended Actions*")
    recs = _build_recommendations(
        oor_positions,
        high_fee_positions,
        stopped_bots,
        total_fees_usd,
        config.fee_alert_usd,
    )
    sections.extend(recs)

    text = "\n".join(sections)

    # Action buttons
    keyboard = []
    if config.include_lp:
        keyboard.append([InlineKeyboardButton("💧 LP Positions", callback_data="dex:lp_menu")])
    if config.include_bots:
        keyboard.append([InlineKeyboardButton("🤖 Bots", callback_data="bots:menu")])
    keyboard.append([InlineKeyboardButton("📊 Full Portfolio", callback_data="portfolio:view")])

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=loading_msg.message_id,
            text=text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
        )
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")
        # Fallback: send as new message
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
            )
        except Exception as e2:
            return f"Failed to deliver brief: {e2}"

    total_str = _fmt_usd(grand_total)
    oor_str = f", {len(oor_positions)} OOR" if oor_positions else ""
    fees_str = f", {_fmt_usd(total_fees_usd)} fees" if total_fees_usd >= 1 else ""
    return f"Delivered: {total_str} portfolio{oor_str}{fees_str}"

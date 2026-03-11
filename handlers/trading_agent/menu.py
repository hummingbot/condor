"""Trading Agent menu screens."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2

from ._shared import (
    TA_CONFIG_PARAMS,
    TA_SELECTED_AGENT,
    TA_SELECTED_STRATEGY,
    TA_STATE_KEY,
    format_agent_status,
    format_strategy_summary,
)

logger = logging.getLogger(__name__)


async def show_main_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the trading agent main menu."""
    from condor.trading_agent.engine import get_all_engines
    from condor.trading_agent.strategy import StrategyStore

    user_id = update_or_query.from_user.id if hasattr(update_or_query, "from_user") else 0
    store = StrategyStore()
    strategies = store.list_all(user_id=user_id)

    # Count running agents for this chat
    chat_id = update_or_query.message.chat_id if hasattr(update_or_query, "message") else 0
    if hasattr(update_or_query, "effective_chat"):
        chat_id = update_or_query.effective_chat.id
    engines = get_all_engines()
    running_count = sum(1 for e in engines.values() if e.chat_id == chat_id and e.is_running)

    text = "🤖 *Trading Agents*\n\n"
    text += f"Strategies: {len(strategies)}\n"
    text += f"Running agents: {running_count}"

    keyboard = [
        [InlineKeyboardButton("📋 My Strategies", callback_data="ta:strategies")],
        [InlineKeyboardButton("🤖 Running Agents", callback_data="ta:running")],
        [InlineKeyboardButton("❌ Close", callback_data="ta:close")],
    ]

    text_escaped = escape_markdown_v2(text)
    markup = InlineKeyboardMarkup(keyboard)

    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text_escaped, parse_mode="MarkdownV2", reply_markup=markup)
    elif hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(text_escaped, parse_mode="MarkdownV2", reply_markup=markup)


async def show_strategies(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of strategies."""
    from condor.trading_agent.strategy import StrategyStore

    user_id = query.from_user.id
    store = StrategyStore()
    strategies = store.list_all(user_id=user_id)

    if not strategies:
        text = "No strategies yet\\.\n\nUse the AI agent \\(/agent\\) to create one conversationally\\."
        keyboard = [[InlineKeyboardButton("« Back", callback_data="ta:menu")]]
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard = []
    for s in strategies:
        keyboard.append([InlineKeyboardButton(f"📋 {s.name}", callback_data=f"ta:strat:{s.id}")])
    keyboard.append([InlineKeyboardButton("« Back", callback_data="ta:menu")])

    text = escape_markdown_v2(f"📋 Your Strategies ({len(strategies)})")
    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_strategy_detail(query, context: ContextTypes.DEFAULT_TYPE, strategy_id: str) -> None:
    """Show strategy detail with start/delete options."""
    from condor.trading_agent.strategy import StrategyStore

    store = StrategyStore()
    strategy = store.get(strategy_id)
    if not strategy:
        await query.edit_message_text("Strategy not found.")
        return

    context.user_data[TA_SELECTED_STRATEGY] = strategy_id
    text = escape_markdown_v2(format_strategy_summary(strategy))

    keyboard = [
        [InlineKeyboardButton("▶️ Start Agent", callback_data=f"ta:start:{strategy_id}")],
        [
            InlineKeyboardButton("🗑 Delete", callback_data=f"ta:delete:{strategy_id}"),
            InlineKeyboardButton("« Back", callback_data="ta:strategies"),
        ],
    ]

    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_start_config(query, context: ContextTypes.DEFAULT_TYPE, strategy_id: str) -> None:
    """Show config editor before starting an agent."""
    from condor.trading_agent.strategy import StrategyStore

    store = StrategyStore()
    strategy = store.get(strategy_id)
    if not strategy:
        await query.edit_message_text("Strategy not found.")
        return

    config = dict(strategy.default_config)
    # Ensure essential keys — use chat's effective server as default
    if "server_name" not in config:
        from config_manager import get_effective_server
        chat_id = query.message.chat_id if query.message else 0
        effective = get_effective_server(chat_id)
        if effective:
            config["server_name"] = effective
    config.setdefault("connector_name", "binance_perpetual")
    config.setdefault("trading_pair", "BTC-USDT")
    config.setdefault("frequency_sec", 60)
    config.setdefault("risk_limits", {})
    config["risk_limits"].setdefault("max_position_size_quote", 500)
    config["risk_limits"].setdefault("max_daily_loss_quote", 50)
    config["risk_limits"].setdefault("max_open_executors", 5)

    context.user_data[TA_CONFIG_PARAMS] = config
    context.user_data[TA_SELECTED_STRATEGY] = strategy_id
    context.user_data[TA_STATE_KEY] = "editing_config"

    await _show_config_editor(query, context, strategy.name, config, strategy_id)


async def _show_config_editor(query, context, strategy_name: str, config: dict, strategy_id: str) -> None:
    """Display the config editor."""
    lines = [f"⚙️ Config for {strategy_name}", ""]
    for key, value in config.items():
        if key == "risk_limits":
            lines.append("risk_limits:")
            for rk, rv in value.items():
                lines.append(f"  {rk}={rv}")
        else:
            lines.append(f"  {key}={value}")
    lines.append("")
    lines.append("Send key=value to edit (e.g. trading_pair=SOL-USDT)")

    text = escape_markdown_v2("\n".join(lines))

    keyboard = [
        [InlineKeyboardButton("🚀 Start", callback_data=f"ta:launch:{strategy_id}")],
        [InlineKeyboardButton("« Back", callback_data=f"ta:strat:{strategy_id}")],
    ]

    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_running_agents(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of running agents."""
    from condor.trading_agent.engine import get_all_engines

    chat_id = query.message.chat_id
    engines = get_all_engines()
    agents = [e for e in engines.values() if e.chat_id == chat_id]

    if not agents:
        text = "No running agents\\."
        keyboard = [[InlineKeyboardButton("« Back", callback_data="ta:menu")]]
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard = []
    for engine in agents:
        info = engine.get_info()
        emoji = {"running": "🟢", "paused": "⏸"}.get(info["status"], "🔴")
        label = f"{emoji} {info['strategy']} (${info['daily_pnl']:+.2f})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"ta:agent:{engine.agent_id}")])
    keyboard.append([InlineKeyboardButton("« Back", callback_data="ta:menu")])

    text = escape_markdown_v2(f"🤖 Running Agents ({len(agents)})")
    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_agent_dashboard(query, context: ContextTypes.DEFAULT_TYPE, agent_id: str) -> None:
    """Show agent dashboard with controls."""
    from condor.trading_agent.engine import get_engine

    engine = get_engine(agent_id)
    if not engine:
        await query.edit_message_text("Agent not found or stopped.")
        return

    context.user_data[TA_SELECTED_AGENT] = agent_id
    info = engine.get_info()
    text = escape_markdown_v2(format_agent_status(info))

    # Build control buttons
    controls = []
    if engine.is_paused:
        controls.append(InlineKeyboardButton("▶️ Resume", callback_data=f"ta:resume:{agent_id}"))
    elif engine.is_running:
        controls.append(InlineKeyboardButton("⏸ Pause", callback_data=f"ta:pause:{agent_id}"))
    controls.append(InlineKeyboardButton("🛑 Stop", callback_data=f"ta:stop:{agent_id}"))

    keyboard = [
        controls,
        [
            InlineKeyboardButton("📓 Journal", callback_data=f"ta:journal:{agent_id}"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"ta:agent:{agent_id}"),
        ],
        [InlineKeyboardButton("« Back", callback_data="ta:running")],
    ]

    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_agent_journal(query, context: ContextTypes.DEFAULT_TYPE, agent_id: str) -> None:
    """Show recent journal entries for an agent."""
    from condor.trading_agent.journal import JournalManager

    jm = JournalManager(agent_id)
    state = jm.read_state()
    learnings = jm.read_learnings()
    recent = jm.read_recent()

    parts = []
    if state:
        parts.append(f"📍 State\n{state}")
    if learnings:
        # Show only last 5 learnings in the UI to keep it readable
        lines = [l for l in learnings.splitlines() if l.startswith("- ")]
        if len(lines) > 5:
            lines = lines[-5:]
            parts.append(f"💡 Learnings (last 5 of {len(learnings.splitlines())})\n" + "\n".join(lines))
        else:
            parts.append(f"💡 Learnings\n" + "\n".join(lines))
    if recent:
        # Show only last 5 actions in the UI
        lines = [l for l in recent.splitlines() if l.strip()]
        if len(lines) > 5:
            lines = lines[-5:]
        parts.append(f"📋 Recent Actions\n" + "\n".join(lines))

    text = "\n\n".join(parts) if parts else "Journal is empty."

    # Truncate for Telegram (4096 char limit with markup overhead)
    if len(text) > 3500:
        text = text[:3500] + "\n...(truncated)"

    text = escape_markdown_v2(text)

    keyboard = [
        [InlineKeyboardButton("« Back", callback_data=f"ta:agent:{agent_id}")],
    ]
    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

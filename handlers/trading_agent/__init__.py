"""Trading Agent handler -- /agent_trading command and callbacks.

Starts an ACP session focused on autonomous trading agent management:
- Strategy creation, editing, deletion
- Agent start/stop/pause/resume
- Monitoring dashboards, journal, runs

Also provides inline keyboard UI for quick actions via ta:* callbacks.
"""

import logging
import uuid

from telegram import Update
from telegram.ext import ContextTypes

from handlers import clear_all_input_states
from utils.auth import restricted

from ._shared import (
    TA_CONFIG_PARAMS,
    TA_SELECTED_STRATEGY,
    TA_STATE_KEY,
    clear_ta_state,
)

logger = logging.getLogger(__name__)


@restricted
async def trading_agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /agent_trading — start a trading-focused ACP session."""
    # Block in group chats (same as /agent)
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.reply_text("Trading agent mode is only available in private chats.")
        return

    clear_all_input_states(context)

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else None

    # Set agent state so messages route through agent_message_handler
    context.user_data["agent_state"] = "active"
    context.user_data["agent_selected"] = "claude-code"
    context.user_data["agent_mode"] = "trading"

    placeholder = await update.message.reply_text("Starting trading agent session...")

    try:
        from handlers.agents.session import get_or_create_session, destroy_session

        # Destroy existing session to start fresh with trading context
        await destroy_session(chat_id)

        bot = context.bot

        async def _perm_cb(tool_call, options):
            from handlers.agents.confirmation import permission_callback
            return await permission_callback(bot, chat_id, tool_call, options)

        session = await get_or_create_session(
            chat_id=chat_id,
            agent_key="claude-code",
            permission_callback=_perm_cb,
            user_id=user_id,
            user_data=context.user_data,
        )

        # Inject trading-focused context on top of the base context
        trading_context = _build_trading_context()
        if trading_context:
            try:
                await session.client.prompt(trading_context)
                # Update usage from context injection
                if session.client.last_usage:
                    u = session.client.last_usage
                    if u.used >= session.tokens_used:
                        session.tokens_used = u.used
                    session.context_window = u.size
                    if u.cost_usd >= session.cost_usd:
                        session.cost_usd = u.cost_usd
            except Exception:
                logger.warning("Failed to inject trading context for chat %d", chat_id)

        await placeholder.edit_text(
            "🤖 Trading agent mode active.\n\n"
            "I can help you create strategies, start/stop agents, and monitor performance.\n"
            "Ask me anything about your trading agents, or say what you want to do."
        )

    except Exception as e:
        logger.exception("Failed to start trading agent session")
        await placeholder.edit_text(f"Failed to start session: {e}")
        context.user_data.pop("agent_state", None)
        context.user_data.pop("agent_mode", None)


def _build_trading_context() -> str:
    """Build the trading-focused initial context prompt."""
    from condor.trading_agent.strategy import StrategyStore
    from condor.trading_agent.engine import get_all_engines

    sections = [TRADING_SYSTEM_PROMPT]

    # List existing strategies
    store = StrategyStore()
    strategies = store.list_all()
    if strategies:
        strat_lines = ["Existing strategies:"]
        for s in strategies:
            skills = ", ".join(s.skills) if s.skills else "none"
            pair = s.default_config.get("trading_pair", "")
            strat_lines.append(f"- {s.name} (id={s.id}, agent={s.agent_key}, skills={skills}, pair={pair})")
        sections.append("\n".join(strat_lines))
    else:
        sections.append("No strategies exist yet. Help the user create their first one.")

    # List running agents
    engines = get_all_engines()
    if engines:
        agent_lines = ["Running agents:"]
        for eid, engine in engines.items():
            info = engine.get_info()
            agent_lines.append(
                f"- {info['strategy']} ({eid}): {info['status']}, "
                f"PnL=${info['daily_pnl']:+.2f}, ticks={info['tick_count']}, "
                f"open={info['open_executors']}"
            )
        sections.append("\n".join(agent_lines))
    else:
        sections.append("No agents are currently running.")

    return "\n\n".join(sections)


TRADING_SYSTEM_PROMPT = """\
[System context -- do not repeat this to the user]
You are now in TRADING AGENT mode. Your focus is on managing autonomous \
trading agents -- creating strategies, starting agents, monitoring \
performance, and reviewing trading decisions.

WHAT YOU CAN DO:
- Create, edit, and delete trading strategies via manage_trading_agent tool
- Start, stop, pause, resume trading agents
- Read agent journals and run snapshots (trading_agent_journal_read)
- Monitor agent status, PnL, risk state
- Review run history (decision logs per tick)

WORKFLOW FOR CREATING A NEW STRATEGY:
1. Discuss with user what they want to trade and how
2. Use manage_trading_agent(action="create_strategy", name=..., description=..., \
instructions=..., agent_key="claude-code") to create it
3. Then start an agent with manage_trading_agent(action="start_agent", strategy_id=..., config={...})

WORKFLOW FOR MONITORING:
1. Use manage_trading_agent(action="list_agents") to see running agents
2. Use manage_trading_agent(action="agent_status", agent_id=...) for detailed status
3. Use trading_agent_journal_read(agent_id=..., section="summary") for quick status
4. Use trading_agent_journal_read(agent_id=..., section="runs") to list run snapshots
5. Use trading_agent_journal_read(agent_id=..., section="run:N") to see tick N detail

DATA STRUCTURE:
Each strategy has its own folder: data/trading_agents/{slug}/
  - agent.md: strategy definition
  - trading_sessions/session_N/: per-session data
    - journal.md: learnings + summary + ticks + executors + snapshots
    - runs/: per-tick snapshots (run_1.md, run_2.md, ...)

RULES:
- Be direct and concise. This is Telegram, keep messages short.
- When showing agent status, use key: value format, not tables.
- When the user asks to create a strategy, help them write good instructions \
for the trading agent (the LLM that will execute ticks).
- Always include risk limits when starting agents.
"""


@restricted
async def trading_agent_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route ta:* callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    # Strip "ta:" prefix
    action = data.split(":", 1)[1] if ":" in data else data

    from .menu import (
        show_agent_dashboard,
        show_agent_journal,
        show_agent_runs,
        show_main_menu,
        show_run_detail,
        show_running_agents,
        show_start_config,
        show_strategies,
        show_strategy_detail,
    )

    if action == "menu":
        clear_ta_state(context)
        await show_main_menu(query, context)

    elif action == "close":
        clear_ta_state(context)
        await query.message.delete()

    elif action == "strategies":
        await show_strategies(query, context)

    elif action.startswith("strat:"):
        strategy_id = action.split(":", 1)[1]
        await show_strategy_detail(query, context, strategy_id)

    elif action.startswith("start:"):
        strategy_id = action.split(":", 1)[1]
        await show_start_config(query, context, strategy_id)

    elif action.startswith("launch:"):
        strategy_id = action.split(":", 1)[1]
        await _launch_agent(query, context, strategy_id)

    elif action.startswith("delete:"):
        strategy_id = action.split(":", 1)[1]
        await _delete_strategy(query, context, strategy_id)

    elif action == "running":
        await show_running_agents(query, context)

    elif action.startswith("agent:"):
        agent_id = action.split(":", 1)[1]
        await show_agent_dashboard(query, context, agent_id)

    elif action.startswith("stop:"):
        agent_id = action.split(":", 1)[1]
        await _stop_agent(query, context, agent_id)

    elif action.startswith("pause:"):
        agent_id = action.split(":", 1)[1]
        await _pause_agent(query, context, agent_id)

    elif action.startswith("resume:"):
        agent_id = action.split(":", 1)[1]
        await _resume_agent(query, context, agent_id)

    elif action.startswith("journal:"):
        agent_id = action.split(":", 1)[1]
        await show_agent_journal(query, context, agent_id)

    elif action.startswith("runs:"):
        agent_id = action.split(":", 1)[1]
        await show_agent_runs(query, context, agent_id)

    elif action.startswith("run:"):
        # Format: run:{agent_id}:{tick}
        parts = action.split(":", 2)
        if len(parts) == 3:
            agent_id = parts[1]
            try:
                tick = int(parts[2])
            except ValueError:
                return
            await show_run_detail(query, context, agent_id, tick)


async def trading_agent_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle key=value config messages. Returns True if handled."""
    state = context.user_data.get(TA_STATE_KEY)
    if state != "editing_config":
        return False

    text = update.message.text.strip()
    if "=" not in text:
        return False

    config = context.user_data.get(TA_CONFIG_PARAMS, {})
    key, _, value = text.partition("=")
    key = key.strip()
    value = value.strip()

    # Handle nested risk_limits keys
    if key.startswith("risk_limits.") or key in config.get("risk_limits", {}):
        risk_key = key.replace("risk_limits.", "")
        if "risk_limits" not in config:
            config["risk_limits"] = {}
        try:
            config["risk_limits"][risk_key] = float(value)
        except ValueError:
            config["risk_limits"][risk_key] = value
    else:
        # Try numeric conversion
        try:
            config[key] = int(value)
        except ValueError:
            try:
                config[key] = float(value)
            except ValueError:
                config[key] = value

    context.user_data[TA_CONFIG_PARAMS] = config
    await update.message.reply_text(f"Set {key} = {value}")
    return True


# --- Internal actions ---


async def _launch_agent(query, context, strategy_id: str) -> None:
    """Launch a new agent from strategy + config."""
    from condor.trading_agent.engine import TickEngine
    from condor.trading_agent.strategy import StrategyStore

    store = StrategyStore()
    strategy = store.get(strategy_id)
    if not strategy:
        await query.edit_message_text("Strategy not found.")
        return

    config = context.user_data.get(TA_CONFIG_PARAMS, dict(strategy.default_config))
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    agent_id = uuid.uuid4().hex[:8]

    engine = TickEngine(
        agent_id=agent_id,
        strategy=strategy,
        config=config,
        chat_id=chat_id,
        user_id=user_id,
    )
    await engine.start(bot=context.bot)

    # Persist for auto-restore
    if "ta_instances" not in context.user_data:
        context.user_data["ta_instances"] = {}
    context.user_data["ta_instances"][agent_id] = {
        "strategy_id": strategy_id,
        "config": config,
        "user_id": user_id,
        "status": "running",
        "session_dir": str(engine.session_dir) if engine.session_dir else "",
    }

    clear_ta_state(context)
    await query.edit_message_text(
        f"🚀 Agent {agent_id} started!\n"
        f"Strategy: {strategy.name}\n"
        f"Frequency: {config.get('frequency_sec', 60)}s\n\n"
        f"Use /agent_trading to monitor."
    )


async def _stop_agent(query, context, agent_id: str) -> None:
    from condor.trading_agent.engine import get_engine
    engine = get_engine(agent_id)
    if engine:
        await engine.stop()
    instances = context.user_data.get("ta_instances", {})
    if agent_id in instances:
        instances[agent_id]["status"] = "stopped"
    await query.edit_message_text(f"🛑 Agent {agent_id} stopped.")


async def _pause_agent(query, context, agent_id: str) -> None:
    from condor.trading_agent.engine import get_engine
    from .menu import show_agent_dashboard
    engine = get_engine(agent_id)
    if engine:
        engine.pause()
    await show_agent_dashboard(query, context, agent_id)


async def _resume_agent(query, context, agent_id: str) -> None:
    from condor.trading_agent.engine import get_engine
    from .menu import show_agent_dashboard
    engine = get_engine(agent_id)
    if engine:
        engine.resume()
    await show_agent_dashboard(query, context, agent_id)


async def _delete_strategy(query, context, strategy_id: str) -> None:
    from condor.trading_agent.strategy import StrategyStore
    from .menu import show_strategies
    store = StrategyStore()
    store.delete(strategy_id)
    await show_strategies(query, context)

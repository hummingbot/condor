"""Trading Agent handler -- ta:* callback UI and key=value config editing.

The /agent_trading command has been removed. Use /agent → Agent Builder mode instead.
The ta:* inline keyboard UI for strategy browsing, agent dashboards, etc. still works independently.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from utils.auth import restricted

from ._shared import (
    TA_CONFIG_PARAMS,
    TA_SELECTED_STRATEGY,
    TA_STATE_KEY,
    clear_ta_state,
)

logger = logging.getLogger(__name__)


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

    engine = TickEngine(
        strategy=strategy,
        config=config,
        chat_id=chat_id,
        user_id=user_id,
    )
    await engine.start(bot=context.bot)

    # Persist for auto-restore
    if "ta_instances" not in context.user_data:
        context.user_data["ta_instances"] = {}
    context.user_data["ta_instances"][engine.agent_id] = {
        "strategy_id": strategy_id,
        "config": config,
        "user_id": user_id,
        "status": "running",
        "session_dir": str(engine.session_dir) if engine.session_dir else "",
    }

    clear_ta_state(context)
    await query.edit_message_text(
        f"🚀 Agent {engine.agent_id} started!\n"
        f"Strategy: {strategy.name}\n"
        f"Frequency: {config.get('frequency_sec', 60)}s\n\n"
        f"Use /agent to monitor."
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

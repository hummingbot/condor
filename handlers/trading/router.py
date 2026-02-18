"""
Unified Trade Callback Router

Handles trade:* callbacks for connector switching between CEX and DEX.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from utils.auth import restricted

from . import (
    handle_back,
    handle_select_cex_connector,
    handle_select_dex_network,
    handle_unified_connector_select,
)

logger = logging.getLogger(__name__)


@restricted
async def unified_trade_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle trade:* callbacks for connector switching"""
    query = update.callback_query
    await query.answer()

    # Parse action from callback data
    callback_parts = query.data.split(":", 1)
    action = callback_parts[1] if len(callback_parts) > 1 else query.data

    logger.debug(f"Unified trade callback: {action}")

    # Route based on action
    if action == "select_connector":
        await handle_unified_connector_select(update, context)

    elif action.startswith("select_cex:"):
        connector = action.replace("select_cex:", "")
        await handle_select_cex_connector(update, context, connector)

    elif action.startswith("select_dex:"):
        network = action.replace("select_dex:", "")
        await handle_select_dex_network(update, context, network)

    elif action == "back":
        await handle_back(update, context)

    elif action == "noop":
        # No-op for separator buttons
        pass

    else:
        logger.warning(f"Unknown unified trade action: {action}")

"""
CEX Trading main menu - redirects to unified trade menu
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Key for storing the background loading task
CEX_LOADING_TASK_KEY = "_cex_menu_loading_task"


def cancel_cex_loading_task(context) -> None:
    """Cancel any pending CEX menu loading task"""
    task = context.user_data.get(CEX_LOADING_TASK_KEY)
    if task and not task.done():
        task.cancel()
        logger.debug("Cancelled pending CEX menu loading task")
    context.user_data.pop(CEX_LOADING_TASK_KEY, None)


async def show_cex_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display main CEX trading menu - redirects to unified trade menu"""
    from .trade import handle_trade

    await handle_trade(update, context)


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle closing the CEX trading interface"""
    from .trade import handle_close as trade_close

    await trade_close(update, context)

import logging
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.config import AUTHORIZED_USERS

logger = logging.getLogger(__name__)


def restricted(func):
    @wraps(func)
    async def wrapped(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        user_id = update.effective_user.id
        if user_id not in AUTHORIZED_USERS:
            print(f"Unauthorized access denied for {user_id}.")
            await update.message.reply_text("You are not authorized to use this bot.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapped


async def _send_service_unavailable_message(
    update: Update,
    title: str,
    status_line: str,
    instruction: str,
    close_callback: str = "dex:close"
) -> None:
    """Send a standardized service unavailable message."""
    message = f"⚠️ *{title}*\n\n"
    message += f"{status_line}\n\n"
    message += instruction

    keyboard = [[InlineKeyboardButton("✕ Close", callback_data=close_callback)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    elif update.callback_query:
        await update.callback_query.message.edit_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


def gateway_required(func):
    """
    Decorator that checks if the Gateway is running on the default server.
    If not running, displays an error message and prevents the handler from executing.

    Usage:
        @gateway_required
        async def handle_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE):
            ...
    """
    @wraps(func)
    async def wrapped(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        try:
            from handlers.config.server_context import get_gateway_status_info, get_server_context_header

            # Get chat_id to use per-chat server default
            chat_id = update.effective_chat.id if update.effective_chat else None

            # Check server status first
            server_header, server_online = await get_server_context_header(chat_id)

            if not server_online:
                await _send_service_unavailable_message(
                    update,
                    title="Server Offline",
                    status_line="🔴 The API server is not reachable\\.",
                    instruction="Check your server configuration in /config \\> API Servers\\."
                )
                return

            # Check gateway status
            _, gateway_running = await get_gateway_status_info(chat_id)

            if not gateway_running:
                await _send_service_unavailable_message(
                    update,
                    title="Gateway Not Running",
                    status_line="🔴 The Gateway is not deployed or not running on this server\\.",
                    instruction="Deploy the Gateway in /config \\> Gateway to use this feature\\."
                )
                return

            return await func(update, context, *args, **kwargs)

        except Exception as e:
            logger.error(f"Error checking gateway status: {e}", exc_info=True)
            await _send_service_unavailable_message(
                update,
                title="Service Unavailable",
                status_line="⚠️ Could not verify service status\\.",
                instruction="Please try again or check /config for server status\\."
            )
            return

    return wrapped


def hummingbot_api_required(func):
    """
    Decorator that checks if the Hummingbot API server is online.
    If offline, displays an error message and prevents the handler from executing.

    Usage:
        @hummingbot_api_required
        async def handle_some_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
            ...
    """
    @wraps(func)
    async def wrapped(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        try:
            from handlers.config.server_context import get_server_context_header

            # Get chat_id to use per-chat server default
            chat_id = update.effective_chat.id if update.effective_chat else None

            # Check server status
            server_header, server_online = await get_server_context_header(chat_id)

            if not server_online:
                await _send_service_unavailable_message(
                    update,
                    title="API Server Offline",
                    status_line="🔴 The Hummingbot API server is not reachable\\.",
                    instruction="Check your server configuration in /config \\> API Servers\\."
                )
                return

            return await func(update, context, *args, **kwargs)

        except Exception as e:
            logger.error(f"Error checking API server status: {e}", exc_info=True)
            await _send_service_unavailable_message(
                update,
                title="Service Unavailable",
                status_line="⚠️ Could not verify service status\\.",
                instruction="Please try again or check /config for server status\\."
            )
            return

    return wrapped

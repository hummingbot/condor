import logging
from functools import wraps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config_manager import (
    ServerPermission,
    UserRole,
    get_config_manager,
)

logger = logging.getLogger(__name__)


async def _notify_admin_new_user(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, username: str
) -> None:
    """Send notification to admin about new user request."""
    cm = get_config_manager()
    admin_id = cm.admin_id

    if not admin_id:
        return

    try:
        message = (
            f"👤 *New Access Request*\n\n"
            f"User ID: `{user_id}`\n"
            f"Username: @{username or 'N/A'}\n\n"
            f"Use /start \\> Admin Panel to approve or reject\\."
        )
        await context.bot.send_message(
            chat_id=admin_id, text=message, parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.warning(f"Failed to notify admin about new user: {e}")


def restricted(func):
    """
    Decorator that checks if user is approved.
    New users are auto-registered as pending and admin is notified.
    """

    @wraps(func)
    async def wrapped(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        user_id = update.effective_user.id
        username = update.effective_user.username

        cm = get_config_manager()
        role = cm.get_user_role(user_id)

        # Handle blocked users
        if role == UserRole.BLOCKED:
            logger.info(f"Blocked user {user_id} attempted access")
            if update.message:
                await update.message.reply_text("🚫 Access denied.")
            elif update.callback_query:
                await update.callback_query.answer("Access denied", show_alert=True)
            return

        # Handle pending users
        if role == UserRole.PENDING:
            if update.message:
                await update.message.reply_text(
                    "⏳ Your access request is pending admin approval.\n"
                    "You will be notified when approved."
                )
            elif update.callback_query:
                await update.callback_query.answer(
                    "Access pending approval", show_alert=True
                )
            return

        # Handle new users - register as pending
        if role is None:
            is_new = cm.register_pending(user_id, username)
            if is_new:
                # Notify admin
                await _notify_admin_new_user(context, user_id, username)

            if update.message:
                await update.message.reply_text(
                    "🔒 *Access Request Submitted*\n\n"
                    f"Your User ID: `{user_id}`\n\n"
                    "An admin will review your request\\. "
                    "You will be notified when approved\\.",
                    parse_mode="MarkdownV2",
                )
            elif update.callback_query:
                await update.callback_query.answer(
                    "Access request submitted", show_alert=True
                )
            return

        # User is approved (USER or ADMIN role)
        # Store user_id in context for access control in subsequent calls
        context.user_data["_user_id"] = user_id
        return await func(update, context, *args, **kwargs)

    return wrapped


def admin_required(func):
    """Decorator that requires admin role."""

    @wraps(func)
    async def wrapped(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        user_id = update.effective_user.id
        cm = get_config_manager()

        if not cm.is_admin(user_id):
            if update.message:
                await update.message.reply_text("🔐 Admin access required.")
            elif update.callback_query:
                await update.callback_query.answer(
                    "Admin access required", show_alert=True
                )
            return

        return await func(update, context, *args, **kwargs)

    return wrapped


def server_access_required(min_permission: ServerPermission = ServerPermission.VIEWER):
    """
    Decorator factory that checks server permission.
    Server name is determined from context.user_data or per-chat default.
    """

    def decorator(func):
        @wraps(func)
        async def wrapped(
            update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
        ):
            from config_manager import get_config_manager
            from handlers.config.user_preferences import get_active_server

            user_id = update.effective_user.id
            cm = get_config_manager()

            # Get user's preferred server, fallback to first accessible
            server_name = get_active_server(context.user_data)
            if not server_name:
                accessible = cm.get_accessible_servers(user_id)
                server_name = accessible[0] if accessible else None

            if not server_name:
                if update.message:
                    await update.message.reply_text("⚠️ No server configured.")
                elif update.callback_query:
                    await update.callback_query.answer(
                        "No server configured", show_alert=True
                    )
                return

            # Check permission
            if not cm.has_server_access(user_id, server_name, min_permission):
                perm_name = min_permission.value.title()
                if update.message:
                    await update.message.reply_text(
                        f"🚫 You don't have {perm_name} access to this server."
                    )
                elif update.callback_query:
                    await update.callback_query.answer(
                        f"No {perm_name} access to this server", show_alert=True
                    )
                return

            return await func(update, context, *args, **kwargs)

        return wrapped

    return decorator


async def _send_service_unavailable_message(
    update: Update,
    title: str,
    status_line: str,
    instruction: str,
    close_callback: str = "dex:close",
) -> None:
    """Send a standardized service unavailable message."""
    message = f"⚠️ *{title}*\n\n"
    message += f"{status_line}\n\n"
    message += instruction

    keyboard = [[InlineKeyboardButton("✕ Close", callback_data=close_callback)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            message, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
    elif update.callback_query:
        await update.callback_query.message.edit_text(
            message, parse_mode="MarkdownV2", reply_markup=reply_markup
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
            from handlers.config.server_context import (
                get_gateway_status_info,
                get_server_context_header,
            )

            # Get chat_id to use per-chat server default
            chat_id = update.effective_chat.id if update.effective_chat else None

            # Check server status first
            server_header, server_online = await get_server_context_header(
                context.user_data
            )

            if not server_online:
                await _send_service_unavailable_message(
                    update,
                    title="Server Offline",
                    status_line="🔴 The API server is not reachable\\.",
                    instruction="Check your server configuration in /start \\> API Servers\\.",
                )
                return

            # Check gateway status
            _, gateway_running = await get_gateway_status_info(
                chat_id, context.user_data
            )

            if not gateway_running:
                await _send_service_unavailable_message(
                    update,
                    title="Gateway Not Running",
                    status_line="🔴 The Gateway is not deployed or not running on this server\\.",
                    instruction="Deploy the Gateway in /start \\> Gateway to use this feature\\.",
                )
                return

            return await func(update, context, *args, **kwargs)

        except Exception as e:
            logger.error(f"Error checking gateway status: {e}", exc_info=True)
            await _send_service_unavailable_message(
                update,
                title="Service Unavailable",
                status_line="⚠️ Could not verify service status\\.",
                instruction="Please try again or check /start for server status\\.",
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

            # Check server status
            server_header, server_online = await get_server_context_header(
                context.user_data
            )

            if not server_online:
                await _send_service_unavailable_message(
                    update,
                    title="API Server Offline",
                    status_line="🔴 The Hummingbot API server is not reachable\\.",
                    instruction="Check your server configuration in /start \\> API Servers\\.",
                )
                return

            return await func(update, context, *args, **kwargs)

        except Exception as e:
            logger.error(f"Error checking API server status: {e}", exc_info=True)
            await _send_service_unavailable_message(
                update,
                title="Service Unavailable",
                status_line="⚠️ Could not verify service status\\.",
                instruction="Please try again or check /start for server status\\.",
            )
            return

    return wrapped

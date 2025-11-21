"""
Chat ID command handler - helps users identify their chat ID for configuration
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /chatid command - Display the current chat ID

    This is useful for first-time setup when configuring ALLOWED_CHAT_IDS.
    The command is intentionally NOT restricted so users can find their chat ID
    before adding it to the configuration.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or "No username"
    first_name = update.effective_user.first_name or ""

    message = f"""
🆔 *Chat Information*

📱 *Chat ID*: `{chat_id}`
👤 *User ID*: `{user_id}`
🏷️ *Username*: @{username}
📝 *Name*: {first_name}

_Copy the Chat ID above and add it to your ALLOWED\\_CHAT\\_IDS environment variable to enable bot access\\._
"""

    await update.message.reply_text(message, parse_mode="MarkdownV2")
    logger.info(f"Chat ID request from user {user_id} ({username}): chat_id={chat_id}")

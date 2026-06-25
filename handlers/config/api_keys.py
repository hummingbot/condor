"""
API Keys configuration handlers (read-only).

Connecting and removing exchange API keys is done exclusively through the
Condor web dashboard (Settings → Keys). The Telegram bot only shows which
exchanges are currently connected and points the user to the web UI.
"""

import logging
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.auth import restricted
from utils.config import WEB_URL
from utils.telegram_formatters import escape_markdown_v2

from .server_context import build_config_message_header, format_server_selection_needed
from .user_preferences import get_active_server

logger = logging.getLogger(__name__)

# Default account name used for all API key operations
DEFAULT_ACCOUNT = "master_account"

# Where users connect/remove exchange keys (web dashboard only)
KEYS_WEB_URL = f"{WEB_URL}/settings?tab=keys"


def keys_web_button() -> InlineKeyboardButton | None:
    """Return a tappable URL button to the web keys page, or None.

    Telegram rejects URL buttons pointing at localhost/loopback hosts, so for
    a local WEB_URL we return None and callers fall back to a callback button.
    """
    host = (urlparse(KEYS_WEB_URL).hostname or "").lower()
    if not host or host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return None
    return InlineKeyboardButton("🌐 Open Web Dashboard", url=KEYS_WEB_URL)


@restricted
async def keys_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /keys command - show connected exchanges (read-only)."""
    from handlers import clear_all_input_states
    from utils.telegram_helpers import create_mock_query_from_message

    clear_all_input_states(context)
    mock_query = await create_mock_query_from_message(update, "Loading API keys...")
    await show_api_keys(mock_query, context)


async def get_default_account(client) -> str:
    """
    Get the default account to use for API key operations.
    Returns the first available account from the backend, or DEFAULT_ACCOUNT if none exist.
    """
    try:
        accounts = await client.accounts.list_accounts()
        if accounts:
            return str(accounts[0])
    except Exception:
        pass
    return DEFAULT_ACCOUNT


async def show_api_keys(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show the exchanges currently connected (read-only).

    Connecting or removing keys is done via the web dashboard, so this view
    lists the configured connectors and points the user to the web UI.
    """
    # Clear bots state to prevent bots handler from intercepting input
    # This is needed when navigating here from a bot wizard.
    context.user_data.pop("bots_state", None)

    web_hint = (
        "_To connect or remove exchange keys, open the web dashboard:_\n"
        f"`{KEYS_WEB_URL}`\n"
        "_\\(Settings → Keys\\)_"
    )

    try:
        from config_manager import get_config_manager

        servers = get_config_manager().list_servers()

        if not servers:
            message_text = format_server_selection_needed()
            keyboard = [[InlineKeyboardButton("« Close", callback_data="config_close")]]
        else:
            chat_id = query.message.chat_id
            header, server_online, _ = await build_config_message_header(
                "🔑 API Keys",
                include_gateway=False,
                chat_id=chat_id,
                user_data=context.user_data,
            )

            if not server_online:
                message_text = (
                    header + "⚠️ _Server is offline\\. Cannot show API keys\\._"
                )
                keyboard = [
                    [InlineKeyboardButton("« Close", callback_data="config_close")]
                ]
            else:
                client = await get_config_manager().get_client_for_chat(
                    chat_id, preferred_server=get_active_server(context.user_data)
                )

                account_name = await get_default_account(client)

                try:
                    credentials = await client.accounts.list_account_credentials(
                        account_name=account_name
                    )
                    cred_list = credentials if credentials else []
                except Exception as e:
                    logger.warning(f"Failed to get credentials for {account_name}: {e}")
                    cred_list = []

                if cred_list:
                    lines = ["*Connected exchanges:*"]
                    for cred in cred_list:
                        emoji = "📈" if "perpetual" in cred else "💱"
                        lines.append(f"  {emoji} {escape_markdown_v2(str(cred))}")
                    creds_display = "\n".join(lines) + "\n\n"
                else:
                    creds_display = "_No exchanges connected yet\\._\n\n"

                message_text = header + creds_display + web_hint
                keyboard = []
                web_button = keys_web_button()
                if web_button:
                    keyboard.append([web_button])
                keyboard.append(
                    [InlineKeyboardButton("« Close", callback_data="config_close")]
                )

        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.message.edit_text(
                message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                await query.answer("✅ Already up to date")
            else:
                raise

    except Exception as e:
        logger.error(f"Error showing API keys: {e}", exc_info=True)
        error_text = f"❌ Error loading API keys: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Close", callback_data="config_close")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


# Entry point function for routing


async def handle_api_keys_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Route API key callback queries to the read-only view.

    All callbacks (the live `config_api_keys` entry and any stale `api_key_*`
    buttons from before keys management moved to the web UI) just refresh the
    read-only view.
    """
    await show_api_keys(update.callback_query, context)

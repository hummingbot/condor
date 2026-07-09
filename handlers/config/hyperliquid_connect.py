"""Connect Hyperliquid via WalletConnect from the /keys menu.

Lets a user with only a mobile wallet authorize a Hyperliquid agent wallet +
builder-fee approval by scanning a QR sent directly in Telegram (sendPhoto),
instead of needing the web dashboard's browser-extension-only "Connect
Hyperliquid" flow. Reuses the same session registry as the
connect_hyperliquid_wallet MCP tool (condor/walletconnect.py) -- this handler
just calls it directly since Telegram handlers already run in the main
process, no loopback HTTP hop needed. See
docs/architecture/hyperliquid-walletconnect-spike.md for the design.
"""

import asyncio
import io
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

POLL_INTERVAL = 3  # seconds between status checks
POLL_TIMEOUT = 6 * 60  # give up if still not terminal after this long (bridge's own approval window is 5 min)

_BACK_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("« Back", callback_data="config_api_keys")]]
)

_STATUS_CAPTION = {
    "pending_approval": (
        "⏳ *Waiting for you to scan and pair\\.\\.\\.*\n\n"
        "Open a WalletConnect\\-compatible mobile wallet \\(Rabby, MetaMask\\) "
        "holding a Hyperliquid\\-funded address, scan this code, and approve "
        "the two signature requests\\."
    ),
    "pending_signatures": "✍️ *Pairing approved — signing agent wallet \\+ builder fee\\.\\.\\.*",
}


async def start_hyperliquid_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for the /keys "🔗 Connect Hyperliquid" button."""
    from config_manager import get_config_manager, get_effective_server

    from condor.walletconnect import generate_qr_png, start_walletconnect_session

    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    cm = get_config_manager()
    server_name = get_effective_server(chat_id, context.user_data) or cm.get_default_server()
    if not server_name:
        await query.message.edit_text(
            "⚠️ _No server configured\\. Add a Hummingbot API server first \\(/servers\\)\\._",
            parse_mode="MarkdownV2",
            reply_markup=_BACK_KEYBOARD,
        )
        return

    await query.message.edit_text(
        "⏳ *Starting WalletConnect session\\.\\.\\.*", parse_mode="MarkdownV2"
    )

    try:
        result = await start_walletconnect_session(server_name, user_id)
    except RuntimeError as e:
        await query.message.edit_text(
            f"❌ {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=_BACK_KEYBOARD,
        )
        return

    session_id = result["session_id"]
    png = generate_qr_png(result["uri"])

    try:
        await query.message.delete()
    except TelegramError:
        pass  # already gone

    photo_msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(png),
        caption=_STATUS_CAPTION["pending_approval"],
        parse_mode="MarkdownV2",
    )

    asyncio.create_task(
        _poll_session(context, chat_id, photo_msg.message_id, session_id, user_id)
    )


async def _poll_session(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    session_id: str,
    user_id: int,
) -> None:
    """Background task: edit the QR photo's caption in place as the session progresses."""
    from condor.walletconnect import get_session_status

    last_status = None
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        status = await get_session_status(session_id, user_id)
        if status is None:
            return  # session evicted (shouldn't happen while we're actively polling it)

        state = status.get("status")
        if state == last_status:
            continue
        last_status = state

        if state == "done":
            saved = ", ".join(f"`{c}`" for c in status.get("saved_connectors", []))
            text = f"✅ *Hyperliquid connected* — {saved} saved\\."
            failed = status.get("failed_connectors") or {}
            if failed:
                text += f"\n⚠️ Failed to save: {', '.join(f'`{c}`' for c in failed)}"
            await _safe_edit_caption(context, chat_id, message_id, text)
            return
        if state == "error":
            msg = escape_markdown_v2(status.get("message", "Unknown error."))
            await _safe_edit_caption(context, chat_id, message_id, f"❌ {msg}")
            return

        caption = _STATUS_CAPTION.get(state)
        if caption:
            await _safe_edit_caption(context, chat_id, message_id, caption)

    await _safe_edit_caption(
        context,
        chat_id,
        message_id,
        "⌛ *Timed out waiting for approval\\.* Try again from /keys\\.",
    )


async def _safe_edit_caption(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, caption: str) -> None:
    try:
        await context.bot.edit_message_caption(
            chat_id=chat_id, message_id=message_id, caption=caption, parse_mode="MarkdownV2"
        )
    except TelegramError as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"Failed to edit WalletConnect status caption: {e}")

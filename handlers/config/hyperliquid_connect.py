"""Connect Hyperliquid via WalletConnect from the /keys menu.

Lets a user with only a mobile wallet authorize a Hyperliquid agent wallet +
builder-fee approval via WalletConnect, instead of needing the web dashboard's
browser-extension-only "Connect Hyperliquid" flow.

Two independent steps, each its own fresh WalletConnect pairing (fresh QR code
+ fresh MetaMask deep-link button -- see condor/walletconnect.py for why a
fresh pairing per signature beats reusing one session):

  1. Connect + create agent wallet (always shown).
  2. Approve builder fee (skipped entirely if the wallet already approved it
     on-chain in a prior connect).

The agent credential is saved as soon as step 1 finishes, so if the user
doesn't come back for step 2 right away, Hyperliquid is still usable in the
meantime -- step 2 is a "Continue when ready" button, not an auto-continuation,
so nothing expires just because they stepped away.

Reuses the same session registry as the connect_hyperliquid_wallet MCP tool --
this handler just calls it directly since Telegram handlers already run in the
main process, no loopback HTTP hop needed. See
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

_STEP_TITLE = {
    "agent": "🔐 *Step 1: Connect \\+ create agent wallet*",
    "builder": "🔐 *Step 2: Approve builder fee*",
}


def _escape_code(uri: str) -> str:
    """Escape only what MarkdownV2 requires inside a code span (backtick, backslash)
    -- running the general escape_markdown_v2 helper on it would corrupt the URI by
    backslash-escaping characters that are literal inside a code span."""
    return uri.replace("\\", "\\\\").replace("`", "\\`")


def _pairing_caption(step: str, uri: str, has_mm_button: bool) -> str:
    action = (
        "Scan the code, or tap below to open MetaMask directly\\."
        if has_mm_button
        else "Scan the code with a WalletConnect\\-compatible wallet\\."
    )
    return (
        f"{_STEP_TITLE[step]}\n\n"
        f"{action} Then approve the signature request\\.\n\n"
        f"_Link not working? Paste it into your wallet manually:_\n`{_escape_code(uri)}`"
    )


def _continue_keyboard(session_id: str, next_step_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"➡️ Continue to {next_step_label}", callback_data=f"api_key_hl_advance:{session_id}")],
            [InlineKeyboardButton("« Back", callback_data="config_api_keys")],
        ]
    )


def _mm_keyboard(deep_link: str | None) -> InlineKeyboardMarkup | None:
    if not deep_link:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("🦊 Open in MetaMask", url=deep_link)]])


async def start_hyperliquid_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for the /keys "🔗 Connect Hyperliquid" button -- begins step 1 (connect + create agent wallet)."""
    from config_manager import get_config_manager, get_effective_server

    from condor.walletconnect import start_walletconnect_session

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

    try:
        await query.message.delete()
    except TelegramError:
        pass  # already gone

    await _render_step(context, chat_id, query.from_user.id, result)


async def hyperliquid_connect_advance(update: Update, context: ContextTypes.DEFAULT_TYPE, session_id: str) -> None:
    """"➡️ Continue to Step 2" -- begins the builder-fee approval step."""
    from condor.walletconnect import advance_walletconnect_session

    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    await query.message.edit_text(
        "⏳ *Starting WalletConnect session\\.\\.\\.*", parse_mode="MarkdownV2"
    )

    try:
        result = await advance_walletconnect_session(session_id, user_id)
    except RuntimeError as e:
        await query.message.edit_text(
            f"❌ {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=_BACK_KEYBOARD,
        )
        return

    try:
        await query.message.delete()
    except TelegramError:
        pass  # already gone

    await _render_step(context, chat_id, user_id, result)


async def _render_step(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, result: dict) -> None:
    """Send the QR + MetaMask-button message for the step just started, and kick
    off polling for it."""
    from condor.walletconnect import generate_qr_png

    step = result["step"]
    uri = result["uri"]
    deep_link = result.get("deep_links", {}).get("MetaMask")
    keyboard = _mm_keyboard(deep_link)

    photo_msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(generate_qr_png(uri)),
        caption=_pairing_caption(step, uri, has_mm_button=bool(deep_link)),
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
    )
    asyncio.create_task(
        _poll_session(context, chat_id, photo_msg.message_id, result["session_id"], user_id, keyboard)
    )


async def _poll_session(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    session_id: str,
    user_id: int,
    keyboard: InlineKeyboardMarkup | None,
) -> None:
    """Background task: edit the QR photo's caption in place as the current step progresses.

    Telegram's edit_message_caption drops the existing inline keyboard unless the
    same (or a new) reply_markup is passed on every edit -- so the MetaMask button
    must be re-sent on each non-terminal update, not just the initial send.
    """
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

        if state == "pending_approval":
            continue  # identical to what send_photo already showed -- no edit needed

        if state == "pending_signature":
            step = status.get("step")
            text = (
                "✍️ *Pairing approved — signing agent wallet\\.\\.\\.*"
                if step == "agent"
                else "✍️ *Pairing approved — signing builder fee approval\\.\\.\\.*"
            )
            await _safe_edit_caption(context, chat_id, message_id, text, keyboard)
            continue

        if state == "step_done":
            # Agent step finished. Either offer the (skippable) builder step, or
            # -- if the wallet already approved Condor's builder fee -- go
            # straight to done without ever mentioning it.
            if status.get("needs_builder_step"):
                text = (
                    "✅ *Step 1 complete* — agent wallet created and saved\\.\n\n"
                    "*Step 2: Approve builder fee* — lets Condor apply the agreed "
                    "order\\-routing fee\\. Tap Continue when you're ready\\."
                )
                await _safe_edit_caption(context, chat_id, message_id, text, _continue_keyboard(session_id, "Step 2"))
            else:
                await _safe_edit_caption(
                    context, chat_id, message_id, "✅ *Hyperliquid connected\\.*", _BACK_KEYBOARD
                )
            return

        if state == "done":
            await _safe_edit_caption(
                context,
                chat_id,
                message_id,
                "✅ *Hyperliquid connected* — builder fee approved\\.",
                _BACK_KEYBOARD,
            )
            return

        if state == "error":
            msg = escape_markdown_v2(status.get("message", "Unknown error."))
            await _safe_edit_caption(context, chat_id, message_id, f"❌ {msg}", _BACK_KEYBOARD)
            return

    await _safe_edit_caption(
        context,
        chat_id,
        message_id,
        "⌛ *Timed out waiting for approval\\.* Try again from /keys\\.",
        _BACK_KEYBOARD,
    )


async def _safe_edit_caption(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    try:
        await context.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=caption,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )
    except TelegramError as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"Failed to edit WalletConnect status message: {e}")

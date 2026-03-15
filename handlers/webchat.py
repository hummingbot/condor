"""Webchat handlers -- /webtoken and /webchat commands."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from utils.auth import restricted

log = logging.getLogger(__name__)

SITE_URL = os.getenv("CONDOR_SITE_URL", "https://condor.hummingbot.org")
TOKEN_TTL_HOURS = 24


async def _register_token(user_id: int, username: str | None) -> str:
    """Generate a token and register it on the site. Returns the token."""
    token = secrets.token_urlsafe(24)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)).isoformat()

    async with httpx.AsyncClient(base_url=SITE_URL, timeout=10.0) as client:
        r = await client.post("/api/condor/register-token", json={
            "userId": user_id,
            "username": username,
            "token": token,
            "expiresAt": expires_at,
        })
        r.raise_for_status()

    return token


@restricted
async def webtoken_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/webtoken — generate a web access token."""
    user = update.effective_user
    msg = await update.message.reply_text("Generating your web token...")

    try:
        token = await _register_token(user.id, user.username)
        context.user_data["webchat_token"] = token
        connect_url = f"{SITE_URL}/connect?token={token}"
        await msg.edit_text(
            f"🔑 *Your web token* (valid {TOKEN_TTL_HOURS}h)\n\n"
            f"`{token}`\n\n"
            f"Connect at:\n{connect_url}\n\n"
            f"_Paste this token on the site to link your Condor instance._",
            parse_mode="Markdown",
        )
    except Exception as e:
        log.exception("Failed to generate web token")
        await msg.edit_text(f"❌ Failed to generate token: {e}")


@restricted
async def webchat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/webchat on|off — enable or disable web chat polling."""
    from condor.webchat.poller import WebChatPoller, get_poller

    args = context.args or []
    action = args[0].lower() if args else ""
    user = update.effective_user

    if action not in ("on", "off"):
        await update.message.reply_text(
            "Usage:\n"
            "  /webchat on — start listening for web chat messages\n"
            "  /webchat off — stop web chat"
        )
        return

    if action == "off":
        poller = get_poller(user.id)
        if poller and poller.is_running:
            await poller.stop()
            await update.message.reply_text("🔴 Web chat disabled.")
        else:
            await update.message.reply_text("Web chat is not running.")
        return

    # action == "on"
    poller = get_poller(user.id)
    if poller and poller.is_running:
        await update.message.reply_text("✅ Web chat is already running.")
        return

    # Get stored token
    stored = context.user_data.get("webchat_token")
    if not stored:
        await update.message.reply_text(
            "No token found. Run /webtoken first, then /webchat on."
        )
        return

    poller = WebChatPoller(
        user_id=user.id,
        chat_id=update.effective_chat.id,
        token=stored,
    )
    await poller.start()
    await update.message.reply_text(
        "🟢 Web chat enabled.\n"
        f"Messages from {SITE_URL}/chat will be processed by your agent."
    )

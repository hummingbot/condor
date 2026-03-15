"""
Verification keys management — stores read-only CEX keys and public DEX
wallet addresses in condor-web for trade verification.

Accessed via the "🌐 Verification Keys" button in the existing /keys menu.
UX mirrors api_keys.py: inline keyboard menu → field-by-field wizard →
secrets deleted immediately → confirmed via edited message.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2

log = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────────────

CEX_EXCHANGES = [
    "binance", "binance_perpetual",
    "kucoin",
    "coinbase",
    "kraken",
    "bybit", "bybit_perpetual",
    "okx",
    "gate_io",
    "hyperliquid",
    "mexc",
]

DEX_CHAINS = [
    "solana",
    "ethereum",
    "arbitrum",
    "base",
    "optimism",
    "polygon",
    "avalanche",
    "bsc",
]

_TOKEN_KEY = "webchat_token"
_STATE_KEY  = "vkey_state"


# ── menu ──────────────────────────────────────────────────────────────────────

async def show_verify_keys_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main menu: list stored keys + buttons to add CEX/DEX keys."""
    token = context.user_data.get(_TOKEN_KEY)

    if not token:
        await query.message.edit_text(
            "🔑 *Verification Keys*\n\n"
            "No web token found\\. Run /webtoken first to link your "
            "Condor instance to condor\\.hummingbot\\.org\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Back", callback_data="config_api_keys")]]
            ),
        )
        return

    cex_rows: list[dict] = []
    dex_rows: list[dict] = []
    try:
        from condor.webchat.client import SiteClient
        client = SiteClient(token)
        data = await client.list_keys()
        await client.close()
        cex_rows = data.get("cex", [])
        dex_rows = data.get("dex", [])
    except Exception as e:
        log.warning("Could not fetch verification keys: %s", e)

    lines = ["🔑 *Verification Keys*\n"]

    if cex_rows:
        lines.append("*CEX \\(read\\-only\\):*")
        for k in cex_rows:
            ex  = escape_markdown_v2(k["exchange"].title())
            key = escape_markdown_v2(_mask(k["apiKey"]))
            lbl = f"  — {escape_markdown_v2(k['label'])}" if k.get("label") else ""
            lines.append(f"  • {ex}  `{key}`{lbl}")
    else:
        lines.append("_No CEX keys stored\\._")

    lines.append("")

    if dex_rows:
        lines.append("*DEX wallets:*")
        for w in dex_rows:
            chain = escape_markdown_v2(w["chain"].title())
            addr  = escape_markdown_v2(_mask(w["address"], 8, 6))
            lbl   = f"  — {escape_markdown_v2(w['label'])}" if w.get("label") else ""
            lines.append(f"  • {chain}  `{addr}`{lbl}")
    else:
        lines.append("_No DEX wallets stored\\._")

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ CEX key",    callback_data="vkey_add_cex"),
                InlineKeyboardButton("➕ DEX wallet", callback_data="vkey_add_dex"),
            ],
            [InlineKeyboardButton("« Back", callback_data="config_api_keys")],
        ]),
    )


# ── CEX wizard ─────────────────────────────────────────────────────────────────

async def show_cex_exchange_select(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 1 — choose exchange from inline keyboard."""
    context.user_data[_STATE_KEY] = {
        "type": "cex",
        "step": "exchange",
        "message_id": query.message.message_id,
        "chat_id": query.message.chat_id,
    }
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for ex in CEX_EXCHANGES:
        row.append(InlineKeyboardButton(ex, callback_data=f"vkey_cex_ex:{ex}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("« Back", callback_data="vkey_menu")])

    await query.message.edit_text(
        "🔑 *Add CEX Verification Key*\n\n_Select exchange:_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _prompt_api_key(
    bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Step 2 — prompt user to type their read-only API key."""
    state = context.user_data.get(_STATE_KEY, {})
    state["step"] = "api_key"
    context.user_data[_STATE_KEY] = state

    ex_esc = escape_markdown_v2(state.get("exchange", "").title())
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=(
            f"🔑 *{ex_esc} — Read\\-only API Key*\n\n"
            "_Enter your read\\-only API key\\._\n\n"
            "⚠️ Ensure this key has *no withdrawal permissions*\\."
        ),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data="vkey_menu")]]
        ),
    )


async def _prompt_api_secret(
    bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Step 3 — prompt user to type their API secret (deleted immediately)."""
    state = context.user_data.get(_STATE_KEY, {})
    state["step"] = "api_secret"
    context.user_data[_STATE_KEY] = state

    ex_esc  = escape_markdown_v2(state.get("exchange", "").title())
    key_esc = escape_markdown_v2(_mask(state.get("api_key", "")))
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=(
            f"🔑 *{ex_esc}*\n\n"
            f"Key: `{key_esc}` ✅\n\n"
            "_Enter the API secret\\. Your message will be deleted immediately\\._"
        ),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data="vkey_menu")]]
        ),
    )


async def _prompt_cex_label(
    bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Step 4 — optional label."""
    state = context.user_data.get(_STATE_KEY, {})
    state["step"] = "label"
    context.user_data[_STATE_KEY] = state

    ex_esc = escape_markdown_v2(state.get("exchange", "").title())
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=(
            f"🔑 *{ex_esc}*\n\n"
            "Key: `****` ✅\n"
            "Secret: `****` ✅\n\n"
            "_Optional: enter a label for this key, or tap Skip\\._"
        ),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Skip", callback_data="vkey_cex_submit")],
            [InlineKeyboardButton("❌ Cancel", callback_data="vkey_menu")],
        ]),
    )


async def _submit_cex_key(
    bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Final step — POST to condor-web and confirm."""
    state = context.user_data.pop(_STATE_KEY, {})
    token = context.user_data.get(_TOKEN_KEY)

    try:
        from condor.webchat.client import SiteClient
        client = SiteClient(token)
        await client.add_cex_key(
            exchange=state.get("exchange", ""),
            api_key=state.get("api_key", ""),
            api_secret=state.get("api_secret", ""),
            label=state.get("label") or None,
        )
        await client.close()

        ex_esc  = escape_markdown_v2(state.get("exchange", "").title())
        key_esc = escape_markdown_v2(_mask(state.get("api_key", "")))
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"✅ *{ex_esc} key saved*\n\n"
                f"Key: `{key_esc}`\n"
                "_Stored as read\\-only verification key in condor\\.hummingbot\\.org_"
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Back", callback_data="vkey_menu")]]
            ),
        )
    except Exception as e:
        log.error("Failed to save CEX key: %s", e)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"❌ *Error saving key*\n\n`{escape_markdown_v2(str(e)[:120])}`",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Back", callback_data="vkey_menu")]]
            ),
        )


# ── DEX wizard ─────────────────────────────────────────────────────────────────

async def show_dex_chain_select(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 1 — choose chain from inline keyboard."""
    context.user_data[_STATE_KEY] = {
        "type": "dex",
        "step": "chain",
        "message_id": query.message.message_id,
        "chat_id": query.message.chat_id,
    }
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for chain in DEX_CHAINS:
        row.append(InlineKeyboardButton(chain.title(), callback_data=f"vkey_dex_chain:{chain}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("« Back", callback_data="vkey_menu")])

    await query.message.edit_text(
        "🔑 *Add DEX Wallet*\n\n_Select chain:_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _prompt_address(
    bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Step 2 — prompt for public wallet address."""
    state = context.user_data.get(_STATE_KEY, {})
    state["step"] = "address"
    context.user_data[_STATE_KEY] = state

    chain_esc = escape_markdown_v2(state.get("chain", "").title())
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=(
            f"🔑 *{chain_esc} Wallet*\n\n"
            "_Enter the public wallet address\\._\n\n"
            "ℹ️ Public address only — no private key required\\."
        ),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data="vkey_menu")]]
        ),
    )


async def _prompt_dex_label(
    bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Step 3 — optional label."""
    state = context.user_data.get(_STATE_KEY, {})
    state["step"] = "dex_label"
    context.user_data[_STATE_KEY] = state

    chain_esc = escape_markdown_v2(state.get("chain", "").title())
    addr_esc  = escape_markdown_v2(_mask(state.get("address", ""), 8, 6))
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=(
            f"🔑 *{chain_esc} Wallet*\n\n"
            f"Address: `{addr_esc}` ✅\n\n"
            "_Optional: enter a label, or tap Skip\\._"
        ),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Skip", callback_data="vkey_dex_submit")],
            [InlineKeyboardButton("❌ Cancel", callback_data="vkey_menu")],
        ]),
    )


async def _submit_dex_wallet(
    bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Final step — POST to condor-web and confirm."""
    state = context.user_data.pop(_STATE_KEY, {})
    token = context.user_data.get(_TOKEN_KEY)

    try:
        from condor.webchat.client import SiteClient
        client = SiteClient(token)
        await client.add_dex_wallet(
            chain=state.get("chain", ""),
            address=state.get("address", ""),
            label=state.get("label") or None,
        )
        await client.close()

        chain_esc = escape_markdown_v2(state.get("chain", "").title())
        addr_esc  = escape_markdown_v2(_mask(state.get("address", ""), 8, 6))
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"✅ *{chain_esc} wallet saved*\n\n"
                f"`{addr_esc}`\n"
                "_Stored for on\\-chain verification in condor\\.hummingbot\\.org_"
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Back", callback_data="vkey_menu")]]
            ),
        )
    except Exception as e:
        log.error("Failed to save DEX wallet: %s", e)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"❌ *Error saving wallet*\n\n`{escape_markdown_v2(str(e)[:120])}`",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Back", callback_data="vkey_menu")]]
            ),
        )


# ── text input router ──────────────────────────────────────────────────────────

async def handle_verify_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route free-text input during active wizard step. Called from the unified text handler."""
    state = context.user_data.get(_STATE_KEY)
    if not state:
        return

    step       = state.get("step")
    chat_id    = state.get("chat_id") or update.effective_chat.id
    message_id = state.get("message_id")
    bot        = update.get_bot()
    text       = update.message.text.strip()

    # Delete the user's message immediately (especially important for secrets)
    try:
        await update.message.delete()
    except Exception:
        pass

    if step == "api_key":
        state["api_key"] = text
        context.user_data[_STATE_KEY] = state
        await _prompt_api_secret(bot, chat_id, message_id, context)

    elif step == "api_secret":
        state["api_secret"] = text
        context.user_data[_STATE_KEY] = state
        await _prompt_cex_label(bot, chat_id, message_id, context)

    elif step == "label":
        state["label"] = text
        context.user_data[_STATE_KEY] = state
        await _submit_cex_key(bot, chat_id, message_id, context)

    elif step == "address":
        state["address"] = text
        context.user_data[_STATE_KEY] = state
        await _prompt_dex_label(bot, chat_id, message_id, context)

    elif step == "dex_label":
        state["label"] = text
        context.user_data[_STATE_KEY] = state
        await _submit_dex_wallet(bot, chat_id, message_id, context)


# ── callback router ────────────────────────────────────────────────────────────

async def handle_verify_key_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all vkey_* callback queries from the config callback handler."""
    query = update.callback_query
    data  = query.data
    await query.answer()

    state      = context.user_data.get(_STATE_KEY, {})
    message_id = state.get("message_id", query.message.message_id)
    chat_id    = state.get("chat_id",    query.message.chat_id)
    bot        = query.message.get_bot()

    if data == "vkey_menu":
        context.user_data.pop(_STATE_KEY, None)
        await show_verify_keys_menu(query, context)

    elif data == "vkey_add_cex":
        await show_cex_exchange_select(query, context)

    elif data.startswith("vkey_cex_ex:"):
        exchange = data.split(":", 1)[1]
        state.update({
            "exchange":   exchange,
            "message_id": query.message.message_id,
            "chat_id":    query.message.chat_id,
        })
        context.user_data[_STATE_KEY] = state
        await _prompt_api_key(bot, chat_id, message_id, context)

    elif data == "vkey_cex_submit":
        await _submit_cex_key(bot, chat_id, message_id, context)

    elif data == "vkey_add_dex":
        await show_dex_chain_select(query, context)

    elif data.startswith("vkey_dex_chain:"):
        chain = data.split(":", 1)[1]
        state.update({
            "chain":      chain,
            "message_id": query.message.message_id,
            "chat_id":    query.message.chat_id,
        })
        context.user_data[_STATE_KEY] = state
        await _prompt_address(bot, chat_id, message_id, context)

    elif data == "vkey_dex_submit":
        await _submit_dex_wallet(bot, chat_id, message_id, context)


# ── helpers ────────────────────────────────────────────────────────────────────

def _mask(s: str, front: int = 6, back: int = 4) -> str:
    """Truncate a key/address for safe display."""
    if len(s) <= front + back + 3:
        return s
    return f"{s[:front]}…{s[-back:]}"

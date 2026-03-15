"""
Verification keys management — stores read-only CEX keys and public DEX
wallet addresses in condor-web for trade verification.

UX mirrors api_keys.py: inline keyboard menu → field-by-field wizard →
secrets deleted immediately → confirmed via edited message.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.auth import restricted

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
_STATE_KEY  = "vkey_state"   # dict tracking current wizard state


# ── entry point ───────────────────────────────────────────────────────────────

@restricted
async def verify_keys_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /keys verify sub-flow — called from show_api_keys menu button."""
    from utils.telegram_helpers import create_mock_query_from_message
    mock_query = await create_mock_query_from_message(update, "Loading verification keys…")
    await show_verify_keys_menu(mock_query, context)


async def show_verify_keys_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main verification-keys menu: show stored keys + add options."""
    token = context.user_data.get(_TOKEN_KEY)

    if not token:
        text = (
            "🔑 *Verification Keys*\n\n"
            "No web token found\\. Run /webtoken first to link your "
            "Condor instance to condor\\.hummingbot\\.org\\."
        )
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_api_keys")]]
        await query.message.edit_text(
            text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Fetch existing keys from condor-web
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

    # Build message
    lines = ["🔑 *Verification Keys*\n"]

    if cex_rows:
        lines.append("*CEX \\(read\\-only\\):*")
        for k in cex_rows:
            ex   = _esc(k["exchange"].title())
            key  = _esc(_mask(k["apiKey"]))
            lbl  = f"  — {_esc(k['label'])}" if k.get("label") else ""
            lines.append(f"  • {ex}  `{key}`{lbl}")
    else:
        lines.append("_No CEX keys stored\\._")

    lines.append("")

    if dex_rows:
        lines.append("*DEX wallets:*")
        for w in dex_rows:
            chain   = _esc(w["chain"].title())
            addr    = _esc(_mask(w["address"], 8, 6))
            lbl     = f"  — {_esc(w['label'])}" if w.get("label") else ""
            lines.append(f"  • {chain}  `{addr}`{lbl}")
    else:
        lines.append("_No DEX wallets stored\\._")

    text = "\n".join(lines)

    keyboard = [
        [
            InlineKeyboardButton("➕ CEX key",    callback_data="vkey_add_cex"),
            InlineKeyboardButton("➕ DEX wallet", callback_data="vkey_add_dex"),
        ],
        [InlineKeyboardButton("« Back", callback_data="config_api_keys")],
    ]
    await query.message.edit_text(
        text, parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── CEX wizard ─────────────────────────────────────────────────────────────────

async def show_cex_exchange_select(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 1: pick exchange."""
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
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("« Back", callback_data="vkey_menu")])

    await query.message.edit_text(
        "🔑 *Add CEX Verification Key*\n\n_Select exchange:_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_cex_api_key_prompt(query, context: ContextTypes.DEFAULT_TYPE, exchange: str) -> None:
    """Step 2: enter API key."""
    state = context.user_data.get(_STATE_KEY, {})
    state.update({"step": "api_key", "exchange": exchange})
    context.user_data[_STATE_KEY] = state

    ex_esc = _esc(exchange.title())
    await query.message.edit_text(
        f"🔑 *{ex_esc} — Read\\-only API Key*\n\n"
        "_Enter your read\\-only API key\\._\n\n"
        "⚠️ Ensure this key has *no withdrawal permissions*\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data="vkey_menu")]]
        ),
    )


async def show_cex_secret_prompt(query_or_msg, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 3: enter API secret."""
    state = context.user_data.get(_STATE_KEY, {})
    state["step"] = "api_secret"
    context.user_data[_STATE_KEY] = state

    ex_esc  = _esc(state.get("exchange", "").title())
    key_esc = _esc(_mask(state.get("api_key", "")))

    text = (
        f"🔑 *{ex_esc}*\n\n"
        f"Key: `{key_esc}` ✅\n\n"
        "_Enter the API secret\\. Your message will be deleted immediately\\._"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="vkey_menu")]])

    if hasattr(query_or_msg, "message"):
        await query_or_msg.message.edit_text(text, parse_mode="MarkdownV2", reply_markup=kb)
    else:
        await query_or_msg.edit_text(text, parse_mode="MarkdownV2", reply_markup=kb)


async def show_cex_label_prompt(bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 4: optional label."""
    state = context.user_data.get(_STATE_KEY, {})
    state["step"] = "label"
    context.user_data[_STATE_KEY] = state

    ex_esc = _esc(state.get("exchange", "").title())
    text = (
        f"🔑 *{ex_esc}*\n\n"
        "Key: `****` ✅\n"
        "Secret: `****` ✅\n\n"
        "_Optional: enter a label for this key, or tap Skip\\._"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip", callback_data="vkey_cex_submit")],
        [InlineKeyboardButton("❌ Cancel", callback_data="vkey_menu")],
    ])
    await bot.edit_message_text(
        chat_id=chat_id, message_id=message_id,
        text=text, parse_mode="MarkdownV2", reply_markup=kb
    )


async def submit_cex_key(bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Final step: POST to condor-web."""
    state = context.user_data.pop(_STATE_KEY, {})
    token = context.user_data.get(_TOKEN_KEY)

    exchange   = state.get("exchange", "")
    api_key    = state.get("api_key", "")
    api_secret = state.get("api_secret", "")
    label      = state.get("label") or None

    try:
        from condor.webchat.client import SiteClient
        client = SiteClient(token)
        await client.add_cex_key(
            exchange=exchange,
            api_key=api_key,
            api_secret=api_secret,
            label=label,
        )
        await client.close()

        ex_esc  = _esc(exchange.title())
        key_esc = _esc(_mask(api_key))
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
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
        err = _esc(str(e)[:120])
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"❌ *Error saving key*\n\n`{err}`",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Back", callback_data="vkey_menu")]]
            ),
        )


# ── DEX wizard ─────────────────────────────────────────────────────────────────

async def show_dex_chain_select(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 1: pick chain."""
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
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("« Back", callback_data="vkey_menu")])

    await query.message.edit_text(
        "🔑 *Add DEX Wallet*\n\n_Select chain:_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_dex_address_prompt(query, context: ContextTypes.DEFAULT_TYPE, chain: str) -> None:
    """Step 2: enter public address."""
    state = context.user_data.get(_STATE_KEY, {})
    state.update({"step": "address", "chain": chain})
    context.user_data[_STATE_KEY] = state

    chain_esc = _esc(chain.title())
    await query.message.edit_text(
        f"🔑 *{chain_esc} Wallet*\n\n"
        "_Enter the public wallet address\\._\n\n"
        "ℹ️ Public address only — no private key required\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data="vkey_menu")]]
        ),
    )


async def show_dex_label_prompt(bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 3: optional label."""
    state = context.user_data.get(_STATE_KEY, {})
    state["step"] = "dex_label"
    context.user_data[_STATE_KEY] = state

    chain_esc = _esc(state.get("chain", "").title())
    addr_esc  = _esc(_mask(state.get("address", ""), 8, 6))
    await bot.edit_message_text(
        chat_id=chat_id, message_id=message_id,
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


async def submit_dex_wallet(bot, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Final step: POST to condor-web."""
    state = context.user_data.pop(_STATE_KEY, {})
    token = context.user_data.get(_TOKEN_KEY)

    chain   = state.get("chain", "")
    address = state.get("address", "")
    label   = state.get("label") or None

    try:
        from condor.webchat.client import SiteClient
        client = SiteClient(token)
        await client.add_dex_wallet(chain=chain, address=address, label=label)
        await client.close()

        chain_esc = _esc(chain.title())
        addr_esc  = _esc(_mask(address, 8, 6))
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
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
        err = _esc(str(e)[:120])
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"❌ *Error saving wallet*\n\n`{err}`",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Back", callback_data="vkey_menu")]]
            ),
        )


# ── text input router ──────────────────────────────────────────────────────────

async def handle_verify_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route free-text input during any vkey wizard step."""
    state = context.user_data.get(_STATE_KEY)
    if not state:
        return

    step       = state.get("step")
    message_id = state.get("message_id")
    chat_id    = state.get("chat_id") or update.effective_chat.id
    bot        = update.get_bot()
    text       = update.message.text.strip()

    # Always delete the user's message to keep chat clean (especially secrets)
    try:
        await update.message.delete()
    except Exception:
        pass

    if step == "api_key":
        state["api_key"] = text
        context.user_data[_STATE_KEY] = state
        # Mock a query-like object to reuse the edit helper
        from types import SimpleNamespace
        mock_msg = SimpleNamespace(
            edit_text=lambda t, parse_mode=None, reply_markup=None: bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=t, parse_mode=parse_mode, reply_markup=reply_markup
            )
        )
        mock_q = SimpleNamespace(message=mock_msg)
        await show_cex_secret_prompt(mock_q, context)

    elif step == "api_secret":
        state["api_secret"] = text
        context.user_data[_STATE_KEY] = state
        await show_cex_label_prompt(bot, chat_id, message_id, context)

    elif step == "label":
        state["label"] = text
        context.user_data[_STATE_KEY] = state
        await submit_cex_key(bot, chat_id, message_id, context)

    elif step == "address":
        state["address"] = text
        context.user_data[_STATE_KEY] = state
        await show_dex_label_prompt(bot, chat_id, message_id, context)

    elif step == "dex_label":
        state["label"] = text
        context.user_data[_STATE_KEY] = state
        await submit_dex_wallet(bot, chat_id, message_id, context)


# ── callback router ────────────────────────────────────────────────────────────

async def handle_verify_key_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all vkey_* callback queries."""
    query = update.callback_query
    data  = query.data
    await query.answer()

    if data == "vkey_menu":
        context.user_data.pop(_STATE_KEY, None)
        await show_verify_keys_menu(query, context)

    elif data == "vkey_add_cex":
        await show_cex_exchange_select(query, context)

    elif data.startswith("vkey_cex_ex:"):
        exchange = data.split(":", 1)[1]
        state = context.user_data.get(_STATE_KEY, {})
        state["message_id"] = query.message.message_id
        state["chat_id"]    = query.message.chat_id
        context.user_data[_STATE_KEY] = state
        await show_cex_api_key_prompt(query, context, exchange)

    elif data == "vkey_cex_submit":
        state      = context.user_data.get(_STATE_KEY, {})
        message_id = state.get("message_id", query.message.message_id)
        chat_id    = state.get("chat_id",    query.message.chat_id)
        await submit_cex_key(query.message.get_bot(), chat_id, message_id, context)

    elif data == "vkey_add_dex":
        await show_dex_chain_select(query, context)

    elif data.startswith("vkey_dex_chain:"):
        chain = data.split(":", 1)[1]
        state = context.user_data.get(_STATE_KEY, {})
        state["message_id"] = query.message.message_id
        state["chat_id"]    = query.message.chat_id
        context.user_data[_STATE_KEY] = state
        await show_dex_address_prompt(query, context, chain)

    elif data == "vkey_dex_submit":
        state      = context.user_data.get(_STATE_KEY, {})
        message_id = state.get("message_id", query.message.message_id)
        chat_id    = state.get("chat_id",    query.message.chat_id)
        await submit_dex_wallet(query.message.get_bot(), chat_id, message_id, context)


# ── helpers ────────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Escape text for MarkdownV2."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _mask(s: str, front: int = 6, back: int = 4) -> str:
    if len(s) <= front + back + 3:
        return s
    return f"{s[:front]}…{s[-back:]}"

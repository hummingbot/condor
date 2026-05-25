"""Telegram /performance command — strategy-wide agent performance report."""

from __future__ import annotations

import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config_manager import get_client
from condor.trading_agent.config import load_agent_config
from condor.trading_agent.engine import get_all_engines
from condor.trading_agent.performance_digest import (
    build_strategy_digest,
    format_performance_report,
    format_performance_report_html,
)
from condor.trading_agent.strategy import StrategyStore
from utils.auth import restricted

log = logging.getLogger(__name__)

_TRADING_AGENTS_ROOT = Path(__file__).resolve().parent.parent.parent / "trading_agents"


def _strategy_dir(slug: str) -> Path:
    return _TRADING_AGENTS_ROOT / slug


def _running_for_slug(slug: str) -> tuple[str | None, int | None]:
    """Return (agent_id, tick_count) if a live engine matches this strategy slug."""
    prefix = f"{slug}_"
    for eid, engine in get_all_engines().items():
        if eid.startswith(prefix) and not eid[len(prefix) :].startswith("e"):
            info = engine.get_info()
            return eid, info.get("tick_count")
    return None, None


async def _resolve_client_for_strategy(
    slug: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE
):
    """Pick API client: strategy config server first, else user's default."""
    agent_dir = _strategy_dir(slug)
    store = StrategyStore()
    strategy = store.get_by_slug(slug)
    defaults = strategy.default_config if strategy else None

    if agent_dir.is_dir():
        cfg = load_agent_config(agent_dir, defaults)
        if cfg.server_name:
            from config_manager import get_config_manager

            cm = get_config_manager()
            try:
                client = await cm.get_client(cfg.server_name)
                if client:
                    return client, cfg.total_amount_quote
            except Exception as e:
                log.warning("get_client(%s) failed: %s", cfg.server_name, e)

    client = await get_client(chat_id, context=context)
    total_quote = 200.0
    if defaults:
        total_quote = float(defaults.get("total_amount_quote") or total_quote)
    return client, total_quote


async def _send_performance_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    slug: str,
    session_filter: int | None = None,
) -> None:
    agent_dir = _strategy_dir(slug)
    if not agent_dir.is_dir():
        msg = update.message or (update.callback_query.message if update.callback_query else None)
        if msg:
            await msg.reply_text(f"No agent directory for {slug}")
        return

    chat_id = update.effective_chat.id
    client, total_quote = await _resolve_client_for_strategy(slug, chat_id, context)

    if not client:
        msg = update.message or update.callback_query.message
        if msg:
            await msg.reply_text(
                "No Hummingbot API server available. Configure a server in /config."
            )
        return

    running_id, running_tick = _running_for_slug(slug)
    if session_filter is not None:
        running_id = f"{slug}_{session_filter}"
        eng = get_all_engines().get(running_id)
        running_tick = eng.get_info().get("tick_count") if eng else None

    try:
        digest = await build_strategy_digest(
            client=client,
            slug=slug,
            agent_dir=agent_dir,
            total_amount_quote=total_quote,
            session_filter=session_filter,
            running_agent_id=running_id,
            running_tick=running_tick,
        )
    except Exception as e:
        log.exception("build_strategy_digest(%s) failed", slug)
        msg = update.message or update.callback_query.message
        if msg:
            await msg.reply_text(f"Failed to load performance: {e}")
        return

    text_html = format_performance_report_html(digest)
    text_plain = format_performance_report(digest)
    msg = update.message or update.callback_query.message
    if not msg:
        return
    await _reply_performance(msg, text_html, text_plain)


async def _reply_performance(msg, text_html: str, text_plain: str) -> None:
    chunks_html = _split_message(text_html, 4096) if len(text_html) > 4096 else [text_html]
    try:
        for chunk in chunks_html:
            await msg.reply_text(chunk, parse_mode=ParseMode.HTML)
        return
    except BadRequest:
        log.warning("HTML performance report failed, falling back to plain text")

    chunks_plain = _split_message(text_plain, 4096) if len(text_plain) > 4096 else [text_plain]
    for chunk in chunks_plain:
        await msg.reply_text(chunk)


@restricted
async def performance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /performance [slug] [session_num]."""
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.reply_text("Use /performance in a private chat.")
        return

    args = context.args or []
    store = StrategyStore()
    strategies = store.list_all()

    if not args:
        if not strategies:
            await update.message.reply_text("No trading agent strategies found.")
            return
        buttons = []
        engines = get_all_engines()
        for s in strategies[:12]:
            slug = s.slug
            if not (_TRADING_AGENTS_ROOT / slug).is_dir():
                slug = s.agent_dir.name
            label = s.name or slug
            running = any(eid.startswith(f"{slug}_") for eid in engines)
            if running:
                label = f"● {label}"
            buttons.append(
                [InlineKeyboardButton(label, callback_data=f"perf:{slug}")]
            )
        await update.message.reply_text(
            "Select a strategy for performance report:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    slug = args[0].strip()
    session_filter: int | None = None
    if len(args) >= 2:
        try:
            session_filter = int(args[1])
        except ValueError:
            await update.message.reply_text("Session number must be an integer.")
            return

    strategy = store.get_by_slug(slug)
    if not strategy and not _strategy_dir(slug).is_dir():
        await update.message.reply_text(f"Strategy not found: {slug}")
        return

    await _send_performance_report(update, context, slug, session_filter=session_filter)


@restricted
async def performance_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle perf:slug inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith("perf:"):
        return
    slug = data[5:]
    await _send_performance_report(update, context, slug)


def _split_message(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks

"""/memory — review and prune what each assistant remembers about the user.

Memory is **per-assistant** (FEAT-003): the chat ``condor`` and every trading
agent keep their own isolated memory. This view shows one section per assistant
that has a non-empty memory, with inline buttons to delete each entry. Deletes go
through MemoryStore.delete(..., source="user") so they are audited in that
assistant's own audit.log. (Skills are NOT shown here — they are read-only
playbooks general to the assistant, authored in the repo, not learned per user.)

Delete callbacks carry the assistant so the right store is mutated:
``memory:del:{agent_slug}:{name}`` — ``agent_slug`` empty for the chat.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor.memory import MemoryStore, iter_user_stores
from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

# Per-assistant button caps, plus a global cap so many trading agents can't blow
# past Telegram's keyboard limits.
_MAX_BUTTONS_PER_STORE = 6
_MAX_BUTTONS_TOTAL = 18
_MAX_AUDIT = 3


def _build_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build the /memory message text + keyboard, grouped by assistant."""
    stores = iter_user_stores(user_id)

    lines = ["🧠 *Memory by assistant*", ""]
    keyboard: list[list[InlineKeyboardButton]] = []
    buttons_left = _MAX_BUTTONS_TOTAL
    shown = 0

    for label, agent_slug, _root in stores:
        mem = MemoryStore(user_id, agent_slug)
        memories = mem.search("", limit=_MAX_BUTTONS_PER_STORE)
        if not memories:
            continue  # skip stores that exist but hold nothing
        shown += 1

        label_e = escape_markdown_v2(label)
        lines.append(f"━━━ *{label_e}* ━━━")

        lines.append(f"_Remembers \\({len(memories)}\\):_")
        for m in memories:
            name = escape_markdown_v2(m["name"])
            desc = escape_markdown_v2(m["description"])
            mtype = escape_markdown_v2(m["type"])
            lines.append(f"• *{name}* — {desc} _\\({mtype}\\)_")

        audit = mem.audit(limit=_MAX_AUDIT)
        if audit:
            lines.append("_Recent changes:_")
            for e in reversed(audit):  # newest first
                action = escape_markdown_v2(e.get("action", ""))
                target = (
                    e.get("target", "").replace("memory:", "").replace("skill:", "")
                )
                target = escape_markdown_v2(target)
                source = escape_markdown_v2(e.get("source", ""))
                lines.append(f"• {action} {target} _by {source}_")

        lines.append("")

        # Delete buttons for this assistant (bounded globally).
        aslug = agent_slug or ""
        for m in memories:
            if buttons_left <= 0:
                break
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🗑 [{label}] {m['name']}",
                        callback_data=f"memory:del:{aslug}:{m['name']}",
                    )
                ]
            )
            buttons_left -= 1

    if shown == 0:
        lines.append(
            "No memories yet\\. Each assistant \\(the chat and each trading "
            "agent\\) builds its own as it learns about you\\."
        )

    keyboard.append(
        [InlineKeyboardButton("🔄 Refresh", callback_data="memory:refresh")]
    )

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


@restricted
async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /memory — show each assistant's memory, grouped."""
    from handlers import clear_all_input_states

    clear_all_input_states(context)

    user_id = update.effective_user.id
    text, keyboard = _build_view(user_id)
    await update.message.reply_text(
        text, parse_mode="MarkdownV2", reply_markup=keyboard
    )


@restricted
async def memory_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle memory:* callbacks (delete a memory in a store, refresh)."""
    query = update.callback_query
    user_id = update.effective_user.id

    action = query.data.split(":", 1)[1] if ":" in query.data else query.data

    if action.startswith("del:"):
        # del:{agent_slug}:{name} — agent_slug empty => chat (None).
        _, aslug, name = action.split(":", 2)
        agent_slug = aslug or None
        deleted = MemoryStore(user_id, agent_slug).delete(name, source="user")
        await query.answer("Deleted" if deleted else "Not found", show_alert=False)
    else:
        await query.answer()

    text, keyboard = _build_view(user_id)
    try:
        await query.edit_message_text(
            text, parse_mode="MarkdownV2", reply_markup=keyboard
        )
    except Exception:
        # Telegram raises if the message is unchanged (e.g. refresh with no diff).
        pass


__all__ = ["memory_command", "memory_callback_handler"]

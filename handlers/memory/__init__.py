"""/memory — review and prune what the agent remembers about the user.

Shows the user's memory index plus the most recent audit entries, with an
inline button to delete each memory. Deletes go through MemoryStore.delete(...,
source="user") so they are themselves audited.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor.memory import MemoryStore
from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

_MAX_BUTTONS = 12
_MAX_AUDIT = 5


def _build_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build the /memory message text + keyboard for a user."""
    store = MemoryStore(user_id)
    memories = store.search("", limit=_MAX_BUTTONS)

    lines = ["🧠 *User Memory*", ""]
    if not memories:
        lines.append(
            "No memories yet\\. I'll save stable preferences and facts as I learn them\\."
        )
    else:
        lines.append(f"What I remember about you \\({len(memories)}\\):")
        lines.append("")
        for m in memories:
            name = escape_markdown_v2(m["name"])
            desc = escape_markdown_v2(m["description"])
            mtype = escape_markdown_v2(m["type"])
            lines.append(f"• *{name}* — {desc} _\\({mtype}\\)_")

    audit = store.audit(limit=_MAX_AUDIT)
    if audit:
        lines.append("")
        lines.append("_Recent changes:_")
        for e in reversed(audit):  # newest first
            action = escape_markdown_v2(e.get("action", ""))
            target = escape_markdown_v2(e.get("target", "").replace("memory:", ""))
            source = escape_markdown_v2(e.get("source", ""))
            lines.append(f"• {action} {target} _by {source}_")

    keyboard: list[list[InlineKeyboardButton]] = []
    for m in memories:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🗑 {m['name']}", callback_data=f"memory:delete:{m['name']}"
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton("🔄 Refresh", callback_data="memory:refresh")]
    )

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


@restricted
async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /memory — show the user's memory index and audit."""
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
    """Handle memory:* callbacks (delete a memory, refresh the view)."""
    query = update.callback_query
    user_id = update.effective_user.id

    action = query.data.split(":", 1)[1] if ":" in query.data else query.data

    if action.startswith("delete:"):
        name = action[len("delete:") :]
        deleted = MemoryStore(user_id).delete(name, source="user")
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

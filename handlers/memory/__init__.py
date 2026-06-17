"""/memory — review and prune what the agent remembers and the skills it built.

Shows the user's memory index, their skill playbooks, and the most recent audit
entries, with an inline button to delete each one. Deletes go through
MemoryStore/SkillStore.delete(..., source="user") so they are themselves audited
(memories and skills share the same audit.log).
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor.memory import MemoryStore, SkillStore
from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

_MAX_BUTTONS = 12
_MAX_AUDIT = 5


def _build_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build the /memory message text + keyboard for a user."""
    store = MemoryStore(user_id)
    skill_store = SkillStore(user_id)
    memories = store.search("", limit=_MAX_BUTTONS)
    skills = skill_store.search("", limit=_MAX_BUTTONS)

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

    # Skills (playbooks) — same store family, shown in their own block.
    lines.append("")
    lines.append("📓 *Skills \\(playbooks\\)*")
    if not skills:
        lines.append("")
        lines.append("No skills yet\\. I'll save reusable playbooks as I build them\\.")
    else:
        lines.append("")
        for s in skills:
            name = escape_markdown_v2(s["name"])
            when = escape_markdown_v2(s.get("when_to_use", ""))
            line = f"• *{name}* — {when}"
            ref = s.get("references_routine")
            if ref:
                ref_e = escape_markdown_v2(ref)
                ok = "✅" if s.get("routine_ok") else "⚠️"
                line += f" _\\(→ {ref_e} {ok}\\)_"
            lines.append(line)

    audit = store.audit(limit=_MAX_AUDIT)
    if audit:
        lines.append("")
        lines.append("_Recent changes:_")
        for e in reversed(audit):  # newest first
            action = escape_markdown_v2(e.get("action", ""))
            target = e.get("target", "").replace("memory:", "").replace("skill:", "")
            target = escape_markdown_v2(target)
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
    for s in skills:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🗑 📓 {s['name']}",
                    callback_data=f"memory:delete_skill:{s['name']}",
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

    if action.startswith("delete_skill:"):
        name = action[len("delete_skill:") :]
        deleted = SkillStore(user_id).delete(name, source="user")
        await query.answer("Deleted" if deleted else "Not found", show_alert=False)
    elif action.startswith("delete:"):
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

"""/memory — review and prune what each assistant remembers and the skills it built.

Memory and skills are **per-assistant** (FEAT-003): the chat ``condor`` and every
trading agent keep their own isolated store. This view shows one section per
assistant that has a non-empty store, with inline buttons to delete each memory
or skill. Deletes go through MemoryStore/SkillStore.delete(..., source="user") so
they are themselves audited in that assistant's own audit.log (memory and skills
share the same log per assistant).

Delete callbacks carry the assistant so the right store is mutated:
``memory:del:{kind}:{agent_slug}:{name}`` — kind ``m`` (memory) or ``s`` (skill),
``agent_slug`` empty for the chat.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor.memory import MemoryStore, SkillStore, iter_user_stores
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

    lines = ["🧠 *Memory & Skills by assistant*", ""]
    keyboard: list[list[InlineKeyboardButton]] = []
    buttons_left = _MAX_BUTTONS_TOTAL
    shown = 0

    for label, agent_slug, _root in stores:
        mem = MemoryStore(user_id, agent_slug)
        skill = SkillStore(user_id, agent_slug)
        memories = mem.search("", limit=_MAX_BUTTONS_PER_STORE)
        skills = skill.search("", limit=_MAX_BUTTONS_PER_STORE)
        if not memories and not skills:
            continue  # skip stores that exist but hold nothing
        shown += 1

        label_e = escape_markdown_v2(label)
        lines.append(f"━━━ *{label_e}* ━━━")

        if memories:
            lines.append(f"_Remembers \\({len(memories)}\\):_")
            for m in memories:
                name = escape_markdown_v2(m["name"])
                desc = escape_markdown_v2(m["description"])
                mtype = escape_markdown_v2(m["type"])
                lines.append(f"• *{name}* — {desc} _\\({mtype}\\)_")

        if skills:
            lines.append("_Skills \\(playbooks\\):_")
            for s in skills:
                name = escape_markdown_v2(s["name"])
                when = escape_markdown_v2(s.get("when_to_use", ""))
                line = f"• 📓 *{name}* — {when}"
                ref = s.get("references_routine")
                if ref:
                    ref_e = escape_markdown_v2(ref)
                    ok = "✅" if s.get("routine_ok") else "⚠️"
                    line += f" _\\(→ {ref_e} {ok}\\)_"
                lines.append(line)

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
                        callback_data=f"memory:del:m:{aslug}:{m['name']}",
                    )
                ]
            )
            buttons_left -= 1
        for s in skills:
            if buttons_left <= 0:
                break
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🗑 📓 [{label}] {s['name']}",
                        callback_data=f"memory:del:s:{aslug}:{s['name']}",
                    )
                ]
            )
            buttons_left -= 1

    if shown == 0:
        lines.append(
            "No memories or skills yet\\. Each assistant \\(the chat and each "
            "trading agent\\) builds its own as it learns\\."
        )

    keyboard.append(
        [InlineKeyboardButton("🔄 Refresh", callback_data="memory:refresh")]
    )

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


@restricted
async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /memory — show each assistant's memory + skills, grouped."""
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
    """Handle memory:* callbacks (delete a memory/skill in a store, refresh)."""
    query = update.callback_query
    user_id = update.effective_user.id

    action = query.data.split(":", 1)[1] if ":" in query.data else query.data

    if action.startswith("del:"):
        # del:{kind}:{agent_slug}:{name} — agent_slug empty => chat (None).
        _, kind, aslug, name = action.split(":", 3)
        agent_slug = aslug or None
        store = (
            SkillStore(user_id, agent_slug)
            if kind == "s"
            else MemoryStore(user_id, agent_slug)
        )
        deleted = store.delete(name, source="user")
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

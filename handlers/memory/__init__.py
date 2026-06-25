"""/memory — review and prune what each assistant remembers about the user.

Memory is **per-assistant** (FEAT-003): the chat ``condor`` and every trading
agent keep their own isolated memory. This view shows one section per assistant
that has a non-empty memory, with inline buttons to delete each entry. Deletes go
through MemoryStore.delete(..., source="user") so they are audited in that
assistant's own audit.log. (Skills are NOT shown here — they are read-only
playbooks general to the assistant, authored in the repo, not learned per user.)

Delete callbacks reference a memory by a short integer index into a per-render
map (``memory:del:{idx}``) instead of embedding the ``(agent_slug, name)`` pair
directly: assistant slugs and memory names are uncapped, so embedding both could
push ``callback_data`` past Telegram's hard 64-byte limit and make the whole view
fail to render. The map is stored in ``context.user_data`` and rebuilt on every
render, so it always matches the buttons currently on screen.
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

# Key under which the per-render delete map ({idx: (agent_slug, name)}) is cached
# in context.user_data so delete callbacks can stay a tiny ``memory:del:{idx}``.
_DEL_MAP_KEY = "memory_del_map"


def _build_view(
    user_id: int, context: ContextTypes.DEFAULT_TYPE
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the /memory message text + keyboard, grouped by assistant.

    Populates ``context.user_data[_DEL_MAP_KEY]`` with a fresh
    ``{idx: (agent_slug, name)}`` map so each delete button can carry only a
    short index instead of the (uncapped) slug + name pair.
    """
    stores = iter_user_stores(user_id)

    lines = ["🧠 *Memory by assistant*", ""]
    keyboard: list[list[InlineKeyboardButton]] = []
    del_map: dict[int, tuple[str | None, str]] = {}
    next_idx = 0
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

        # Delete buttons for this assistant (bounded globally). callback_data
        # carries only a short index into del_map so it can never exceed
        # Telegram's 64-byte limit regardless of slug/name length.
        for m in memories:
            if buttons_left <= 0:
                break
            del_map[next_idx] = (agent_slug or None, m["name"])
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🗑 [{label}] {m['name']}",
                        callback_data=f"memory:del:{next_idx}",
                    )
                ]
            )
            next_idx += 1
            buttons_left -= 1

    if shown == 0:
        lines.append(
            "No memories yet\\. Each assistant \\(the chat and each trading "
            "agent\\) builds its own as it learns about you\\."
        )

    keyboard.append(
        [InlineKeyboardButton("🔄 Refresh", callback_data="memory:refresh")]
    )

    context.user_data[_DEL_MAP_KEY] = del_map

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


@restricted
async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /memory — show each assistant's memory, grouped."""
    from handlers import clear_all_input_states

    clear_all_input_states(context)

    user_id = update.effective_user.id
    text, keyboard = _build_view(user_id, context)
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
        # del:{idx} — idx is a key into the map built by the last _build_view.
        del_map = context.user_data.get(_DEL_MAP_KEY) or {}
        try:
            idx = int(action.split(":", 1)[1])
            agent_slug, name = del_map[idx]
        except (ValueError, KeyError):
            # Stale/unknown button (e.g. after a restart) — nothing to delete.
            await query.answer("Not found", show_alert=False)
        else:
            deleted = MemoryStore(user_id, agent_slug).delete(name, source="user")
            await query.answer("Deleted" if deleted else "Not found", show_alert=False)
    else:
        await query.answer()

    text, keyboard = _build_view(user_id, context)
    try:
        await query.edit_message_text(
            text, parse_mode="MarkdownV2", reply_markup=keyboard
        )
    except Exception:
        # Telegram raises if the message is unchanged (e.g. refresh with no diff).
        pass


__all__ = ["memory_command", "memory_callback_handler"]

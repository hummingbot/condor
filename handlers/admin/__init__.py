"""
Admin panel for user and access management.
Only accessible by admin users.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.auth import admin_required
from config_manager import (
    get_config_manager,
    UserRole,
)
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)


def _get_admin_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the admin menu keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("👥 Pending Requests", callback_data="admin:pending"),
            InlineKeyboardButton("📋 All Users", callback_data="admin:users"),
        ],
        [
            InlineKeyboardButton("📜 Audit Log", callback_data="admin:audit"),
            InlineKeyboardButton("📊 Stats", callback_data="admin:stats"),
        ],
        [
            InlineKeyboardButton("« Back", callback_data="config_back"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _format_user_role_badge(role: str) -> str:
    """Get role badge emoji."""
    badges = {
        UserRole.ADMIN.value: "👑",
        UserRole.USER.value: "✓",
        UserRole.PENDING.value: "⏳",
        UserRole.BLOCKED.value: "🚫",
    }
    return badges.get(role, "?")


def _format_timestamp(ts: float) -> str:
    """Format timestamp for display."""
    if not ts:
        return "N/A"
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M")


@admin_required
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /admin command - show admin panel."""
    from handlers import clear_all_input_states
    clear_all_input_states(context)

    cm = get_config_manager()
    pending_count = len(cm.get_pending_users())
    total_users = len(cm.get_all_users())

    message = (
        "🔐 *Admin Panel*\n\n"
        f"👥 Total Users: {total_users}\n"
        f"⏳ Pending Requests: {pending_count}\n\n"
        "Select an option below:"
    )

    await update.message.reply_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=_get_admin_menu_keyboard()
    )


@admin_required
async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin panel callbacks."""
    query = update.callback_query

    data = query.data

    # Handle config_admin entry point (from /config menu)
    if data == "config_admin":
        await query.answer()
        await _show_admin_menu(query, context)
        return

    await query.answer()
    action = data.split(":", 1)[1] if ":" in data else data

    if action == "menu" or action == "back":
        await _show_admin_menu(query, context)
    elif action == "pending":
        await _show_pending_users(query, context)
    elif action == "users":
        await _show_all_users(query, context)
    elif action == "audit":
        await _show_audit_log(query, context)
    elif action == "stats":
        await _show_stats(query, context)
    elif action.startswith("approve_"):
        user_id = int(action.replace("approve_", ""))
        await _approve_user(query, context, user_id)
    elif action.startswith("reject_"):
        user_id = int(action.replace("reject_", ""))
        await _reject_user(query, context, user_id)
    elif action.startswith("block_"):
        user_id = int(action.replace("block_", ""))
        await _block_user(query, context, user_id)
    elif action.startswith("unblock_"):
        user_id = int(action.replace("unblock_", ""))
        await _unblock_user(query, context, user_id)
    elif action.startswith("user_"):
        user_id = int(action.replace("user_", ""))
        await _show_user_details(query, context, user_id)


async def _show_admin_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show main admin menu."""
    cm = get_config_manager()
    pending_count = len(cm.get_pending_users())
    total_users = len(cm.get_all_users())

    message = (
        "🔐 *Admin Panel*\n\n"
        f"👥 Total Users: {total_users}\n"
        f"⏳ Pending Requests: {pending_count}\n\n"
        "Select an option below:"
    )

    await query.edit_message_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=_get_admin_menu_keyboard()
    )


async def _show_pending_users(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show pending user approval list."""
    cm = get_config_manager()
    pending = cm.get_pending_users()

    if not pending:
        message = (
            "👥 *Pending Requests*\n\n"
            "No pending access requests\\."
        )
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin:back")]]
        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    message = f"👥 *Pending Requests* \\({len(pending)}\\)\n\n"

    keyboard = []
    for user in pending:
        user_id = user['user_id']
        username = user.get('username') or 'N/A'
        created = _format_timestamp(user.get('created_at', 0))

        message += f"• `{user_id}` \\(@{escape_markdown_v2(username)}\\)\n"
        message += f"  Requested: {escape_markdown_v2(created)}\n\n"

        keyboard.append([
            InlineKeyboardButton(f"✓ Approve {user_id}", callback_data=f"admin:approve_{user_id}"),
            InlineKeyboardButton(f"✕ Reject", callback_data=f"admin:reject_{user_id}"),
        ])

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin:back")])

    await query.edit_message_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _show_all_users(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all users list."""
    cm = get_config_manager()
    users = cm.get_all_users()

    if not users:
        message = "📋 *All Users*\n\nNo users registered\\."
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin:back")]]
        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Group by role
    by_role = {}
    for user in users:
        role = user.get('role', 'unknown')
        by_role.setdefault(role, []).append(user)

    message = f"📋 *All Users* \\({len(users)}\\)\n\n"

    # Show in order: admin, user, pending, blocked
    role_order = [UserRole.ADMIN.value, UserRole.USER.value, UserRole.PENDING.value, UserRole.BLOCKED.value]

    keyboard = []
    for role in role_order:
        role_users = by_role.get(role, [])
        if not role_users:
            continue

        badge = _format_user_role_badge(role)
        message += f"*{badge} {role.title()}* \\({len(role_users)}\\)\n"

        for user in role_users[:5]:  # Limit to 5 per role in message
            user_id = user['user_id']
            username = user.get('username') or 'N/A'
            message += f"  • `{user_id}` @{escape_markdown_v2(username)}\n"

            keyboard.append([
                InlineKeyboardButton(
                    f"{badge} {user_id} (@{username[:10]})",
                    callback_data=f"admin:user_{user_id}"
                )
            ])

        if len(role_users) > 5:
            message += f"  _\\.\\.\\. and {len(role_users) - 5} more_\n"

        message += "\n"

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin:back")])

    await query.edit_message_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _show_user_details(query, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Show details for a specific user."""
    cm = get_config_manager()
    user = cm.get_user(user_id)

    if not user:
        await query.answer("User not found", show_alert=True)
        return

    role = user.get('role', 'unknown')
    username = user.get('username') or 'N/A'
    created = _format_timestamp(user.get('created_at', 0))
    approved_at = _format_timestamp(user.get('approved_at'))
    approved_by = user.get('approved_by')
    notes = user.get('notes') or 'None'

    badge = _format_user_role_badge(role)

    message = (
        f"👤 *User Details*\n\n"
        f"*ID:* `{user_id}`\n"
        f"*Username:* @{escape_markdown_v2(username)}\n"
        f"*Role:* {badge} {escape_markdown_v2(role.title())}\n"
        f"*Created:* {escape_markdown_v2(created)}\n"
    )

    if approved_at != "N/A":
        message += f"*Approved:* {escape_markdown_v2(approved_at)}\n"
    if approved_by:
        message += f"*Approved By:* `{approved_by}`\n"
    if notes != 'None':
        message += f"*Notes:* {escape_markdown_v2(notes)}\n"

    # Show servers owned by user
    owned_servers = cm.get_owned_servers(user_id)
    if owned_servers:
        message += f"\n*Owned Servers:* {len(owned_servers)}\n"
        for s in owned_servers[:3]:
            message += f"  • {escape_markdown_v2(s)}\n"
        if len(owned_servers) > 3:
            message += f"  _\\.\\.\\. and {len(owned_servers) - 3} more_\n"

    # Show shared servers
    shared_servers = cm.get_shared_servers(user_id)
    if shared_servers:
        message += f"\n*Shared Access:* {len(shared_servers)}\n"
        for s, perm in shared_servers[:3]:
            message += f"  • {escape_markdown_v2(s)} \\({perm.value}\\)\n"

    # Build action buttons based on role
    keyboard = []
    admin_id = cm.admin_id

    if role == UserRole.PENDING.value:
        keyboard.append([
            InlineKeyboardButton("✓ Approve", callback_data=f"admin:approve_{user_id}"),
            InlineKeyboardButton("✕ Reject", callback_data=f"admin:reject_{user_id}"),
        ])
    elif role == UserRole.BLOCKED.value:
        keyboard.append([
            InlineKeyboardButton("🔓 Unblock", callback_data=f"admin:unblock_{user_id}"),
        ])
    elif role == UserRole.USER.value and user_id != admin_id:
        keyboard.append([
            InlineKeyboardButton("🚫 Block", callback_data=f"admin:block_{user_id}"),
        ])

    keyboard.append([InlineKeyboardButton("🔙 Back to Users", callback_data="admin:users")])

    await query.edit_message_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _approve_user(query, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Approve a pending user."""
    cm = get_config_manager()
    admin_id = query.from_user.id

    if cm.approve_user(user_id, admin_id):
        # Notify the user
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ *Access Approved\\!*\n\n"
                    "Your access request has been approved\\.\n"
                    "Use /start to begin\\."
                ),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            logger.warning(f"Failed to notify user {user_id} of approval: {e}")

        await query.answer("User approved!", show_alert=True)
    else:
        await query.answer("Failed to approve user", show_alert=True)

    # Refresh pending list
    await _show_pending_users(query, context)


async def _reject_user(query, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Reject a pending user."""
    cm = get_config_manager()
    admin_id = query.from_user.id

    if cm.reject_user(user_id, admin_id):
        await query.answer("User rejected", show_alert=True)
    else:
        await query.answer("Failed to reject user", show_alert=True)

    # Refresh pending list
    await _show_pending_users(query, context)


async def _block_user(query, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Block a user."""
    cm = get_config_manager()
    admin_id = query.from_user.id

    if cm.block_user(user_id, admin_id):
        await query.answer("User blocked", show_alert=True)
    else:
        await query.answer("Failed to block user", show_alert=True)

    # Show user details
    await _show_user_details(query, context, user_id)


async def _unblock_user(query, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Unblock a user."""
    cm = get_config_manager()
    admin_id = query.from_user.id

    if cm.unblock_user(user_id, admin_id):
        await query.answer("User unblocked (now pending)", show_alert=True)
    else:
        await query.answer("Failed to unblock user", show_alert=True)

    # Show user details
    await _show_user_details(query, context, user_id)


async def _show_audit_log(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent audit log entries."""
    cm = get_config_manager()
    entries = cm.get_audit_log(limit=10)

    if not entries:
        message = "📜 *Audit Log*\n\nNo entries yet\\."
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin:back")]]
        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    message = "📜 *Audit Log* \\(Recent 10\\)\n\n"

    for entry in entries:
        ts = _format_timestamp(entry.get('timestamp', 0))
        action = entry.get('action', 'unknown')
        actor = entry.get('actor_id', 0)
        target_type = entry.get('target_type', '')
        target_id = entry.get('target_id', '')

        # Format action nicely
        action_display = action.replace('_', ' ').title()

        message += f"• *{escape_markdown_v2(ts)}*\n"
        message += f"  {escape_markdown_v2(action_display)}\n"
        message += f"  By: `{actor}` \\| {escape_markdown_v2(target_type)}: `{escape_markdown_v2(str(target_id))}`\n\n"

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin:back")]]

    await query.edit_message_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _show_stats(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show system statistics."""
    cm = get_config_manager()
    from config_manager import get_config_manager

    users = cm.get_all_users()
    servers = list(get_config_manager().list_servers().keys())

    # Count by role
    role_counts = {}
    for user in users:
        role = user.get('role', 'unknown')
        role_counts[role] = role_counts.get(role, 0) + 1

    # Count servers by owner
    server_owners = {}
    for server_name in servers:
        owner = cm.get_server_owner(server_name)
        if owner:
            server_owners[owner] = server_owners.get(owner, 0) + 1

    message = (
        "📊 *System Statistics*\n\n"
        f"*Users*\n"
        f"  👑 Admins: {role_counts.get(UserRole.ADMIN.value, 0)}\n"
        f"  ✓ Approved: {role_counts.get(UserRole.USER.value, 0)}\n"
        f"  ⏳ Pending: {role_counts.get(UserRole.PENDING.value, 0)}\n"
        f"  🚫 Blocked: {role_counts.get(UserRole.BLOCKED.value, 0)}\n\n"
        f"*Servers*\n"
        f"  Total: {len(servers)}\n"
        f"  With owners: {len(server_owners)}\n\n"
        f"*Audit Log*\n"
        f"  Entries: {len(cm.cm.get('audit_log', []))}\n"
    )

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin:back")]]

    await query.edit_message_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    filters,
)

from backend_api_manager.client import BackendAPIClient
from backend_api_manager.models import StartBotAction, StopBotAction

# Define states
SELECT_BOT, SELECT_ACTION = range(2)


async def list_active_bots(update: Update, context: CallbackContext) -> int:
    backend_api_client = BackendAPIClient.get_instance()
    active_bots = await backend_api_client.async_active_containers()

    keyboard = [
        [InlineKeyboardButton(bot["name"], callback_data=bot["name"])]
        for bot in active_bots["active_instances"]
    ]
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Select a bot to control:", reply_markup=reply_markup
    )
    return SELECT_BOT


async def bot_selected(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        return await cancel(update, context)

    context.user_data["selected_bot"] = query.data
    await show_bot_actions(update, context)
    return SELECT_ACTION


async def show_bot_actions(update: Update, context: CallbackContext):
    # Actions split into two rows
    actions_row_1 = ["Start", "Stop", "Status"]
    actions_row_2 = ["History", "Remove"]

    keyboard = [
        [
            InlineKeyboardButton(action, callback_data=action.lower())
            for action in actions_row_1
        ],
        [
            InlineKeyboardButton(action, callback_data=action.lower())
            for action in actions_row_2
        ],
        [
            InlineKeyboardButton("Cancel", callback_data="cancel")
        ],  # Cancel button in its own row
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.reply_text(
        text=f'Selected bot: {context.user_data["selected_bot"]}. Choose an action:',
        reply_markup=reply_markup,
    )


async def execute_bot_action(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    selected_bot = context.user_data.get("selected_bot")
    selected_action = query.data

    if selected_action == "cancel":
        return await cancel(update, context)

    backend_api_client = BackendAPIClient.get_instance()
    if selected_action == "start":
        # Implement start bot logic
        response = await backend_api_client.async_start_bot(
            StartBotAction(bot_name=selected_bot, async_backend=True)
        )
        await query.edit_message_text(text=f"Bot successfully started: {response}")
    elif selected_action == "stop":
        # Implement stop bot logic
        response = await backend_api_client.async_stop_bot(
            StopBotAction(bot_name=selected_bot)
        )
        await query.edit_message_text(text=f"Bot successfully stopped: {response}")
    elif selected_action == "status":
        # Implement status logic
        bot_status = await backend_api_client.async_get_bot_status(
            bot_name=selected_bot
        )
        await query.edit_message_text(
            text=f"Bot status for {selected_bot}: {bot_status}"
        )
    elif selected_action == "history":
        # Implement history logic
        bot_history = await backend_api_client.async_get_bot_history(
            bot_name=selected_bot
        )
        await query.edit_message_text(
            text=f"Bot history for {selected_bot}: {bot_history}"
        )

    elif selected_action == "remove":
        # Implement remove bot logic
        stop_report = await backend_api_client.async_stop_bot(
            StopBotAction(bot_name=selected_bot)
        )
        while stop_report["status"] == "error":
            await asyncio.sleep(1)
            stop_report = await backend_api_client.async_stop_bot(
                StopBotAction(bot_name=selected_bot)
            )
        response = await backend_api_client.async_remove_container(
            container_name=selected_bot
        )
        await query.edit_message_text(text=f"Bot successfully removed: {response}")
        return ConversationHandler.END
    await show_bot_actions(update, context)
    return SELECT_ACTION


async def handle_action_response(query, response, selected_bot, update, context):
    if response["status"] == "error":
        await query.edit_message_text(text=f"Error: {response['response']}")
    else:
        await query.edit_message_text(
            text=f"Action successfully executed for {selected_bot}."
        )
        await show_bot_actions(update, context)


async def cancel(update: Update, context: CallbackContext) -> int:
    await update.callback_query.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


def get_control_bots_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("control_bots", list_active_bots)],
        states={
            SELECT_BOT: [CallbackQueryHandler(bot_selected)],
            SELECT_ACTION: [CallbackQueryHandler(execute_bot_action)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern="^cancel$"),
        ],
    )

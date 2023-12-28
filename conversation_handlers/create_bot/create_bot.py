from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Define states
ASKING_NAME, ASKING_IMAGE, CUSTOM_IMAGE, ASKING_SCRIPT, ASKING_CONFIG = range(5)


# Example list of available images and scripts
AVAILABLE_IMAGES = [
    "hummingbot/hummingbot:latest",
    "hummingbot/hummingbot:development",
    "custom",
]
AVAILABLE_SCRIPTS = ["script1", "script2", "script3"]


async def ask_bot_name(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Please enter the name for your new bot:")
    return ASKING_NAME


async def ask_image(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton(image, callback_data=image)] for image in AVAILABLE_IMAGES
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Choose a Docker image:", reply_markup=reply_markup)
    return ASKING_IMAGE


async def image_chosen(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "custom":
        await query.edit_message_text(text="Please enter the custom Docker image name:")
        return CUSTOM_IMAGE
    else:
        context.user_data["image"] = query.data
        await query.edit_message_text(text=f"Selected image: {query.data}")
        await update.callback_query.message.reply_text(
            "Do you want to autostart the bot with a script? (yes/no)"
        )
        return ASKING_SCRIPT


async def custom_image(update: Update, context: CallbackContext) -> int:
    context.user_data["image"] = update.message.text
    await update.message.reply_text(
        f"Custom image set to: {update.message.text}\nDo you want to autostart the bot with a script? (yes/no)"
    )
    return ASKING_SCRIPT


async def ask_script(update: Update, context: CallbackContext) -> int:
    script_response = update.message.text.lower()
    if script_response == "yes":
        keyboard = [
            [InlineKeyboardButton(script, callback_data=script)]
            for script in AVAILABLE_SCRIPTS
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Choose a script to autostart:", reply_markup=reply_markup
        )
    else:
        context.user_data["script"] = None
        await update.message.reply_text(
            "Do you want to autostart the script with a config? (yes/no)"
        )
        return ASKING_CONFIG


async def script_chosen(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["script"] = query.data
    await query.edit_message_text(text=f"Selected script: {query.data}")
    await update.callback_query.message.reply_text(
        "Do you want to autostart the script with a config? (yes/no)"
    )
    return ASKING_CONFIG


async def ask_config(update: Update, context: CallbackContext) -> int:
    config_response = update.message.text.lower()
    context.user_data["config"] = config_response == "yes"
    return await summarize_and_end(update, context)


async def summarize_and_end(update: Update, context: CallbackContext) -> int:
    bot_name = context.user_data.get("bot_name", "N/A")
    image = context.user_data.get("image", "N/A")
    script = context.user_data.get("script", "No")
    config = "Yes" if context.user_data.get("config", False) else "No"

    reply_text = (
        "Bot Creation Summary:\n"
        f"- Bot Name: {bot_name}\n"
        f"- Docker Image: {image}\n"
        f"- Autostart with script: {script}\n"
        f"- Autostart script with config: {config}\n"
        "\nCreating bot..."
    )
    await update.message.reply_text(reply_text)
    # Here you would add the logic to create the bot based on the collected data

    return ConversationHandler.END


async def cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Bot creation cancelled.")
    return ConversationHandler.END


def get_create_bot_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("create_bot", ask_bot_name)],
        states={
            ASKING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_image)],
            ASKING_IMAGE: [CallbackQueryHandler(image_chosen)],
            CUSTOM_IMAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_image)
            ],
            ASKING_SCRIPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_script),
                CallbackQueryHandler(script_chosen),
            ],
            ASKING_CONFIG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_config)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

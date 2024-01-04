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
(
    ASKING_NAME,
    ASKING_IMAGE,
    CUSTOM_IMAGE,
    ASKING_SCRIPT,
    ASKING_CONFIG,
    ASKING_CREDENTIALS,
) = range(6)


# Example list of available images and scripts
AVAILABLE_IMAGES = [
    "hummingbot/hummingbot:latest",
    "hummingbot/hummingbot:development",
    "custom",
]
AVAILABLE_SCRIPTS = ["script1", "script2", "script3"]
AVAILABLE_CONFIGS = ["config1", "config2", "config3", "no"]
AVAILABLE_CREDENTIALS = ["master_account", "test_account"]


async def ask_bot_name(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Please enter the name for your new bot:")
    return ASKING_NAME


async def ask_image(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton(image, callback_data=image)] for image in AVAILABLE_IMAGES
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🐙 Choose a docker image for your bot:", reply_markup=reply_markup
    )
    return ASKING_IMAGE


async def image_chosen(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "custom":
        await query.edit_message_text(text="Please enter the custom docker image name:")
        return CUSTOM_IMAGE
    else:
        context.user_data["image"] = query.data
        await query.edit_message_text(text=f"Selected image: {query.data}")
        await present_available_credentials(update, context)
        return ASKING_CREDENTIALS


async def custom_image(update: Update, context: CallbackContext) -> int:
    context.user_data["image"] = update.message.text
    await update.message.reply_text(f"Custom image set to: {update.message.text}")
    await present_available_credentials(update, context)
    return ASKING_CREDENTIALS


async def present_available_scripts(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton(script, callback_data=script)]
        for script in AVAILABLE_SCRIPTS
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.reply_text(
        "Choose a script to run:", reply_markup=reply_markup
    )


async def present_available_credentials(
    update: Update, context: CallbackContext
) -> int:
    keyboard = [
        [InlineKeyboardButton(credential, callback_data=credential)]
        for credential in AVAILABLE_CREDENTIALS
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.reply_text(
        "Choose the credentials to use:", reply_markup=reply_markup
    )


async def ask_credentials(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["credentials"] = query.data
    await query.edit_message_text(text=f"Selected Credentials: {query.data}")
    await present_available_scripts(update, context)
    return ASKING_SCRIPT


async def script_chosen(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["script"] = query.data
    await query.edit_message_text(text=f"Selected script: {query.data}")
    keyboard = [
        [InlineKeyboardButton(config, callback_data=config)]
        for config in AVAILABLE_CONFIGS
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.reply_text(
        "Do you want to autostart the script with a config?", reply_markup=reply_markup
    )
    return ASKING_CONFIG


async def ask_config(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    config_response = None if query.data == "no" else query.data
    await query.edit_message_text(text=f"Selected config: {query.data}")
    context.user_data["config"] = config_response
    return await summarize_and_end(update, context)


async def summarize_and_end(update: Update, context: CallbackContext) -> int:
    bot_name = context.user_data.get("bot_name", "N/A")
    image = context.user_data.get("image", "N/A")
    script = context.user_data.get("script", "N/A")
    config = context.user_data.get("config", "N/A")

    reply_text = (
        "Bot Creation Summary:\n"
        f"- Bot Name: {bot_name}\n"
        f"- Docker Image: {image}\n"
        f"- Autostart with script: {script}\n"
        f"- Autostart script with config: {config}\n"
        "\nCreating bot..."
    )
    await update.callback_query.message.reply_text(reply_text)
    # Here you would add the logic to create the bot based on the collected data

    return ConversationHandler.END


async def cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Bot creation cancelled.")
    return ConversationHandler.END


def get_create_bot_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("create_bot", ask_image)],
        states={
            ASKING_IMAGE: [CallbackQueryHandler(image_chosen)],
            CUSTOM_IMAGE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("(?i)^cancel$"),
                    custom_image,
                )
            ],
            ASKING_CREDENTIALS: [
                CallbackQueryHandler(ask_credentials),
            ],
            ASKING_SCRIPT: [
                CallbackQueryHandler(script_chosen),
            ],
            ASKING_CONFIG: [CallbackQueryHandler(ask_config)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

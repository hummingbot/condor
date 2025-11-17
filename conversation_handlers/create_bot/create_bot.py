import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from services.backend_api_client import BackendAPIClient

# Define states
(
    ASKING_NAME,
    ASKING_IMAGE,
    CUSTOM_IMAGE,
    ASKING_SCRIPT,
    ASKING_CONFIG,
    ASKING_CREDENTIALS,
) = range(6)


async def ask_image(update: Update, context: CallbackContext) -> int:
    backend_api_client = BackendAPIClient.get_instance()
    context.user_data["available_hummingbot_images"] = asyncio.create_task(
        backend_api_client.get_available_images(image_name="hummingbot")
    )
    context.user_data["available_scripts"] = asyncio.create_task(
        backend_api_client.get_all_scripts()
    )
    context.user_data["available_scripts_configs"] = asyncio.create_task(
        backend_api_client.get_all_scripts_config()
    )
    context.user_data["available_credentials"] = asyncio.create_task(
        backend_api_client.get_credentials(account_name="master_account")
    )
    available_images = await context.user_data["available_hummingbot_images"]
    keyboard = [
        [InlineKeyboardButton(image, callback_data=image)]
        for image in available_images["available_images"]
    ]
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
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


async def present_available_scripts(update: Update, context: CallbackContext):
    available_scripts = await context.user_data["available_scripts"]
    keyboard = [
        [InlineKeyboardButton(script, callback_data=script)]
        for script in available_scripts
    ]
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.reply_text(
        "Choose a script to run:", reply_markup=reply_markup
    )


async def present_available_credentials(update: Update, context: CallbackContext):
    available_credentials = await context.user_data["available_credentials"]
    keyboard = [
        [InlineKeyboardButton(credential, callback_data=credential)]
        for credential in available_credentials
    ]
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
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
    available_configs = await context.user_data["available_scripts_configs"]
    query = update.callback_query
    await query.answer()
    context.user_data["script"] = query.data
    await query.edit_message_text(text=f"Selected script: {query.data}")
    keyboard = [
        [InlineKeyboardButton(config, callback_data=config)]
        for config in available_configs
    ]
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.reply_text(
        "Select the config to use:", reply_markup=reply_markup
    )
    return ASKING_CONFIG


async def ask_config(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    config_response = None if query.data == "no" else query.data
    await query.edit_message_text(text=f"Selected config: {query.data}")
    context.user_data["config"] = config_response
    return await summarize_and_end(update, context)


def find_next_bot_name(active_containers, exited_containers):
    # Combine the names from both active and exited containers
    all_container_names = (
        active_containers["active_instances"] + exited_containers["exited_instances"]
    )

    # Extract numbers from container names and find the maximum
    max_number = 0
    for container in all_container_names:
        if container["name"].startswith("hummingbot-"):
            try:
                number = int(container["name"].split("-")[1])
                max_number = max(max_number, number)
            except ValueError:
                # Handle cases where the split part is not a number
                continue

    # The next available number will be max_number + 1
    next_number = max_number + 1
    return f"{next_number}"


async def summarize_and_end(update: Update, context: CallbackContext) -> int:
    backend_api_client = BackendAPIClient.get_instance()
    # The API calls start as soon as this line is executed
    active_containers, exited_containers = await asyncio.gather(
        backend_api_client.async_active_containers(),
        backend_api_client.async_exited_containers(),
    )
    bot_name = find_next_bot_name(active_containers, exited_containers)
    image = context.user_data["image"]
    script = context.user_data.get("script")
    config = context.user_data.get("config")
    credentials = context.user_data["credentials"]
    bot_config = HummingbotInstanceConfig(
        instance_name=bot_name,
        image=image,
        script=script,
        script_config=config,
        credentials_profile=credentials,
    )

    reply_text = (
        "Bot Creation Summary:\n"
        f"- Bot Name: hummingbot-{bot_name}\n"
        f"- Docker Image: {image}\n"
        f"- Autostart with script: {script or 'N/A'}\n"
        f"- Autostart script with config: {config or 'N/A'}\n"
        "\nCreating bot..."
    )
    await update.callback_query.message.reply_text(reply_text)
    await backend_api_client.async_create_hummingbot_instance(bot_config)
    return ConversationHandler.END


async def cancel(update: Update, context: CallbackContext) -> int:
    await update.callback_query.message.reply_text("Bot creation cancelled.")
    return ConversationHandler.END


def get_create_bot_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("create_bot", ask_image)],
        states={
            ASKING_IMAGE: [
                CallbackQueryHandler(image_chosen, pattern="^(?!cancel$).+"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            CUSTOM_IMAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_image),
                CommandHandler("cancel", cancel),
            ],
            ASKING_CREDENTIALS: [
                CallbackQueryHandler(ask_credentials, pattern="^(?!cancel$).+"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ASKING_SCRIPT: [
                CallbackQueryHandler(script_chosen, pattern="^(?!cancel$).+"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ASKING_CONFIG: [
                CallbackQueryHandler(ask_config, pattern="^(?!cancel$).+"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

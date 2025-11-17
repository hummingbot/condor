import asyncio
import datetime
import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    PicklePersistence,
)

from conversation_handlers.control_bots.control_bots import (
    get_control_bots_conversation_handler,
)
from conversation_handlers.create_bot.create_bot import (
    get_create_bot_conversation_handler,
)
from services.backend_api_client import BackendAPIClient
from utils.auth import restricted
from utils.config import TELEGRAM_TOKEN

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the conversation and display the main menu."""
    reply_text = """
🚀 **Welcome to Condor\!** 🦅

Manage your trading bots efficiently and monitor their performance\.

🎛️ **Quick Commands**:

🔸 `/create_bot`: Launch a new trading bot instance with customized settings\.
🔸 `/bots_status`: View the current status and performance of all your active bots\.
🔸 `/create_bot`: Manage your bot's activities, such as starting or stopping trading strategies\.
🔸 `/add_config`: Add or modify configuration settings for your trading bots\.

🔍 **Need help?** Type `/help` for assistance\.

Get started on your automated trading journey with ease and precision\!
"""
    await update.message.reply_text(reply_text, parse_mode="MarkdownV2")


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provide help information for each command."""
    help_text = """
📖 **Help & Information**

Here's a detailed guide on how to use each command:

🔹 `/create_bot`: Launch a new bot instance\.
   \- You'll be prompted to enter:
     \- **Bot Name**: A unique name for your bot\.
     \- **Docker Image**: The Docker image to use for the bot\.
     \- **Script \(Optional\)**: A script for custom bot operations\.
     \- **Config \(Optional\)**: Configuration settings for the bot\.

🔹 `/bots_status`: View the status of all active bots\.
   \- Displays for each bot:
     \- **Name**: The name of the bot\.
     \- **Status**: Running status \(running or not\)\.
     \- **PNL**: Profit and loss information\.
     \- **Volume Traded**: The trading volume handled by the bot\.

🔹 `/create_bot`: Manage an active bot\.
   \- Choose a bot to:
     \- **Start**: Begin the bot's trading operations\.
     \- **Stop**: Pause the bot's trading operations\.
     \- **Remove**: Delete the bot\.
     \- **Logs**: View the bot's operation logs\.
     \- **Performance**: Check the bot's trading performance\.

🔹 `/add_config`: Add or modify a bot's configuration\.
   \- Steps:
     \- Pick a script to generate the configuration\.
     \- Engage in a conversation to fill out the configuration details\.
     \- Store and optionally deploy the configuration\.

For further assistance or more information, feel free to ask\!
    """
    await update.message.reply_text(help_text, parse_mode="MarkdownV2")


async def initialize_backend_api(context: CallbackContext):
    backend_api_client = BackendAPIClient.get_instance(
        host=os.environ.get("BACKEND_API_HOST", "localhost"),
        port=os.environ.get("BACKEND_API_PORT", 8000),
        username=os.environ.get("BACKEND_API_USERNAME", "admin"),
        password=os.environ.get("BACKEND_API_PASSWORD", "admin"),
    )

    # Retrieve the list of images from environment variable
    all_images = os.environ.get("ALL_HUMMINGBOT_IMAGES", "").split(",")

    # Create a list of tasks for pulling each image
    pull_tasks = [
        backend_api_client.pull_image(image_name=image.strip())
        for image in all_images
        if image.strip()
    ]

    # Pull images concurrently
    if pull_tasks:
        logging.info(f"Pulling images: {datetime.datetime.now()}")
        await asyncio.gather(*pull_tasks)
    else:
        logging.warning("No images to pull.")


def main() -> None:
    """Run the bot."""
    # Persistent storage to save bot's conversations
    persistence = PicklePersistence(
        filepath="data/condorbot_persistence"
    )  # TODO: evaluate usage of PicklePersistence
    # Create the Application and pass it your bot's token
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.job_queue.run_once(initialize_backend_api, when=1)

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help))
    application.add_handler(get_create_bot_conversation_handler())
    application.add_handler(get_control_bots_conversation_handler())

    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

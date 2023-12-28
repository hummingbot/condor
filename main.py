import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, PicklePersistence

from auth import restricted
from config import TELEGRAM_TOKEN

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
🔸 `/control_bot`: Manage your bot's activities, such as starting or stopping trading strategies\.
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

🔹 `/control_bot`: Manage an active bot\.
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


def main() -> None:
    """Run the bot."""
    # Persistent storage to save bot's conversations
    persistence = PicklePersistence(filepath="condorbot_persistence")

    # Create the Application and pass it your bot's token
    application = (
        Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()
    )

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help))

    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

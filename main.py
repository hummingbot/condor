import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from handlers.portfolio import portfolio_command
from handlers.bots import bots_command
from handlers.trade_ai import trade_command
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
🚀 *Welcome to Condor\!* 🦅

Manage your trading bots efficiently and monitor their performance\.

🎛️ *Quick Commands*:

📊 `/portfolio` \\- View your portfolio summary and holdings
🤖 `/bots` \\- Check status of all active trading bots
💹 `/trade` \\- AI\\-powered trading assistant


🔍 *Need help?* Type `/help` for detailed command information\.

Get started on your automated trading journey with ease and precision\!
"""
    await update.message.reply_text(reply_text, parse_mode="MarkdownV2")


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provide help information for each command."""
    help_text = """
📖 *Help & Information*

Here's a detailed guide on how to use each command:

*📊 Portfolio Commands*

🔹 `/portfolio` \\- View your portfolio summary
   • `/portfolio` \\- Show summary with total value and top holdings
   • `/portfolio detailed` \\- Show detailed breakdown by account

🔹 `/bots` \\- View active bots status
   • `/bots` \\- Show all active bots with PnL and metrics
   • `/bots <name>` \\- Show detailed status for a specific bot

🔹 `/trade` \\- AI\\-powered trading assistant
   • Natural language trading queries
   • Market data analysis
   • Price checks and order book analysis
   • Examples:
     • `/trade What's the price of BTC?`
     • `/trade Show my portfolio`
     • `/trade Analyze ETH order book`

For further assistance or more information, feel free to ask\!
    """
    await update.message.reply_text(help_text, parse_mode="MarkdownV2")




def main() -> None:
    """Run the bot."""
    # Create the Application and pass it your bot's token
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help))
    application.add_handler(CommandHandler("portfolio", portfolio_command))
    application.add_handler(CommandHandler("bots", bots_command))
    application.add_handler(CommandHandler("trade", trade_command))

    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

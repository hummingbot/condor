"""
AI-powered trading command handler using Pydantic AI and hummingbot_mcp
"""

import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from utils.auth import restricted
from utils.telegram_formatters import format_error_message, format_success_message, escape_markdown_v2
from hummingbot_mcp.hummingbot_client import hummingbot_client
from hummingbot_mcp.tools import trading as trading_tools
from hummingbot_mcp.tools import market_data as market_data_tools
from hummingbot_mcp.tools import portfolio as portfolio_tools

logger = logging.getLogger(__name__)


# System prompt for the trading AI agent
TRADING_SYSTEM_PROMPT = """You are a professional crypto trading assistant integrated with Hummingbot.

Your capabilities:
- Analyze market data and provide trading insights
- Execute trades on behalf of users with their confirmation
- Get real-time prices, order books, and market data
- Check portfolio positions and balances
- Provide trading recommendations based on market conditions

Important guidelines:
1. ALWAYS confirm with the user before executing any trades
2. Provide clear explanations for your trading recommendations
3. Use proper risk management - never recommend trading entire portfolio
4. Be transparent about market conditions and risks
5. Format numbers clearly (use $ for USD values)
6. Keep responses concise but informative

Available tools:
- place_order: Execute buy/sell orders
- get_prices: Get current market prices
- get_order_book: Analyze order book depth
- get_candles: Get historical price data
- get_portfolio_overview: Check current holdings

When helping with trades:
1. First, gather relevant market data
2. Analyze the situation
3. Provide a clear recommendation with reasoning
4. Ask for confirmation before executing
5. Execute the trade if confirmed
6. Report the result clearly
"""


async def create_trading_agent():
    """
    Create and configure the Pydantic AI agent for trading

    Returns:
        Configured Agent instance
    """
    # Initialize the Hummingbot client
    client = await hummingbot_client.get_client()

    # Create the OpenAI model
    model = OpenAIModel(
        model_name="gpt-4",
        api_key=os.environ.get("OPENAI_API_KEY"),
    )

    # Create the agent with trading tools
    # Note: We'll create wrapper functions that use the MCP tools
    agent = Agent(
        model=model,
        system_prompt=TRADING_SYSTEM_PROMPT,
    )

    return agent, client


@restricted
async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /trade command - AI-assisted trading

    Usage:
        /trade <your trading question or command>

    Examples:
        /trade What's the current price of BTC on binance?
        /trade Should I buy ETH right now?
        /trade Buy $100 worth of SOL on binance
        /trade Analyze the BTC-USDT order book
    """
    # Check if message is provided
    if not context.args:
        help_text = (
            "🤖 *AI Trading Assistant*\n\n"
            "*Usage:*\n"
            "`/trade <your question or command>`\n\n"
            "*Examples:*\n"
            "• `/trade What's the current price of BTC?`\n"
            "• `/trade Should I buy ETH right now?`\n"
            "• `/trade Buy $100 of SOL on binance`\n"
            "• `/trade Show me the BTC\\-USDT order book`\n\n"
            "_Note: The AI will ask for confirmation before executing any trades\\._"
        )
        await update.message.reply_text(help_text, parse_mode="MarkdownV2")
        return

    # Get user message
    user_message = " ".join(context.args)

    # Send "typing" status
    await update.message.reply_chat_action("typing")

    try:
        # Create the AI agent
        agent, client = await create_trading_agent()

        # For this simple implementation, we'll handle the tools manually
        # A full implementation would integrate the MCP tools directly into Pydantic AI

        # Run the agent with the user message
        # For now, we'll provide a simplified response that shows the concept
        response = await handle_trading_query(user_message, client)

        # Format and send response
        message = f"🤖 *AI Trading Assistant*\n\n{escape_markdown_v2(response)}"
        await update.message.reply_text(message, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error in AI trading assistant: {e}", exc_info=True)
        error_message = format_error_message(f"AI trading assistant error: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def handle_trading_query(user_message: str, client) -> str:
    """
    Handle trading query using MCP tools

    This is a simplified implementation showing how to use the MCP tools.
    A production version would integrate these tools into Pydantic AI.

    Args:
        user_message: User's trading query
        client: Hummingbot API client

    Returns:
        Response message
    """
    # Convert message to lowercase for easier parsing
    message_lower = user_message.lower()

    # Check for price queries
    if "price" in message_lower:
        # Extract trading pair if mentioned
        if "btc" in message_lower:
            result = await market_data_tools.get_prices(
                client=client,
                connector_name="binance",
                trading_pairs=["BTC-USDT"]
            )
            price_data = result.get("prices_table", "")
            return f"Current BTC price:\n\n{price_data}"

        elif "eth" in message_lower:
            result = await market_data_tools.get_prices(
                client=client,
                connector_name="binance",
                trading_pairs=["ETH-USDT"]
            )
            price_data = result.get("prices_table", "")
            return f"Current ETH price:\n\n{price_data}"

        else:
            return (
                "I can help you check prices. Please specify which token you'd like to check.\n"
                "For example: 'What's the price of BTC?' or 'Show me ETH price'"
            )

    # Check for portfolio queries
    elif "portfolio" in message_lower or "balance" in message_lower or "holdings" in message_lower:
        result = await portfolio_tools.get_portfolio_overview(
            client=client,
            include_balances=True,
            include_perp_positions=True,
            include_lp_positions=False,
            include_active_orders=False
        )
        return f"Here's your portfolio overview:\n\n{result.get('formatted_output', 'No data available')}"

    # Check for order book queries
    elif "order book" in message_lower or "orderbook" in message_lower:
        if "btc" in message_lower:
            result = await market_data_tools.get_order_book(
                client=client,
                connector_name="binance",
                trading_pair="BTC-USDT",
                query_type="snapshot"
            )
            return f"BTC-USDT Order Book:\n\n{result.get('order_book_table', 'No data available')}"
        else:
            return "Please specify which trading pair you'd like to analyze. For example: 'Show BTC order book'"

    # Check for buy/sell commands
    elif any(word in message_lower for word in ["buy", "sell", "trade"]):
        return (
            "⚠️ Trading requires confirmation.\n\n"
            "To execute a trade, please use the following format:\n"
            "/trade execute <BUY|SELL> <amount> <token> on <exchange>\n\n"
            "Example: /trade execute BUY 0.001 BTC on binance\n\n"
            "I'll analyze the market and ask for your confirmation before executing."
        )

    # Default response for unrecognized queries
    else:
        return (
            "I can help you with:\n"
            "• Checking prices: 'What's the price of BTC?'\n"
            "• Viewing portfolio: 'Show my portfolio'\n"
            "• Analyzing order books: 'Show BTC order book'\n"
            "• Market analysis: 'Should I buy ETH?'\n\n"
            "What would you like to know?"
        )

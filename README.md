# Condor 🦅

A Telegram bot for managing and monitoring Hummingbot trading bots with AI-powered assistance.

## Features

- 📊 **Portfolio Management** - View your crypto portfolio across all exchanges
- 🤖 **Bot Monitoring** - Track active bots with real-time PnL and metrics
- 💹 **AI Trading Assistant** - Natural language trading queries and analysis
- 🎛️ **Bot Deployment** - Create and manage trading bot instances
- ⚙️ **Controller Management** - Configure and deploy trading strategies

## Quick Start

### Prerequisites

- Python 3.11+
- Conda package manager
- Hummingbot API server running
- Telegram Bot Token
- OpenAI API Key (for AI features)

### Installation

1. **Clone the repository**
```bash
git clone <your-repo-url>
cd condor
```

2. **Create conda environment**
```bash
conda env create -f environment.yml
conda activate condor
```

3. **Configure environment variables**
```bash
cp .env.example .env
# Edit .env with your configuration
```

Required environment variables:
```bash
# Hummingbot API
BACKEND_API_HOST=localhost
BACKEND_API_PORT=8000
BACKEND_API_USERNAME=admin
BACKEND_API_PASSWORD=admin

# OpenAI (for AI features)
OPENAI_API_KEY=your_openai_key

# Telegram
TELEGRAM_TOKEN=your_telegram_bot_token
TELEGRAM_ALLOWED_IDS=comma,separated,user,ids
```

4. **Run the bot**
```bash
python main.py
```

## Commands

### Portfolio Commands

**`/portfolio`** - View portfolio summary
```
/portfolio              # Show summary with top holdings
/portfolio detailed     # Show detailed breakdown by account
```

**`/bots`** - View active bots status
```
/bots                   # Show all active bots
/bots my_bot_1         # Show specific bot details
```

**`/trade`** - AI-powered trading assistant
```
/trade What's the price of BTC?
/trade Show my portfolio
/trade Analyze the ETH-USDT order book
/trade Should I buy SOL right now?
```

### Future Features

Bot deployment and management features are planned for future releases. Currently, you can monitor existing bots via the `/bots` command.

## Architecture

Condor uses two complementary tools:

### 1. hummingbot_api_client
Direct API access for fast, simple operations:
- Portfolio queries
- Bot status checks
- Market data retrieval

### 2. hummingbot_mcp
AI-powered tools using Pydantic AI for complex operations:
- Natural language trading queries
- Market analysis
- Strategy recommendations

See [TOOLS_USAGE_GUIDE.md](TOOLS_USAGE_GUIDE.md) for detailed documentation.

## Documentation

- [TOOLS_USAGE_GUIDE.md](TOOLS_USAGE_GUIDE.md) - Comprehensive guide on using hummingbot_api_client and hummingbot_mcp
- [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) - Implementation details and usage examples

## Project Structure

```
condor/
├── handlers/                    # Command handlers
│   ├── __init__.py
│   ├── portfolio.py            # /portfolio command
│   ├── bots.py                 # /bots command
│   └── trade_ai.py             # /trade command (AI)
├── utils/                       # Utilities
│   ├── auth.py                 # Authentication
│   ├── config.py               # Configuration
│   └── telegram_formatters.py  # Message formatting
├── hummingbot_api_client/      # Direct API client package
├── hummingbot_mcp/             # MCP AI-powered tools
├── main.py                     # Main bot entry point
├── environment.yml             # Conda environment
├── TOOLS_USAGE_GUIDE.md        # Comprehensive tools guide
├── IMPLEMENTATION_SUMMARY.md   # Implementation details
└── README.md                   # This file
```

## Examples

### Check Portfolio
```
📊 Portfolio Summary

💰 Total Value: $12,450.32
🔢 Tokens: 8
👤 Accounts: 2

🏆 Top Holdings:
1. USDT: $5,234.12 (42.0%)
2. BTC: $3,456.78 (27.8%)
3. ETH: $2,123.45 (17.1%)
```

### Monitor Active Bots
```
🤖 Active Bots Status

🟢 trading_bot_1
  📈 PnL: $245.67
  📊 Volume: $15.4K
  ⚙️ Controllers: 2

Total Active Bots: 2
```

### AI Trading Assistant
```
/trade What's the current price of BTC?

🤖 AI Trading Assistant

Current BTC price:

BTC-USDT: $43,251.25
Spread: 0.01%
```

## Development

### Adding New Commands

1. Create handler in `handlers/` directory
2. Import in `main.py`
3. Register with `application.add_handler()`

Example:
```python
# handlers/my_command.py
@restricted
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Your implementation
    pass

# main.py
from handlers.my_command import my_command
application.add_handler(CommandHandler("mycommand", my_command))
```

### Using API Client

```python
from hummingbot_api_client import HummingbotAPIClient

async with HummingbotAPIClient(...) as client:
    # Get portfolio
    portfolio = await client.portfolio.get_portfolio_summary()

    # Get bots
    bots = await client.bot_orchestration.get_active_bots_status()

    # Get prices
    prices = await client.market_data.get_prices(
        connector_name="binance",
        trading_pairs=["BTC-USDT"]
    )
```

### Using MCP Tools

```python
from hummingbot_mcp.hummingbot_client import hummingbot_client
from hummingbot_mcp.tools import trading as trading_tools

# Get client
client = await hummingbot_client.get_client()

# Use tools
result = await trading_tools.place_order(
    client=client,
    connector_name="binance",
    trading_pair="BTC-USDT",
    trade_type="BUY",
    amount="0.001"
)
```

## Testing

```bash
# Activate environment
conda activate condor

# Run with logging
python main.py

# Test commands in Telegram
/start
/portfolio
/bots
/trade What's the price of BTC?
```

## Troubleshooting

**Bot not responding:**
- Check Telegram token is correct
- Verify your user ID is in TELEGRAM_ALLOWED_IDS
- Check bot logs for errors

**Portfolio/Bots commands failing:**
- Ensure Hummingbot API is running
- Verify BACKEND_API_HOST and BACKEND_API_PORT
- Check API credentials

**AI features not working:**
- Verify OPENAI_API_KEY is set
- Check OpenAI API credits
- Review error logs

## Security

- 🔒 Authentication via `@restricted` decorator
- 🔑 Environment-based configuration
- 🚫 No hardcoded credentials
- 👤 User ID whitelist

## Roadmap

### Planned Features
- [ ] Bot deployment via Telegram (`/deploy` command)
- [ ] Bot management (start/stop/archive via Telegram)
- [ ] Advanced AI trading strategies with execution
- [ ] AI-powered controller configuration
- [ ] Natural language strategy creation
- [ ] Advanced analytics and reporting
- [ ] Web dashboard integration
- [ ] Multi-language support
- [ ] Automated strategy optimization
- [ ] Risk management tools and alerts

---

Built with ❤️ using Hummingbot, Telegram, and AI

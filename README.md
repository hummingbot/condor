# Condor 🦅

A lightweight Telegram bot for monitoring Hummingbot trading bots with AI-powered assistance.

## What It Does

- 📊 **View Portfolio** - Check balances and holdings across all exchanges
- 🤖 **Monitor Bots** - Track active bots with real-time PnL and metrics
- 💹 **AI Assistant** - Ask questions about prices, market data, and your portfolio using natural language

## Quick Start

**Prerequisites:** Python 3.11+, Conda, Docker, Hummingbot API running, Telegram Bot Token, OpenAI API Key

```bash
# Install
conda env create -f environment.yml
conda activate condor

# Configure .env file (copy from .env.example)
cp .env.example .env
# Edit .env with your credentials

# IMPORTANT: Update MCP server path in handlers/trade_ai.py (line 68)
# Replace with your actual hummingbot_mcp config path

# Run
python main.py
```

**Note:** For `/trade` AI features, see [MCP_SETUP.md](MCP_SETUP.md) for Docker MCP server configuration.

## Commands

| Command | Description |
|---------|-------------|
| `/portfolio` | Portfolio summary with top holdings |
| `/portfolio detailed` | Detailed breakdown by account |
| `/bots` | All active bots with PnL and metrics |
| `/bots <name>` | Specific bot details |
| `/trade <question>` | AI assistant for prices, market data, portfolio queries |

**Examples:**
```
/portfolio
/bots trading_bot_1
/trade What's the price of BTC?
/trade Show my portfolio
/trade Analyze the ETH-USDT order book
```

## How It Works

Condor uses two approaches:

1. **Direct API** (`/portfolio`, `/bots`) - Fast API calls via hummingbot_api_client
2. **AI Assistant** (`/trade`) - GPT-4o + MCP server in Docker with access to all Hummingbot tools

**Architecture:**
```
Telegram → Condor Bot → Hummingbot API → Trading Bots
                     ↘ GPT-4o → MCP (Docker) → Hummingbot API
```

The AI assistant has real-time access to your portfolio, bots, market data, and more via MCP tools.

## Example Output

**Portfolio:**
```
📊 Portfolio Summary
💰 Total Value: $12,450.32
🏆 Top Holdings:
1. USDT: $5,234.12 (42.0%)
2. BTC: $3,456.78 (27.8%)
```

**Bots:**
```
🤖 Active Bots Status
🟢 trading_bot_1
  📈 PnL: $245.67
  📊 Volume: $15.4K
```

**AI Assistant:**
```
🤖 Current BTC price:
BTC-USDT: $43,251.25
```

## Project Structure

```
condor/
├── handlers/              # Command handlers (/portfolio, /bots, /trade)
├── utils/                 # Auth, config, formatters
├── hummingbot_api_client/ # Direct API client
├── hummingbot_mcp/        # MCP AI tools
└── main.py               # Entry point
```

## Troubleshooting

- **Bot not responding?** Check `TELEGRAM_TOKEN` and `TELEGRAM_ALLOWED_IDS` in `.env`
- **Commands failing?** Verify Hummingbot API is running and credentials are correct
- **AI not working?** Check `OPENAI_API_KEY` is set and you have API credits

## Security

- User ID whitelist (`TELEGRAM_ALLOWED_IDS`)
- Environment-based credentials
- `@restricted` decorator on all commands

---

**Built with Hummingbot, Telegram, and OpenAI**

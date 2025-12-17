# Condor

A Telegram bot for monitoring and trading with Hummingbot via the Backend API.

## Features

- **Portfolio Dashboard** - Comprehensive portfolio view with PNL tracking, 24h changes, and graphical analysis
- **Bot Monitoring** - Track active Hummingbot trading bots with real-time status and metrics
- **CLOB Trading** - Place orders on centralized exchanges (Binance, Bybit, etc.) with interactive menus
- **DEX Trading** - Swap tokens and manage CLMM liquidity positions via Gateway
- **Configuration** - Manage API servers and exchange credentials through Telegram
- **AI Assistant** - Natural language queries via GPT-4o + MCP (coming soon)

## Quick Start

**Prerequisites:** Python 3.11+, Conda, Hummingbot Backend API running, Telegram Bot Token

```bash
# clone repo
git clone https://github.com/hummingbot/condor.git
cd condor
# environment setup
conda env create -f environment.yml
conda activate condor

# Configure
cp .env.example .env
# Edit .env with your credentials:
# - TELEGRAM_TOKEN
# - TELEGRAM_ALLOWED_IDS
# - OPENAI_API_KEY (optional, for AI features)

# Run
python main.py
```

## Commands

| Command | Description |
|---------|-------------|
| `/portfolio` | Portfolio dashboard with PNL indicators, holdings, and graphs |
| `/bots` | All active bots with status and metrics |
| `/bots <name>` | Specific bot details |
| `/clob_trading` | CLOB trading menu (place orders, manage positions) |
| `/dex_trading` | DEX trading menu (swaps, pools, liquidity positions) |
| `/config` | Configuration menu (servers, API keys) |
| `/trade <question>` | AI assistant (disabled, coming soon) |

## Architecture

```
Telegram → Condor Bot → Hummingbot Backend API → Trading Bots
                     ↘ Gateway → DEX Protocols
                     ↘ GPT-4o → MCP (Docker) → Hummingbot API (future)
```

### Direct API Commands
- `/portfolio`, `/bots`, `/clob_trading`, `/dex_trading` use direct API calls via `hummingbot_api_client`
- Fast, reliable, interactive button menus

### AI Assistant (Future)
- `/trade` uses GPT-4o + MCP server with access to all Hummingbot tools
- Natural language interface for market data, portfolio queries, and more

## Project Structure

```
condor/
├── handlers/                    # Telegram command handlers
│   ├── bots.py                 # /bots command
│   ├── portfolio.py            # /portfolio command with dashboard
│   ├── trade_ai.py             # /trade AI assistant (disabled)
│   ├── clob/                   # CLOB trading module
│   │   ├── __init__.py         # Main command, callback router
│   │   ├── menu.py             # Trading menu with overview
│   │   ├── place_order.py      # Order placement flow
│   │   ├── leverage.py         # Leverage/position mode config
│   │   ├── orders.py           # Order search/cancel
│   │   ├── positions.py        # Position management
│   │   └── account.py          # Account switching
│   ├── dex/                    # DEX trading module
│   │   ├── __init__.py         # Main command, callback router
│   │   ├── menu.py             # Trading menu with balances
│   │   ├── swap_quote.py       # Get swap quotes
│   │   ├── swap_execute.py     # Execute swaps
│   │   ├── swap_history.py     # Swap history/status
│   │   └── pools.py            # Pool discovery & LP management
│   └── config/                 # Configuration module
│       ├── __init__.py         # /config command
│       ├── servers.py          # API server management
│       ├── api_keys.py         # Exchange credentials
│       └── user_preferences.py # User preference storage
├── utils/                      # Utilities
│   ├── auth.py                 # @restricted decorator
│   ├── telegram_formatters.py  # Message formatting
│   ├── portfolio_graphs.py     # Dashboard chart generation
│   └── trading_data.py         # Data aggregation helpers
├── servers/                    # Server management
│   ├── server_manager.py       # Server CRUD & client pool
│   └── servers.yml             # Server configuration
├── hummingbot_api_client/      # API client library
├── hummingbot_mcp/             # MCP AI tools (Docker)
├── flows/                      # Documentation
│   ├── bots_flow.txt           # /bots command flow
│   ├── portfolio_flow.txt      # /portfolio command flow
│   ├── clob_trading_flow.txt   # CLOB trading flow
│   ├── dex_trading_flow.txt    # DEX trading flow
│   ├── config_flow.txt         # Configuration flow
│   └── common_patterns.txt     # Shared patterns
└── main.py                     # Entry point
```

## Handler Features

### Portfolio (`/portfolio`)
- **PNL Indicators** - 24h, 7d, 30d with deposit/withdrawal detection
- **Token Holdings** - Balances with 24h price changes
- **Positions** - Perpetual positions with unrealized PnL
- **LP Positions** - CLMM positions with in-range status
- **Active Orders** - Open order summary
- **Dashboard** - Combined chart with value history, token distribution, account breakdown
- **Settings** - Configure time period (1d, 3d, 7d, 14d, 30d)

### CLOB Trading (`/clob_trading`)
- **Overview** - Account balances, positions, orders at a glance
- **Place Orders** - Interactive menu with dual input (buttons + direct text)
  - Toggle: side, order type, position mode
  - Input: connector, pair, amount, price
  - USD notation: `$100` auto-converts to token units
- **Set Leverage** - Configure leverage and position mode per connector
- **Search Orders** - View/filter/cancel orders
- **Manage Positions** - View, trade, close positions with confirmation

### DEX Trading (`/dex_trading`)
- **Gateway Balances** - Token balances across DEX wallets
- **Swap Quote** - Get quotes before executing
- **Execute Swap** - Perform swaps with slippage control
- **Quick Swap** - Repeat last swap with minimal input
- **Pool Discovery** - Search pools by connector and token
- **Pool Info** - Detailed pool stats with liquidity charts
- **LP Positions** - Manage CLMM positions (add, close, collect fees)

### Configuration (`/config`)
- **API Servers** - Add, modify, delete Hummingbot Backend API servers
  - Real-time status checking (online/offline/auth error)
  - Set default server
  - Progressive form for adding servers
- **API Keys** - Manage exchange credentials per account
  - View connected exchanges
  - Add new credentials (field-by-field input)
  - Delete credentials with confirmation

## User Preferences

Preferences are automatically saved and persist across sessions:

- **Portfolio** - Graph time period (days, interval)
- **CLOB** - Active account, last order parameters
- **DEX** - Default network/connector, last swap parameters
- **General** - Active server

## Security

- **User ID Whitelist** - Only `TELEGRAM_ALLOWED_IDS` can access
- **@restricted Decorator** - Applied to all command handlers
- **Environment Credentials** - API keys stored in `.env`
- **Secret Masking** - Passwords hidden in UI

## Configuration Files

### `.env`
```bash
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_ALLOWED_IDS=123456789,987654321
OPENAI_API_KEY=sk-...  # Optional, for AI features
```

### `servers.yml`
```yaml
default_server: main
servers:
  main:
    host: localhost
    port: 8000
    username: admin
    password: admin
    enabled: true
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Bot not responding | Check `TELEGRAM_TOKEN` and `TELEGRAM_ALLOWED_IDS` |
| Commands failing | Verify Hummingbot API is running |
| Connection refused | Check server host:port in `/config` |
| Auth error | Verify server credentials |
| DEX features unavailable | Ensure Gateway is configured and running |

## Development

### Flow Documentation
See `flows/` directory for detailed command flow documentation:
- Each handler has a corresponding `*_flow.txt` file
- `common_patterns.txt` documents shared patterns across handlers

### Adding New Features
1. Create handler in `handlers/` (or subdirectory for complex features)
2. Register in `main.py`
3. Follow patterns in `flows/common_patterns.txt`
4. Document flow in `flows/`

---

**Built with [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) and [Hummingbot](https://hummingbot.org/)**

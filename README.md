# Condor

A Telegram bot for monitoring and trading with Hummingbot via the Backend API.

## Features

- **Portfolio Dashboard** - Comprehensive portfolio view with PNL tracking, 24h changes, and graphical analysis
- **Bot Monitoring** - Track active Hummingbot trading bots with real-time status and metrics
- **CLOB Trading** - Place orders on centralized exchanges (Binance, Bybit, etc.) with interactive menus
- **DEX Trading** - Swap tokens and manage CLMM liquidity positions via Gateway
- **Configuration** - Manage API servers and exchange credentials through Telegram
- **AI Chat** - Streaming LLM responses with multi-provider support (Claude, GPT-4o, Gemini)

## Quick Start

**Prerequisites:** Python 3.12+, Conda, Hummingbot Backend API running, Telegram Bot Token

```bash
git clone https://github.com/hummingbot/condor.git
cd condor

# Option 1: Local Python
make install     # Interactive setup + conda environment
make run         # Start the bot

# Option 2: Docker
make setup       # Interactive configuration
make deploy      # Start with Docker Compose
```

## Commands

| Command | Description |
|---------|-------------|
| `/portfolio` | Portfolio dashboard with PNL indicators, holdings, and graphs |
| `/bots` | All active bots with status and metrics |
| `/trade` | CEX trading menu (spot & perpetual orders, positions) |
| `/swap` | DEX swap trading (quotes, execution, history) |
| `/lp` | DEX liquidity pool management (positions, pools) |
| `/routines` | Auto-discoverable Python scripts with scheduling |
| `/chat` | AI chat with streaming responses (Claude, GPT-4o, Gemini) |
| `/config` | Configuration menu (servers, API keys, Gateway, admin) |

## Architecture

```
Telegram → Condor Bot → Hummingbot Backend API → Trading Bots
                     ↘ Gateway → DEX Protocols
```

All commands use direct API calls via `hummingbot_api_client` with interactive button menus.

## Project Structure

```
condor/
├── handlers/                    # Telegram command handlers
│   ├── portfolio.py            # /portfolio command with dashboard
│   ├── bots/                   # Bot monitoring module
│   │   ├── __init__.py         # /bots command
│   │   ├── menu.py             # Bot status display
│   │   └── controllers/        # Bot controller configs
│   ├── cex/                    # CEX trading module (/trade)
│   │   ├── __init__.py         # Main command, callback router
│   │   ├── trade.py            # Order placement
│   │   ├── orders.py           # Order management
│   │   └── positions.py        # Position tracking
│   ├── dex/                    # DEX trading module (/swap, /lp)
│   │   ├── __init__.py         # Main commands, callback router
│   │   ├── swap.py             # Quote, execute, history
│   │   ├── liquidity.py        # LP positions management
│   │   └── pools.py            # Pool info and discovery
│   ├── config/                 # Configuration module (/config)
│   │   ├── __init__.py         # Main command
│   │   ├── servers.py          # API server management
│   │   ├── api_keys.py         # Exchange credentials
│   │   └── gateway/            # Gateway configuration
│   ├── routines/               # Routines module (/routines)
│   │   └── __init__.py         # Script discovery and execution
│   ├── chat/                   # AI Chat module (/chat)
│   │   └── __init__.py         # Multi-provider LLM with streaming
│   └── admin/                  # Admin panel (via /config)
├── routines/                   # User-defined automation scripts
├── utils/                      # Utilities
│   ├── auth.py                 # @restricted, @admin_required decorators
│   ├── streaming.py            # LLM streaming via sendMessageDraft
│   └── telegram_formatters.py  # Message formatting
├── config_manager.py           # Unified config (servers, users, permissions)
├── hummingbot_api_client/      # API client library
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

### CEX Trading (`/trade`)
- **Overview** - Account balances, positions, orders at a glance
- **Place Orders** - Interactive menu with dual input (buttons + direct text)
  - Toggle: side, order type, position mode
  - Input: connector, pair, amount, price
  - USD notation: `$100` auto-converts to token units
- **Set Leverage** - Configure leverage and position mode per connector
- **Search Orders** - View/filter/cancel orders
- **Manage Positions** - View, trade, close positions with confirmation

### DEX Swaps (`/swap`)
- **Gateway Balances** - Token balances across DEX wallets
- **Swap Quote** - Get quotes before executing
- **Execute Swap** - Perform swaps with slippage control
- **Quick Swap** - Repeat last swap with minimal input

### Liquidity Pools (`/lp`)
- **Pool Discovery** - Search pools by connector and token
- **Pool Info** - Detailed pool stats with liquidity charts
- **LP Positions** - Manage CLMM positions (add, close, collect fees)

### Routines (`/routines`)
- **Auto-Discovery** - Python scripts auto-discovered from `routines/` folder
- **Pydantic Config** - Type-safe configuration with descriptions
- **One-shot Scripts** - Run once, optionally schedule (interval or daily)
- **Continuous Scripts** - Long-running tasks with start/stop control
- **Multi-instance** - Run multiple instances with different configs

### AI Chat (`/chat`)
- **Multi-Provider** - Choose between Claude, GPT-4o, or Gemini models
- **Streaming Responses** - See responses as they're generated in real-time
- **Conversation History** - Maintains context across messages (last 20)
- **Model Switching** - Change models on the fly via inline buttons

**Available Models:**
| Provider | Models | API Key |
|----------|--------|---------|
| Anthropic | Claude 3.5 Sonnet, Claude 3.5 Haiku | `ANTHROPIC_API_KEY` |
| OpenAI | GPT-4o, GPT-4o-mini | `OPENAI_API_KEY` |
| Google | Gemini 2.0 Flash, Gemini 1.5 Pro | `GOOGLE_API_KEY` |

**Enabling Streaming (Threaded Mode):**

For real-time streaming responses, the bot must have Threaded Mode enabled:

1. Open **@BotFather** in Telegram
2. Send `/mybots`
3. Select your bot
4. Go to **Bot Settings**
5. Toggle **Threaded Mode** → ON

Once enabled, users can create topic threads in their chat with the bot. Messages sent within topics will display streaming responses as they're generated.

> **Note:** Without Threaded Mode, the bot falls back to progressive message editing which still works but updates less smoothly.

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
- **CEX** - Active account, last order parameters
- **DEX** - Default network/connector, last swap parameters
- **General** - Active server

## Security

- **Admin Whitelist** - Only `ADMIN_USER_ID` has initial access
- **Role-Based Access** - Admin, User, Pending, Blocked roles
- **@restricted Decorator** - Applied to all command handlers
- **Secret Masking** - Passwords hidden in UI

## Configuration Files

### `.env`
```bash
TELEGRAM_TOKEN=your_bot_token
ADMIN_USER_ID=123456789

# LLM API Keys (at least one required for /chat)
ANTHROPIC_API_KEY=sk-ant-...  # For Claude models
OPENAI_API_KEY=sk-...         # For GPT models
GOOGLE_API_KEY=AIza...        # For Gemini models
```

### `config.yml` (auto-created on first run)
```yaml
servers:
  main:
    host: localhost
    port: 8000
    username: admin
    password: admin
default_server: main
admin_id: 123456789
users: {}
server_access: {}
chat_defaults: {}
audit_log: []
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Bot not responding | Check `TELEGRAM_TOKEN` and `ADMIN_USER_ID` in `.env` |
| Access pending | Admin must approve user via /config > Admin Panel |
| Commands failing | Verify Hummingbot API is running |
| Connection refused | Check server host:port in `/config` |
| Auth error | Verify server credentials |
| DEX features unavailable | Ensure Gateway is configured and running |
| /chat shows no models | Set at least one LLM API key in `.env` |
| Chat not streaming | Enable Threaded Mode in @BotFather, use topic threads |

## Docker Deployment

```bash
# Setup and run with Docker
make setup       # Interactive configuration
docker compose up -d
```

Volumes mounted:
- `condor_bot_data.pickle` - User preferences and state
- `config.yml` - Server and permission configuration
- `routines/` - Custom automation scripts

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

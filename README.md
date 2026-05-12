# Condor

A Telegram bot for monitoring and trading with Hummingbot via the **Hummingbot Backend API**.

## Features

- **Portfolio Dashboard** - Comprehensive portfolio view with PNL tracking, 24h changes, and graphical analysis
- **Bot Monitoring** - Track active Hummingbot trading bots with real-time status and metrics
- **CLOB Trading** - Place orders on centralized exchanges (Binance, Bybit, etc.) with interactive menus
- **DEX Trading** - Swap tokens and manage CLMM liquidity positions via Gateway
- **Configuration** - Manage API servers, exchange credentials, and Gateway through Telegram (`/servers`, `/keys`, `/gateway`)
- **AI Assistant** - Natural language trading help via **`/agent`** (optional OpenAI or OpenRouter keys; MCP tools when configured)

## What you need

- A **Mac** or **Linux** computer (Windows users: install **WSL2** with Ubuntu, then use Terminal inside Ubuntu).
- The **Terminal** app open.
- A **stable internet** connection.
- For **Hummingbot API** (the API-only install below, or if you choose to add the API during Condor setup): **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** (Mac/Windows) or Docker on Linux, **installed and running** on that machine before you run the command.

---

## Install Condor (start here)

Open Terminal, go to an **empty folder** where you are happy to create files (for example your home folder, or `cd Desktop` first), then paste:

```bash
curl -fsSL https://raw.githubusercontent.com/hummingbot/deploy/main/setup.sh | bash
```

The installer walks you through setup—for example your **Telegram** bot token and your **Telegram user id**—and can also install **Hummingbot API** on the **same machine** if you choose that when it asks. When it finishes, continue to **After installation** below.

---

## Install only Hummingbot API

Use this when you are deploying **Hummingbot API** on its own machine (for example a **VPS** or another **remote server**), or any time you **only** need the API and database stack and **not** Condor. **Docker** must be installed and running on that server before you run the command:

```bash
curl -fsSL https://raw.githubusercontent.com/hummingbot/deploy/main/setup.sh | bash -s -- --hummingbot-api
```

---

## After installation

The following applies after **Install Condor**. If you used **Install only Hummingbot API**, use your API host’s health checks and client docs instead; point Condor (or other clients) at that API’s base URL when you connect them.

- Open the **Telegram** chat with your Condor bot. When startup succeeds, admins receive a message such as **"Condor is online and ready."**
- **Logs:** Condor runs in a **tmux** session named `condor`. Attach with `tmux attach -t condor`. Detach without stopping the bot: **Ctrl+B**, then **D**. To stop Condor completely: `tmux kill-session -t condor`.
- In Telegram, use **`/servers`** for Hummingbot Backend API URLs and auth, **`/keys`** for exchange credentials, and **`/gateway`** for DEX setup (or **`/start`** for the setup shortcuts) so commands like `/portfolio` and `/trade` can reach your stack.
- If something fails, see **Troubleshooting** below.

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome, access requests, and shortcuts to servers, keys, and Gateway |
| `/portfolio` | Portfolio dashboard with PNL indicators, holdings, and graphs |
| `/bots` | Active bots with status and metrics |
| `/new_bot` | Create bot configurations |
| `/executors` | Deploy and manage trading executors |
| `/trade` | CEX and DEX trading menu (spot & perpetual orders, positions, swaps) |
| `/swap` | Same trading flow as `/trade` (convenient alias) |
| `/lp` | DEX liquidity pool management (positions, pools) |
| `/routines` | Auto-discoverable Python scripts with scheduling |
| `/agent` | AI trading assistant (optional LLM keys in `.env`) |
| `/servers` | Hummingbot Backend API servers (add, edit, default, status) |
| `/keys` | Exchange API credentials per account |
| `/gateway` | Gateway configuration for DEX |
| `/web` | Time-limited link to the web dashboard |
| `/admin` | Admin panel: users and access (admin role only) |
| `/update` | Check for updates and restart (admin role only) |

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
│   ├── config/                 # Configuration module (/servers, /keys, /gateway)
│   │   ├── __init__.py         # Main command
│   │   ├── servers.py          # API server management
│   │   ├── api_keys.py         # Exchange credentials
│   │   └── gateway/            # Gateway configuration
│   ├── routines/               # Routines module (/routines)
│   │   └── __init__.py         # Script discovery and execution
│   └── admin/                  # Admin panel (/admin)
├── routines/                   # User-defined automation scripts
├── utils/                      # Utilities
│   ├── auth.py                 # @restricted, @admin_required decorators
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

### Configuration (`/servers`, `/keys`, `/gateway`)
- **API Servers** (`/servers`) - Add, modify, delete Hummingbot Backend API servers
  - Real-time status checking (online/offline/auth error)
  - Set default server
  - Progressive form for adding servers
- **API Keys** (`/keys`) - Manage exchange credentials per account
  - View connected exchanges
  - Add new credentials (field-by-field input)
  - Delete credentials with confirmation
- **Gateway** (`/gateway`) - DEX connector settings and Gateway status

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
OPENAI_API_KEY=sk-...                    # Optional, for AI features
OPENROUTER_API_KEY=sk-or-...             # Optional, unlocks the OpenRouter LLM picker
```

> **OpenRouter:** Add `OPENROUTER_API_KEY` to `.env`, then in `/agent → Change LLM`
> select **OpenRouter — Pick Model**. The picker fetches the live catalog and shows
> only models that support tool-calling. Get a key at
> [openrouter.ai/keys](https://openrouter.ai/keys).

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
| Access pending | Admin must approve the user via `/admin` or the admin flow from `/start` |
| Commands failing | Verify Hummingbot Backend API is running and reachable |
| Connection refused | Check host and port under `/servers` for the active server |
| Auth error | Verify API username/password in `/servers` |
| DEX features unavailable | Ensure Gateway is configured and running (`/gateway`) |

## Development

### Run from source

For local development or manual setup without the [deploy installer](https://github.com/hummingbot/deploy):

```bash
git clone https://github.com/hummingbot/condor.git
cd condor
make install     # Interactive setup + uv deps + AI CLI tools
make run         # Start the bot
```

To run **Hummingbot API** locally with Docker (for example from a sibling clone of [hummingbot-api](https://github.com/hummingbot/hummingbot-api)):

```bash
cd ../hummingbot-api
docker compose up -d
```

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

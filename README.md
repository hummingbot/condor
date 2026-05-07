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

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/), Telegram Bot Token

```bash
git clone https://github.com/hummingbot/condor.git
cd condor

make install     # Interactive setup + uv deps + AI CLI tools
make run         # Start the bot
```

To start Hummingbot API separately:

```bash
cd ../hummingbot-api && docker compose up -d
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
│   └── admin/                  # Admin panel (via /config)
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

## Secure Connection via Tailscale

[Tailscale](https://tailscale.com) creates a private WireGuard network (tailnet) so Condor can reach Hummingbot API securely without exposing port 8000 publicly.

Use this when:
- Hummingbot API is running on a remote server or VPS
- You want an encrypted private connection without opening firewall ports

### Prerequisites: Get a Tailscale auth key

1. Create a free account at [tailscale.com](https://tailscale.com)
2. Go to **Settings → Keys**: [tailscale.com/admin/settings/keys](https://tailscale.com/admin/settings/keys)
3. Click **Generate auth key** — check **Reusable** for multiple deployments
4. Copy the key (starts with `tskey-auth-`)

### Setup

Run `make setup` — the wizard supports two Tailscale scenarios:

**Scenario A — Deploy Hummingbot API locally with Tailscale**

Choose `Y` to deploy Hummingbot API locally, then `y` when asked about Tailscale. The wizard will:
- Clone and start `hummingbot-api` with a Tailscale sidecar container
- Install Tailscale on this machine and connect with hostname `condor`
- Update `config.yml` to reach the API at `http://hummingbot-api:8000`

**Scenario B — Connect to a remote Hummingbot API via Tailscale**

Choose `N` to skip local deployment, enter the remote API URL, then `y` when asked about Tailscale. The wizard will:
- Install Tailscale on this machine and connect with hostname `condor`
- Update `config.yml` to use the Tailscale MagicDNS hostname of the remote API

The remote machine must be running [hummingbot-api-tailscale](https://github.com/hummingbot/hummingbot-api) on the same tailnet.

### Network layout

```
[Condor]  tailnet: condor  ──WireGuard──►  [API]  tailnet: hummingbot-api
                           http://hummingbot-api:8000
```

Both machines must be on the same Tailscale account.

### Verify the connection

```bash
tailscale status
```

Both `condor` and `hummingbot-api` should appear as connected peers.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Bot not responding | Check `TELEGRAM_TOKEN` and `ADMIN_USER_ID` in `.env` |
| Access pending | Admin must approve user via /config > Admin Panel |
| Commands failing | Verify Hummingbot API is running |
| Connection refused | Check server host:port in `/config` |
| Auth error | Verify server credentials |
| DEX features unavailable | Ensure Gateway is configured and running |
| Tailscale: can't reach API | Run `tailscale status` — confirm both peers are connected |
| Tailscale: auth key rejected | Key must start with `tskey-auth-`, check expiry in Tailscale admin |

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

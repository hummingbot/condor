# Condor

A Telegram bot for monitoring and trading with Hummingbot via the **Hummingbot API**.

> **Why we recommend Tailscale for production**
>
> Condor controls real trading through Hummingbot API: orders, balances, bots, and stored exchange keys. That has always required strong passwords and careful configuration—but **the risk surface has grown**. Trading agents, MCP tools, and other AI assistants make powerful API actions easier to trigger, while cloud VPSes are constantly scanned for open ports like **8000**.
>
> **Tailscale is one safeguard you can add**: it puts the API on a private encrypted network so only your devices can reach it, without publishing port 8000 to the internet. It does **not** replace proper security—use strong API and config passwords, and avoid exposing sensitive services publicly. Tailscale also works when Condor and the API run on the **same machine**.
>
> Full walkthrough: [Securing Condor and Hummingbot API with Tailscale](https://hummingbot.org/blog/posts/securing-condor-and-hummingbot-api-with-tailscale/) · [Hummingbot API Tailscale guide](https://hummingbot.org/hummingbot-api/tailscale/)

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
- **[Tailscale](https://tailscale.com) account** (free tier is enough) — **recommended for production**, especially when Condor and the API run on different machines. Create a [reusable auth key](https://login.tailscale.com/admin/settings/keys) and enable [MagicDNS](https://login.tailscale.com/admin/dns) before you install.

---

## Install Condor (start here)

### Before you install (production)

1. Create a free account at [tailscale.com](https://tailscale.com)
2. Generate a **reusable** auth key at [Settings → Keys](https://login.tailscale.com/admin/settings/keys) (starts with `tskey-auth-`)
3. Enable **[MagicDNS](https://login.tailscale.com/admin/dns)** in the Tailscale admin console

Open Terminal, go to an **empty folder** where you are happy to create files (for example your home folder, or `cd Desktop` first), then paste:

```bash
curl -fsSL https://raw.githubusercontent.com/hummingbot/deploy/main/setup.sh | bash
```

The installer walks you through setup—for example your **Telegram** bot token and your **Telegram user id**—and can also install **Hummingbot API** on the **same machine** if you choose that when it asks.

When installing Hummingbot API, answer **`y`** when asked to enable Tailscale and paste your auth key. When it finishes, continue to **After installation** below.

---

## Install only Hummingbot API

Use this when you are deploying **Hummingbot API** on its own machine (for example a **VPS** or another **remote server**), or any time you **only** need the API and database stack and **not** Condor. **Docker** must be installed and running on that server before you run the command:

```bash
curl -fsSL https://raw.githubusercontent.com/hummingbot/deploy/main/setup.sh | bash -s -- --hummingbot-api
```

**Enable Tailscale when prompted** (answer **`y`**) so Condor and other clients can reach the API at `http://hummingbot-api:8000` on your private tailnet—without opening port 8000 on a public IP.

If the script finishes but services did not start:

```bash
cd hummingbot-api
make setup
make deploy
```

---

## After installation

The following applies after **Install Condor**. If you used **Install only Hummingbot API**, use your API host's health checks and client docs instead; point Condor (or other clients) at that API when you connect them.

| Where you connect from | API URL |
|------------------------|---------|
| Same machine as the API | `http://localhost:8000` |
| Another device on your tailnet (Condor, browser) | `http://hummingbot-api:8000` |

- Open the **Telegram** chat with your Condor bot. When startup succeeds, admins receive a message such as **"Condor is online and ready."**
- **Logs:** Condor runs in a **tmux** session named `condor`. Attach with `tmux attach -t condor`. Detach without stopping the bot: **Ctrl+B**, then **D**. To stop Condor completely: `tmux kill-session -t condor`.
- In Telegram, use **`/servers`** for Hummingbot API URLs and auth, **`/keys`** for exchange credentials, and **`/gateway`** for DEX setup (or **`/start`** for the setup shortcuts) so commands like `/portfolio` and `/trade` can reach your stack.
- If Condor and the API are on **different machines**, install [Tailscale](https://tailscale.com/download) on the Condor host and add the API in **`/servers`** with host **`hummingbot-api`** (not a public IP). See [Secure Connection via Tailscale](#secure-connection-via-tailscale) below.
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
| `/servers` | Hummingbot API servers (add, edit, default, status) |
| `/keys` | Exchange API credentials per account |
| `/gateway` | Gateway configuration for DEX |
| `/web` | Time-limited link to the web dashboard |
| `/admin` | Admin panel: users and access (admin role only) |
| `/update` | Check for updates and restart (admin role only) |

## Architecture

```
Telegram → Condor Bot → Hummingbot API → Trading Bots
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
- **API Servers** (`/servers`) - Add, modify, delete Hummingbot API servers
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

## Secure Connection via Tailscale

[Tailscale](https://tailscale.com) creates a private WireGuard network (tailnet) so Condor can reach Hummingbot API securely without exposing port 8000 publicly.

Use this when:
- Hummingbot API is running on a remote server or VPS
- You want an encrypted private connection without opening firewall ports
- Condor runs on your laptop and the API runs in the cloud (most common production layout)

Tailscale also works when Condor and the API run on the **same machine**—you still get a stable hostname and avoid publishing port 8000 publicly.

### Prerequisites: Get a Tailscale auth key

1. Create a free account at [tailscale.com](https://tailscale.com)
2. Go to **Settings → Keys**: [tailscale.com/admin/settings/keys](https://tailscale.com/admin/settings/keys)
3. Click **Generate auth key** — check **Reusable** for multiple deployments
4. Copy the key (starts with `tskey-auth-`)
5. Enable **[MagicDNS](https://login.tailscale.com/admin/dns)** in the Tailscale admin console

### On the API server

1. Run the **Install only Hummingbot API** command (or Quick Start with API enabled)
2. When asked **Use Tailscale for secure private networking?**, answer **`y`**
3. Paste your auth key and deploy:

```bash
cd hummingbot-api
make deploy
make tailscale-status   # confirm hummingbot-api appears on your tailnet
```

### On the Condor machine

1. Install [Tailscale](https://tailscale.com/download) and sign in to the **same account**
2. In Telegram, open **`/servers`** and add the API with:
   - **Host**: `hummingbot-api` (MagicDNS name, not a public IP)
   - **Port**: `8000`
   - **Username / Password**: same as the API `.env`

Test from the Condor host:

```bash
curl -u YOUR_USERNAME:YOUR_PASSWORD http://hummingbot-api:8000/health
```

### Manual install (`make install`)

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

The remote machine must be running [hummingbot-api](https://github.com/hummingbot/hummingbot-api) with Tailscale enabled on the same tailnet.

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

**Do not open port 8000 on your public firewall** when Tailscale is enabled. Allow SSH (port 22) for server administration only.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Bot not responding | Check `TELEGRAM_TOKEN` and `ADMIN_USER_ID` in `.env` |
| Access pending | Admin must approve user via /config > Admin Panel |
| Commands failing | Verify Hummingbot API is running |
| Connection refused | Check server host:port in `/servers`; use `hummingbot-api` (not `localhost`) when API is on another machine via Tailscale |
| Auth error | Verify server credentials match the API `.env` |
| DEX features unavailable | Ensure Gateway is configured and running |
| Tailscale: name `hummingbot-api` does not work | Enable **MagicDNS** in [Tailscale DNS settings](https://login.tailscale.com/admin/dns) |
| Tailscale: can't reach API | Run `tailscale status` — confirm both peers are connected; on API server run `make tailscale-status` |
| Tailscale: auth key rejected | Key must start with `tskey-auth-`, check expiry in Tailscale admin |
| API still reachable on public IP | Remove port **8000** from your cloud provider's firewall / security group |

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
make setup    # answer y for Tailscale on production/VPS setups
make deploy
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

## Support

- **Docs**: https://condor.hummingbot.org
- **Installation guide**: https://condor.hummingbot.org/getting-started/installing
- **Tailscale guide**: https://hummingbot.org/hummingbot-api/tailscale/
- **Issues**: https://github.com/hummingbot/condor/issues

---

**Built with [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) and [Hummingbot](https://hummingbot.org/)**

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

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/), Hummingbot Backend API running, Telegram Bot Token

```bash
git clone https://github.com/hummingbot/condor.git
cd condor

# Option 1: Local Python
make install     # Interactive setup + uv deps + AI CLI tools
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
OPENAI_API_KEY=sk-...  # Optional, for AI features
```

### `config.yml` (auto-created on first run)
```yaml
servers:
  main:
    host: localhost
    port: 8000
    protocol: auto   # optional: auto | http | https
    tls_verify: true # optional: true | false (HTTPS certificate verification)
    ca_bundle_path: null # optional: custom CA bundle path for private/self-signed certs
    client_cert_path: null # optional: client cert path for mTLS
    client_key_path: null # optional: client key path for mTLS
    username: admin
    password: admin
default_server: main
admin_id: 123456789
users: {}
server_access: {}
chat_defaults: {}
audit_log: []
```

## HTTPS / SSL Server Configuration

Condor supports both HTTP and HTTPS when connecting to Hummingbot API servers.

### Protocol resolution

For each server in `config.yml`:
- If `host` includes a scheme (`http://` or `https://`), that scheme is used.
- Otherwise, `protocol` is used when provided (`http` or `https`).
- If `protocol` is omitted or set to `auto`:
  - port `443` => HTTPS
  - any other port => HTTP

### Recommended patterns

**Local development (HTTP):**
```yaml
servers:
  local:
    host: localhost
    port: 8000
    protocol: auto
    username: admin
    password: admin
```

**Production (HTTPS on 443):**
```yaml
servers:
  production:
    host: api.example.com
    port: 443
    protocol: auto
    tls_verify: true
    username: admin
    password: strong_password
```

**Explicit HTTPS custom port:**
```yaml
servers:
  production_custom:
    host: api.example.com
    port: 8443
    protocol: https
    tls_verify: true
    username: admin
    password: strong_password
```

**Host with explicit scheme:**
```yaml
servers:
  explicit:
    host: https://api.example.com
    port: 443
    tls_verify: true
    username: admin
    password: strong_password
```

**HTTPS with private/internal CA:**
```yaml
servers:
  private_ca:
    host: api.internal.example.com
    port: 443
    protocol: auto
    tls_verify: true
    ca_bundle_path: /etc/ssl/certs/internal-ca.pem
    username: admin
    password: strong_password
```

**mTLS (client certificate authentication):**
```yaml
servers:
  mtls:
    host: api.example.com
    port: 443
    protocol: auto
    tls_verify: true
    ca_bundle_path: /etc/ssl/certs/ca.pem
    client_cert_path: /etc/ssl/certs/condor-client.pem
    client_key_path: /etc/ssl/private/condor-client.key
    username: admin
    password: strong_password
```

**Temporary insecure mode (not recommended):**
```yaml
servers:
  lab:
    host: lab.example.local
    port: 8443
    protocol: https
    tls_verify: false
    username: admin
    password: admin
```

### Validation behavior

Condor validates server URL settings and rejects conflicting configurations, for example:
- `host: "https://api.example.com:9000"` with `port: 8000`
- `host: "https://api.example.com"` with `protocol: http`

When `tls_verify` is `true` and `ca_bundle_path` is provided, Condor uses that CA bundle for certificate validation.
When `tls_verify` is `false`, Condor disables certificate verification for that server.
If `client_cert_path` or `client_key_path` is provided, both must be provided (mTLS pair).

## End-to-End Certificate Setup (Hummingbot API + Condor)

The easiest secure local setup is to generate a local CA, issue a server cert for Hummingbot API, then configure Condor to trust that CA.

### 1) Generate a local CA and server cert (OpenSSL example)

```bash
# Create a local CA
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -out ca.pem -subj "/CN=Condor Local CA"

# Create server key + CSR
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=localhost"

# Sign server cert with local CA
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
  -out server.pem -days 825 -sha256
```

### Alternative: automated generation from `hummingbot-api`

If you run the sibling `hummingbot-api` repo setup:

```bash
make generate-certs
```

It will generate certs in `hummingbot-api/certs` and print exact paths you can copy into Condor (`ca_bundle_path`, and optional mTLS client cert/key paths).

### 2) Run Hummingbot API with HTTPS

```bash
uvicorn main:app --host 0.0.0.0 --port 8443 \
  --ssl-certfile /path/to/server.pem \
  --ssl-keyfile /path/to/server.key
```

### 3) Configure Condor to trust that CA

```yaml
servers:
  local_https:
    host: localhost
    port: 8443
    protocol: auto
    tls_verify: true
    ca_bundle_path: /path/to/ca.pem
    username: admin
    password: admin
```

### 4) Optional mTLS (if server requires client certs)

Generate client cert/key signed by the same CA and add:

```yaml
    client_cert_path: /path/to/client.pem
    client_key_path: /path/to/client.key
```

### Backward compatibility and migration

Existing configurations continue to work without changes:
- `host: localhost`, `port: 8000` still resolves to `http://localhost:8000`
- configurations already using `http://...` or `https://...` in `host` remain supported

For migration to HTTPS, update one server at a time and verify status in `/servers` before switching `default_server`.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Bot not responding | Check `TELEGRAM_TOKEN` and `ADMIN_USER_ID` in `.env` |
| Access pending | Admin must approve user via /config > Admin Panel |
| Commands failing | Verify Hummingbot API is running |
| Connection refused | Check server host:port in `/config` |
| Auth error | Verify server credentials |
| TLS handshake/certificate errors | Verify API certificate validity and hostname; if using custom CA, ensure Condor environment trusts it |
| Invalid server URL config | Check for conflicts between scheme/host/port/protocol (for example host URL already includes a different port) |
| DEX features unavailable | Ensure Gateway is configured and running |

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

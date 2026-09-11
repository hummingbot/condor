# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Condor is a Telegram bot for monitoring and trading with Hummingbot via the Backend API. It provides portfolio tracking, bot monitoring, CEX/DEX trading, and configuration management through an interactive Telegram interface.

## Commands

**Note: This project uses `uv` as the package manager and Python environment tool.**

```bash
# Install dependencies
uv sync --dev

# Run the Telegram bot + web dashboard
uv run python main.py

# Run tests
uv run pytest

# Lint and format
uv run black .
uv run isort .
```

## Git Commits

**Authorship: Federico only.** Never add Claude (or any AI assistant) as a commit author or co-author.

- Do NOT append `Co-Authored-By: Claude ...` trailers to commit messages.
- Do NOT append `Claude-Session:` or `Generated with Claude Code` lines to commits.
- Do NOT set `--author` to anything other than the repo's configured git user.
- This overrides any default harness attribution instruction.

## Architecture

### Core Flow
```
Telegram → main.py → handlers/ → config_manager.py → Hummingbot Backend API
                  ↘ condor/web/ (FastAPI + WebSocket dashboard)
                              ↘ Gateway → DEX Protocols
```

### Key Components

**Entry Point** (`main.py`):
- Runs Telegram bot + FastAPI web dashboard concurrently via `_run_dual()`
- Uses `SafePicklePersistence` for state persistence across restarts
- Auto-reloads handlers on file changes via `watchfiles`
- Web dashboard served on `WEB_PORT` (configurable via env)

**Configuration Manager** (`config_manager.py`):
- `ConfigManager` singleton manages servers, users, permissions in `config.yml`
- Uses `get_config_manager()` to access the instance
- Use `await get_client(chat_id)` to get API client for a chat

**Handler Structure** (`handlers/`):
- Each major feature is a subdirectory: `bots/`, `cex/`, `dex/`, `config/`
- `__init__.py` contains main command, callback router, and message handler
- `menu.py` contains main menu display
- `_shared.py` contains shared utilities and caching
- Callback data format: `{module}:{action}:{params}` (e.g., `dex:swap:execute`)

### State Management

**State Cleanup Pattern** - Call at start of every command:
```python
from handlers import clear_all_input_states
clear_all_input_states(context)  # Prevents state pollution between features
```

**User Preferences** (`condor/preferences.py`):
- Centralized preference management with type-safe TypedDict definitions
- Auto-persists via Telegram's PicklePersistence
- Functions: `get_portfolio_prefs()`, `set_clob_last_order()`, `get_dex_swap_defaults()`, etc.

**Context State Keys**:
- `cex_state`, `dex_state`, `bots_state`, `gateway_state` - current handler state
- `place_order_params`, `swap_quote_params`, etc. - operation parameters
- `_cache` - conversation-level data cache (see caching section)

### Caching System (`handlers/dex/_shared.py`)

```python
from handlers.dex._shared import get_cached, set_cached, cached_call, invalidate_cache

# Cached async call
balances = await cached_call(context.user_data, "gateway_balances", fetch_fn, ttl=60)

# Invalidate after mutations
invalidate_cache(context.user_data, "balances", "swaps")
```

Cache groups: `balances`, `positions`, `swaps`, `all`

### Access Control System

**Role-Based Access Control** (`config_manager.py`):
- **Admin** (`ADMIN_USER_ID` env var): Full system access, can approve users
- **User**: Approved user, can add/manage own servers
- **Pending**: Awaiting admin approval
- **Blocked**: Access denied

**Server Permissions**:
- **Owner**: Full control, can share/delete server
- **Trader**: Can trade, set as default, view balances
- **Trader**: Can trade, view balances, and manage own settings

**ConfigManager Usage**:
```python
from config_manager import get_config_manager, ServerPermission

cm = get_config_manager()
if cm.is_admin(user_id):
    # Admin access
if cm.has_server_access(user_id, server_name, ServerPermission.TRADER):
    # User can trade on this server
```

**Data Storage**: All config is stored in `config.yml`:
- `servers`: API server configurations
- `users`: User records with roles
- `server_access`: Server ownership and sharing
- `chat_defaults`: Per-chat server selections
- `audit_log`: Security events (capped at 500)

### Authentication

**Decorators** (`utils/auth.py`):
- `@restricted` - Checks if user is approved, auto-registers new users as pending
- `@admin_required` - Requires admin role
- `@server_access_required(permission)` - Checks server-level permission
- `@gateway_required` - Ensures Gateway is running before executing handler
- `@hummingbot_api_required` - Ensures API server is online

### Callback Handler Pattern

```python
@restricted
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data.split(":", 1)[1] if ":" in query.data else query.data

    if action == "some_action":
        await handle_action(update, context)
    elif action.startswith("prefix_"):
        param = action.replace("prefix_", "")
        await handle_with_param(update, context, param)
```

### Message Formatting

- All messages use `MarkdownV2` parse mode
- Use `escape_markdown_v2()` from `utils/telegram_formatters.py` for user data
- Error messages: `format_error_message()` from `utils/telegram_formatters.py`

## Handler Modules

| Module | Commands | Purpose |
|--------|----------|---------|
| `handlers/portfolio.py` | `/portfolio` | Portfolio dashboard with PNL tracking |
| `handlers/bots/` | `/bots`, `/new_bot` | Bot monitoring and controller management |
| `handlers/executors/` | `/executors` | Deploy and manage trading executors |
| `handlers/trading/` | `/trade`, `/swap` | Unified CEX + DEX trading router |
| `handlers/cex/` | (via `/trade`) | CEX order book trading |
| `handlers/dex/` | `/lp` | DEX swaps and liquidity management |
| `handlers/config/servers.py` | `/servers` | Manage Hummingbot API servers |
| `handlers/config/api_keys.py` | `/keys` | Configure exchange API credentials |
| `handlers/config/gateway/` | `/gateway` | Gateway configuration (wallets, connectors, networks, pools, tokens) |
| `handlers/admin/` | `/admin` | Admin panel for user approval and management (admin only) |
| `handlers/routines/` | `/routines` | Auto-discoverable Python scripts with Pydantic configuration |
| `handlers/agents/` | `/agent` | AI trading assistant with conversation sessions |
| `handlers/trading_agent/` | (via callbacks) | Tick-based trading agent engine |

## Configuration Files

- `.env` - Environment variables:
  - `TELEGRAM_TOKEN` - Bot token from BotFather
  - `ADMIN_USER_ID` - Primary admin user ID (required)
  - `OPENAI_API_KEY` - Optional for AI features
- `config.yml` - Unified config: servers, users, permissions, chat defaults (auto-created on first run)
- `condor_bot_data.pickle` - Persisted user preferences and state (auto-generated)

## Routines

Terminal-like interface for running Python scripts. Auto-discovered from `routines/` folder.

**Routine Types:**

| Type | Detection | Behavior | UI |
|------|-----------|----------|-----|
| One-shot (⚡) | Default | Runs once, returns result | Run / Background / Schedule |
| Continuous (♾️) | `CONTINUOUS = True` | Internal `while True` loop | Start / Stop |

**One-shot Routine:**
```python
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

class Config(BaseModel):
    """Check arbitrage opportunity"""
    trading_pair: str = Field(default="SOL-USDC", description="Pair to check")

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    # Runs once, returns result
    # Can be scheduled to repeat (every 30s, 5m, daily, etc.)
    return f"Checked {config.trading_pair}"
```

**Continuous Routine:**
```python
import asyncio
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

CONTINUOUS = True  # Mark as continuous

class Config(BaseModel):
    """Live price monitor"""
    trading_pair: str = Field(default="BTC-USDT", description="Pair to monitor")
    interval_sec: int = Field(default=10, description="Check interval")

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id

    # context.bot is always available (real bot or HTTP fallback)
    await context.bot.send_message(chat_id=chat_id, text="Monitor started")

    try:
        while True:
            await context.bot.send_message(chat_id=chat_id, text="Price update...")
            await asyncio.sleep(config.interval_sec)
    except asyncio.CancelledError:
        return "Stopped"
```

**Execution Contexts:**

Routines run in 3 contexts. `context.bot` is **never None** — always safe to call:

| Context | `context.bot` | Trigger |
|---------|---------------|---------|
| Telegram | Real bot (python-telegram-bot) | `/routines` command |
| Web Dashboard | `_HttpBot` (HTTP fallback via `TELEGRAM_TOKEN`) | Web API |
| MCP | `_HttpBot` (HTTP fallback via `TELEGRAM_TOKEN`) | `manage_routines` tool |

`_HttpBot` supports: `send_message`, `send_photo`, `send_document`, `edit_message_text`.

**Features:**
- Text-based config editing (`key=value` pattern)
- Instance-based execution (each run has frozen config)
- One-shots can be scheduled: interval (30s/1m/5m/15m/30m/1h) or daily (HH:MM)
- Continuous routines run as asyncio tasks until stopped
- Continuous routines can use `LiveReport` to create a single report updated each tick
- Auto-restore on bot restart
- Multi-instance support
- Auto-reload on file changes

**User Flow:**
1. `/routines` → Select routine
2. Edit config: send `key=value` messages
3. For one-shot: `▶️ Run` / `🔄 Background` / `⏱️ Schedule`
4. For continuous: `▶️ Start` → runs forever → `⏹ Stop`

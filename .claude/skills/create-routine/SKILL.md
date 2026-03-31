---
name: create-routine
description: Create agent-local analysis routines with full hummingbot-api-client API reference. Use when creating or editing routines for trading agents.
---

# Create Routine

You are creating an agent-local routine for a Condor trading agent. Follow the template, rules, and API reference below exactly.

## Routine Template

```python
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client


class Config(BaseModel):
    """One-line description of what this routine does."""
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    connector_name: str = Field(default="binance_perpetual", description="Exchange connector")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    # Use client.market_data, client.executors, client.portfolio
    # Always return a string
    return "result"
```

## Rules

- **Naming**: lowercase with underscores, e.g. `microstructure_levels`, `funding_scanner`
- **Config docstring** becomes the routine description shown in listings
- **One routine = one task**. Keep routines focused and composable
- **Always return a string** from `run()`. Format results as readable text
- **Use `get_client(context._chat_id, context=context)`** to get the API client
- **Never hardcode credentials** or server URLs
- **For parallel fetches**, use `asyncio.gather`
- **Handle missing data gracefully** — return informative error strings, don't raise

## hummingbot-api-client API Reference

### `client.market_data` — Market Data Router

| Method | Signature |
|--------|-----------|
| **get_candles** | `(connector_name, trading_pair, interval="1m", max_records=100)` |
| **get_historical_candles** | `(connector_name, trading_pair, interval="1m", start_time=None, end_time=None)` |
| **get_candles_last_days** | `(connector_name, trading_pair, days, interval="1h")` |
| **get_order_book** | `(connector_name, trading_pair, depth=10)` |
| **get_prices** | `(connector_name, trading_pairs)` — trading_pairs can be str or list |
| **get_funding_info** | `(connector_name, trading_pair)` |
| **get_price_for_volume** | `(connector_name, trading_pair, volume, is_buy)` |
| **get_volume_for_price** | `(connector_name, trading_pair, price, is_buy)` |
| **get_price_for_quote_volume** | `(connector_name, trading_pair, quote_volume, is_buy)` |
| **get_quote_volume_for_price** | `(connector_name, trading_pair, price, is_buy)` |
| **get_vwap_for_volume** | `(connector_name, trading_pair, volume, is_buy)` |
| **get_available_candle_connectors** | `()` |
| **get_active_feeds** | `()` |
| **get_market_data_settings** | `()` |
| **add_trading_pair** | `(connector_name, trading_pair, account_name=None, timeout=None)` |
| **remove_trading_pair** | `(connector_name, trading_pair, account_name=None)` |
| **get_order_book_diagnostics** | `(connector_name, account_name=None)` |
| **restart_order_book_tracker** | `(connector_name, account_name=None)` |

### `client.portfolio` — Portfolio Router

| Method | Signature |
|--------|-----------|
| **get_state** | `(account_names=None, connector_names=None, skip_gateway=False, refresh=False)` |
| **get_history** | `(account_names=None, connector_names=None, limit=100, cursor=None, start_time=None, end_time=None, interval=None)` |
| **get_distribution** | `(account_names=None, connector_names=None)` |
| **get_accounts_distribution** | `()` |
| **get_total_value** | `(account_name=None, connector_name=None)` — returns `float` |
| **get_token_holdings** | `(token, account_name=None, connector_name=None)` |
| **get_portfolio_summary** | `(account_name=None)` |

### `client.executors` — Executors Router

| Method | Signature |
|--------|-----------|
| **create_executor** | `(executor_config, account_name=None, controller_id=None)` — config is a dict |
| **search_executors** | `(account_names=None, connector_names=None, trading_pairs=None, executor_types=None, status=None, controller_ids=None, cursor=None, limit=50)` |
| **get_summary** | `()` |
| **get_executor** | `(executor_id)` |
| **get_performance_report** | `(controller_id=None)` — takes controller_id, NOT executor_id |
| **stop_executor** | `(executor_id, keep_position=False)` |
| **get_positions_summary** | `(controller_id=None)` |
| **get_position_held** | `(connector_name, trading_pair, account_name=None, controller_id=None)` |
| **clear_position_held** | `(connector_name, trading_pair, account_name=None, controller_id=None)` |
| **get_available_executor_types** | `()` |
| **get_executor_config_schema** | `(executor_type)` |

## Common Mistakes to Avoid

- `get_order_book(connector, pair, depth)` NOT ~~`get_order_book_snapshot`~~
- `get_candles(connector, pair, interval, max_records)` NOT ~~`get_candles(pair, interval, limit)`~~
- `get_performance_report(controller_id=...)` NOT ~~`get_performance_report(executor_id=...)`~~
- `create_executor(executor_config, ...)` — config is a plain dict, NOT a Pydantic model
- All methods are **async** — always `await` them
- `get_total_value()` returns a `float`, all others return `Dict[str, Any]`

## Common Patterns

### Parallel data fetches
```python
import asyncio

ob, candles, portfolio = await asyncio.gather(
    client.market_data.get_order_book(config.connector_name, config.trading_pair, depth=20),
    client.market_data.get_candles(config.connector_name, config.trading_pair, interval="5m", max_records=50),
    client.portfolio.get_state(connector_names=[config.connector_name]),
)
```

### Parsing order book response
```python
ob = await client.market_data.get_order_book(config.connector_name, config.trading_pair, depth=10)
bids = ob.get("bids", [])  # [[price, size], ...]
asks = ob.get("asks", [])  # [[price, size], ...]
best_bid = float(bids[0][0]) if bids else 0
best_ask = float(asks[0][0]) if asks else 0
spread_pct = (best_ask - best_bid) / best_bid * 100 if best_bid else 0
```

### Parsing candles response
```python
candles = await client.market_data.get_candles(config.connector_name, config.trading_pair, interval="1m", max_records=100)
records = candles.get("candles", [])
# Each record: {"timestamp", "open", "high", "low", "close", "volume", ...}
```

### Searching executors by controller
```python
result = await client.executors.search_executors(
    controller_ids=[controller_id],
    status="active",
    limit=50,
)
executors = result.get("executors", [])
```

Now create the routine using `manage_routines(action="create_routine", strategy_id=..., name=..., code=...)`.

# Hummingbot Client API

Patterns for fetching market data, candles, order book, portfolio, and executor
data from a Hummingbot server inside a routine.

## Before the raw client: `condor.fetchers`

Most of what a routine needs is already a documented function — normalized
candles with a fallback ladder, resolved cross-rates, portfolio state, executor
rows. Those are the **first** thing to reach for, and the live inventory lives in
the code, not in this file:

```python
from condor.primitives import catalog, describe
print(catalog("market_data"))                              # what exists
print(describe("market_data.fetch_historical_candles"))    # exact signature
```

Every one of them takes the `client` below as its first argument:

```python
from condor.fetchers.market_data import fetch_historical_candles

rows = await fetch_historical_candles(
    client, "binance", "SOL-USDT", interval="1h", start_time=start, limit=200
)
```

Use the raw client methods documented below when no fetcher covers what you
need. **Do not maintain a function list here** — `catalog()` is generated from
the code and cannot drift; a list in this file can.

## Getting the Client

```python
from config_manager import get_client

client = await get_client(context._chat_id, context=context)
if not client:
    return "No server available"
```

## Market Data

### Candles
```python
result = await client.market_data.get_candles(connector, pair, interval="1m", max_records=100)
# Parse defensively — response shape varies by connector:
records = result if isinstance(result, list) else result.get("data", result.get("candles", []))
# Each record: {"timestamp": ..., "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}
```

### Prices (multiple pairs at once)
```python
prices = await client.market_data.get_prices(connector, trading_pairs=["BTC-USDT", "ETH-USDT"])
# Returns: {"BTC-USDT": 100000.0, "ETH-USDT": 3500.0}
btc_price = prices.get("BTC-USDT", 0)
```

### Order Book
```python
ob = await client.market_data.get_order_book(connector, pair, depth=10)
# Returns: {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
best_bid = ob["bids"][0][0] if ob["bids"] else None
best_ask = ob["asks"][0][0] if ob["asks"] else None
```

### Funding Rate (perpetuals only)
```python
info = await client.market_data.get_funding_info(connector, pair)
# Returns: {"funding_rate": 0.0001, "next_funding_time": ..., "mark_price": ...}
```

## Portfolio

```python
state = await client.portfolio.get_state()
total_usd = await client.portfolio.get_total_value()  # float, USD
```

## Executors

```python
# Active executors
execs = await client.executors.search_executors(controller_ids=[], status="active", limit=50)

# Performance report — use controller_id, NOT executor_id
report = await client.executors.get_performance_report(controller_id=cid)
```

## Common Mistakes

- Use `get_order_book()` NOT `get_order_book_snapshot`
- `get_candles(connector, pair, interval, max_records)` NOT `limit` as kwarg
- `get_performance_report(controller_id=...)` NOT `executor_id`
- All methods are async — always `await`
- Parse candles defensively — never assume the response is a plain list
- Handle `None` / missing keys gracefully — return error strings, don't raise

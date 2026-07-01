# Hummingbot Client API

Patterns for fetching market data, candles, order book, portfolio, and executor
data from a Hummingbot server inside a routine.

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

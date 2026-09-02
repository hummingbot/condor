---
name: market_data_with_code
description: Fetch and analyse market data — use run_code for anything beyond a single
  raw value; canned snippets for common queries.
when_to_use: 'User asks for market data: price, candles, funding rate, order book,
  RSI/EMA/VWAP, comparisons across assets or venues, any derived calculation. Raw
  tools (get_prices, get_funding_rate) are only appropriate for a single, direct lookup
  with no computation. For everything else — indicators, multi-asset, multi-venue,
  aggregations — write a Python snippet and call run_code.'
created: '2026-09-02T15:29:27Z'
source: chat
---

## Market Data — run_code First

### Decision rule

| Request | Approach |
|---|---|
| Single price, one venue | `get_prices` directly |
| Single funding rate, one venue | `get_funding_rate` directly |
| Indicator, VWAP, RSI, EMA, crossover | `run_code` |
| Compare prices / rates across venues | `run_code` |
| Candles + any derived stat | `run_code` |
| Order book analysis (spread, depth) | `run_code` |
| Multi-asset query | `run_code` |

### DEX connectors and candles

Most DEX connectors (meteora, raydium, orca, uniswap, xrpl, jupiter…) do **not** serve OHLCV.
Before writing a candles snippet on a DEX venue, check the `verify_connector_support` skill.
When a connector has no candle endpoint, use **GeckoTerminal** instead:

```python
import pandas as pd
ohlcv = await client.explore_geckoterminal(
    action="ohlcv",
    network="solana",
    pool_address="<pool_address>",
    timeframe="1h",
    limit=168,  # 7 days of 1h bars
)
rows = ohlcv["data"]["attributes"]["ohlcv_list"]
df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
print(df.tail(5))
```

### Context available inside run_code snippets

The snippet runs with `client` (the Hummingbot API client) and `context` pre-bound.
`pandas`, `pandas_ta` and standard library modules are **not** guaranteed to be pre-imported — always import them explicitly.
`print()` is the output; an optional `result` variable is the return value.

```python
# Discover what's available at runtime
from condor.primitives import catalog, describe
print(catalog())               # lists connectors, pairs, intervals
print(describe("get_candles")) # parameter docs for one method
```

### Canned snippets

**Price — single asset**
```python
prices = await client.get_prices("binance", ["SOL-USDT"])
print(prices)
```

**Price — multi-asset**
```python
prices = await client.get_prices("binance", ["SOL-USDT", "BTC-USDT", "ETH-USDT"])
for pair, p in prices.items():
    print(f"{pair}: {p}")
```

**Candles + RSI**
```python
import pandas_ta
df = await client.get_candles("binance", "SOL-USDT", interval="1h", days=7)
df["rsi"] = pandas_ta.rsi(df["close"], length=14)
print(df[["timestamp", "close", "rsi"]].tail(10))
```

**EMA crossover signal**
```python
import pandas_ta
df = await client.get_candles("binance", "SOL-USDT", interval="4h", days=30)
df["ema_fast"] = pandas_ta.ema(df["close"], length=9)
df["ema_slow"] = pandas_ta.ema(df["close"], length=21)
df["signal"] = (df["ema_fast"] > df["ema_slow"]).astype(int)
last = df.iloc[-1]
print(f"Close: {last.close:.4f} | EMA9: {last.ema_fast:.4f} | EMA21: {last.ema_slow:.4f} | Signal: {'LONG' if last.signal else 'SHORT'}")
```

**VWAP (session)**
```python
df = await client.get_candles("binance", "SOL-USDT", interval="1h", days=1)
df["vwap"] = (df["close"] * df["volume"]).cumsum() / df["volume"].cumsum()
print(df[["timestamp", "close", "vwap"]].tail(5))
```

**Funding rate comparison across venues**
```python
pair = "SOL-USDT"
for connector in ["binance_perpetual", "hyperliquid_perpetual", "okx_perpetual"]:
    try:
        fr = await client.get_funding_rate(connector, pair)
        print(f"{connector}: {fr:.6%}")
    except Exception as e:
        print(f"{connector}: unavailable ({e})")
```

**Order book spread and depth**
```python
ob = await client.get_order_book("binance", "SOL-USDT")
# ob["bids"] and ob["asks"] are [[price, qty], ...] sorted best-first
best_bid = ob["bids"][0][0]
best_ask = ob["asks"][0][0]
spread_bps = (best_ask - best_bid) / best_bid * 10000
bid_depth = sum(p * q for p, q in ob["bids"])
ask_depth = sum(p * q for p, q in ob["asks"])
print(f"Spread: {spread_bps:.2f} bps | Bid depth: ${bid_depth:,.0f} | Ask depth: ${ask_depth:,.0f}")
```

**ATR (for stop sizing)**
```python
import pandas_ta
df = await client.get_candles("binance", "SOL-USDT", interval="1h", days=14)
df["atr"] = pandas_ta.atr(df["high"], df["low"], df["close"], length=14)
print(f"ATR(14): {df['atr'].iloc[-1]:.4f}")
```

### Tips
- Prefer `interval="1h"` or `"4h"` for swing analysis; `"1m"` for scalp context.
- If `run_code` fails, the traceback is returned — fix the snippet and rerun (don't retry the same broken code).
- If you find yourself running essentially the same snippet three times, promote it to a routine with `manage_routines(action="create_routine")` (via background delegate).
- Chart the result with a ` ```chart ` fence when the output is a numeric series.
- To persist the result as a dashboard report, use `from condor.reports import ReportBuilder` inside the snippet; the `report_id` comes back in the run result.
- For DEX venues without candles, see the GeckoTerminal section above and the `verify_connector_support` skill.

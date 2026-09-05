---
name: market_data_with_code
description: Fetch and analyse market data — use run_code for anything beyond a single
  raw value; canned snippets for common queries.
when_to_use: 'User asks for market data: price, candles, funding rate, order book,
  RSI/EMA/VWAP, comparisons across assets or venues, any derived calculation. The
  only raw market data tool left is get_prices, and it is only appropriate for a single,
  direct lookup with no computation. For everything else — indicators, multi-asset,
  multi-venue, aggregations — write a Python snippet and call run_code.'
created: '2026-09-02T15:29:27Z'
source: chat
---

## Market Data — `client.market_data.*` inside `run_code`

### ⚠️ STOP — self-audit before any tool call

If you are reading this because a market data request just came in, do this check NOW:

1. **Did you call `manage_skill(action="read", name="market_data_with_code")` before writing any code?**
   - NO → You are here now. Good. Read the schemas below; write the COMPLETE snippet on the NEXT call.
   - YES → Proceed. The schema you need is already in your context.

2. **Is your call covered by the API reference below?** (order book, funding, candles, tickers, prices)
   - YES → Use the documented schema directly. Do NOT call `catalog()`, `dir(client)`, `inspect.signature`, or send a raw debug probe first. Those calls are wasted — the schemas here are verified.
   - NO → A single raw probe is acceptable ONLY for calls not listed here.

**The cost of skipping this read: 5+ extra discovery calls that the playbook already answers.**

---

### First-call rule

**Write the complete, final snippet on the first `run_code` call.**
- Multi-venue requests: all venues, all math, formatted output — in ONE call.
- Never defer to "let me check the structure first" for any call documented below.
- `asyncio.gather(..., return_exceptions=True)` + `isinstance(r, Exception)` per result — never catch and swallow silently.

---

### When to use what

| Request | Tool | ~ms |
|---|---|---|
| Single price, one venue | `get_prices` MCP tool | 100 |
| Single price inside run_code | `client.market_data.get_prices` | 150 |
| Order book — one or many venues | `run_code` → `get_order_book` | 350–500 |
| Funding rate — one or many venues | `run_code` → `get_funding_info` | 350–500 |
| Indicators (RSI / EMA / ATR / VWAP) | `run_code` → `get_candles_last_days` + pandas_ta | varies |
| All tickers for a connector | `run_code` → `get_tickers` | 300 |
| Multi-venue anything with math | `run_code` + `asyncio.gather` | ~500 |
| DEX candles | GeckoTerminal — DEX connectors don't serve OHLCV | varies |

---

### API reference

**Order book**
```python
ob = await client.market_data.get_order_book("binance_perpetual", "BTC-USDT")
# {
#   "trading_pair": "BTC-USDT",
#   "bids": [{"price": 77406.6, "amount": 5.15}, ...],  # best bid first
#   "asks": [{"price": 77406.7, "amount": 17.44}, ...], # best ask first
#   "timestamp": 1788373224.0
# }
best_bid   = ob["bids"][0]["price"]          # dict — NOT ob["bids"][0][0]
best_ask   = ob["asks"][0]["price"]
spread_bps = (best_ask - best_bid) / best_bid * 10_000
bid_depth  = sum(l["price"] * l["amount"] for l in ob["bids"][:10])
ask_depth  = sum(l["price"] * l["amount"] for l in ob["asks"][:10])
imbalance  = (bid_depth - ask_depth) / (bid_depth + ask_depth) * 100
```

**Price**
```python
r = await client.market_data.get_prices("binance_perpetual", ["BTC-USDT", "ETH-USDT"])
# {
#   "connector": "binance_perpetual",
#   "prices": {"BTC-USDT": 77382.8, "ETH-USDT": 2394.4},
#   "timestamp": 1788373395.9
# }
btc = r["prices"]["BTC-USDT"]               # go through ["prices"] first
```

**Candles**
```python
# Use get_candles_last_days for N-day windows (preferred)
# Use get_candles(connector, pair, interval, max_records) for a fixed count
# Use get_historical_candles(connector, pair, interval, start_time, end_time) for unix ranges
rows = await client.market_data.get_candles_last_days("binance_perpetual", "SOL-USDT", days=7, interval="1h")
# Each row — dict with keys:
#   timestamp, open, high, low, close, volume,
#   quote_asset_volume, n_trades,
#   taker_buy_base_volume, taker_buy_quote_volume
import pandas as pd
df = pd.DataFrame(rows)                      # ready for pandas_ta directly
```

**Funding rate**
```python
f = await client.market_data.get_funding_info("binance_perpetual", "BTC-USDT")
# {
#   "trading_pair":      "BTC-USDT",
#   "funding_rate":      2.212e-05,       # rate per 8h period
#   "next_funding_time": 1788393600.0,    # unix ts
#   "mark_price":        77390.8,
#   "index_price":       77418.2
# }
rate_8h  = f["funding_rate"]
rate_apr = rate_8h * 3 * 365 * 100          # annualise (3 payments/day)
```

**Tickers** — all pairs for a connector
```python
t = await client.market_data.get_tickers(connectors=["binance_perpetual"])
# {
#   "tickers": {
#     "binance_perpetual": {
#       "BTC-USDT": {"price": 77382.8, "base_volume": 12345.6, "quote_volume": 9.5e8, "timestamp": ...},
#       "SOL-USDT": {...}, ...
#     }
#   }
# }
pair_data = t["tickers"]["binance_perpetual"]["BTC-USDT"]
price     = pair_data["price"]
```

**Order book impact** — all return a single float
```python
# Price you'd get filling a $50K buy market order
price = await client.market_data.get_price_for_quote_volume("binance_perpetual", "BTC-USDT", 50_000, is_buy=True)
# VWAP for selling 1 BTC through the book
vwap  = await client.market_data.get_vwap_for_volume("binance_perpetual", "BTC-USDT", 1.0, is_buy=False)
# Quote volume fillable at or below a price
vol   = await client.market_data.get_quote_volume_for_price("binance_perpetual", "BTC-USDT", 77400.0, is_buy=True)
```

**Utilities**
```python
# Check which connectors serve OHLCV before using candles on a DEX connector
candle_connectors = await client.market_data.get_available_candle_connectors()
# → ["binance_perpetual", "binance", "okx_perpetual", ...]
```

---

### Multi-venue pattern

```python
import asyncio

venues = [
    ("binance_perpetual",     "BTC-USDT"),
    ("okx_perpetual",         "BTC-USDT"),
    ("hyperliquid_perpetual", "BTC-USD"),  # HL: BTC-USD not BTC-USDT
]

results = await asyncio.gather(
    *[client.market_data.get_order_book(c, p) for c, p in venues],
    return_exceptions=True,
)

for (c, p), r in zip(venues, results):
    if isinstance(r, Exception):
        print(f"{c}: ERROR {r}"); continue
    bids, asks = r["bids"], r["asks"]
    bid = bids[0]["price"]; ask = asks[0]["price"]
    bps   = (ask - bid) / bid * 10_000
    bid_d = sum(l["price"] * l["amount"] for l in bids[:10])
    ask_d = sum(l["price"] * l["amount"] for l in asks[:10])
    imbal = (bid_d - ask_d) / (bid_d + ask_d) * 100
    print(f"{c:<26}  bid={bid:,.1f}  ask={ask:,.1f}  {bps:.2f}bps  bid${bid_d:,.0f}  ask${ask_d:,.0f}  imbal{imbal:+.1f}%")
```

Same pattern works for `get_funding_info`, `get_prices`, or any other method — just swap the call.

---

### Canonical snippets

**Multi-venue funding rate**
```python
import asyncio

venues = [
    ("binance_perpetual",     "BTC-USDT"),
    ("okx_perpetual",         "BTC-USDT"),
    ("hyperliquid_perpetual", "BTC-USD"),
]
results = await asyncio.gather(
    *[client.market_data.get_funding_info(c, p) for c, p in venues],
    return_exceptions=True,
)
print(f"{'Exchange':<26} {'Rate 8h':>10} {'APR':>8} {'Mark':>12} {'Index':>12}")
for (c, p), r in zip(venues, results):
    if isinstance(r, Exception): print(f"{c}: ERROR {r}"); continue
    apr = r["funding_rate"] * 3 * 365 * 100
    print(f"{c:<26} {r['funding_rate']:>10.4%} {apr:>7.2f}% {r['mark_price']:>12,.2f} {r['index_price']:>12,.2f}")
```

**RSI**
```python
import pandas as pd, pandas_ta
df = pd.DataFrame(await client.market_data.get_candles_last_days("binance_perpetual", "SOL-USDT", days=7, interval="1h"))
df["rsi"] = pandas_ta.rsi(df["close"], length=14)
print(df[["timestamp", "close", "rsi"]].tail(10).to_string(index=False))
```

**EMA crossover**
```python
import pandas as pd, pandas_ta
df = pd.DataFrame(await client.market_data.get_candles_last_days("binance_perpetual", "SOL-USDT", days=30, interval="4h"))
df["ema9"]  = pandas_ta.ema(df["close"], length=9)
df["ema21"] = pandas_ta.ema(df["close"], length=21)
last = df.iloc[-1]
print(f"Close: {last.close:.2f}  EMA9: {last.ema9:.2f}  EMA21: {last.ema21:.2f}  → {'LONG' if last.ema9 > last.ema21 else 'SHORT'}")
```

**ATR**
```python
import pandas as pd, pandas_ta
df = pd.DataFrame(await client.market_data.get_candles_last_days("binance_perpetual", "SOL-USDT", days=14, interval="1h"))
df["atr"] = pandas_ta.atr(df["high"], df["low"], df["close"], length=14)
atr = df["atr"].iloc[-1]; price = df["close"].iloc[-1]
print(f"ATR(14): {atr:.4f}  ({atr/price*100:.2f}% of price)")
```

**VWAP**
```python
import pandas as pd
df = pd.DataFrame(await client.market_data.get_candles_last_days("binance_perpetual", "SOL-USDT", days=1, interval="1h"))
df["vwap"] = (df["close"] * df["volume"]).cumsum() / df["volume"].cumsum()
print(df[["timestamp", "close", "vwap"]].tail(5).to_string(index=False))
```

---

### Tips
- **Probe only for undocumented calls.** Every schema above is verified — use it directly.
- Chart time series with a ` ```chart ` fence; persist with `ReportBuilder`.
- Same snippet 3× → promote to a routine via `delegate(...)`.

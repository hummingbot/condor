---
name: verify_connector_support
description: Check what a connector actually supports before using it; when candles
  are unavailable, source history from a proxy or GeckoTerminal instead
when_to_use: 'User asks "can I use connector X?" or "does Y support Z?" — any capability
  question about a connector or DEX. Also: any time OHLCV / candles / price history
  is needed and the connector is not on the candle list — typically xrpl, meteora,
  raydium, orca, uniswap, jupiter and other DEX connectors. Triggers — "Connector
  ''X'' does not support candle data", setting up or backtesting an agent on a DEX
  venue, "get me candles for <pair> on <dex>", computing EMA/RSI/ATR on a non-candle
  venue; ES — "no hay velas para <conector>", "sin datos históricos en <dex>".'
created: '2026-08-12T11:51:59Z'
source: chat
---

## Verify Connector Support

Never guess connector capabilities from memory. Always pull the authoritative source first.

### Capability lookup steps

1. **LP / CLMM questions** → read the `create_lp_executor` tool description — its `lp_provider` parameter lists the supported DEXs
2. **AMM swap / pool-creation questions** → `manage_amm()` (no action) — read the connector list
3. **Pool discovery questions** → `explore_dex_pools` tool description lists supported connectors
4. **Market data / candle questions** → see the **Candles** section below

Answer from what the guide actually says — not from what you remember.

---

## Candles — When the Connector Has No Feed

`get_candles(connector_name=...)` first asks the API which connectors have a candle feed, and **raises** if yours is not one of them:

```
ValueError: Connector 'xrpl' does not support candle data.
Available connectors: ['binance', 'binance_perpetual', 'kucoin', 'kraken', ...]
```

This is a **hard capability gap, not a transient error**. Retrying, changing the interval, changing `days`, or reformatting the pair will never make it succeed. The failing loop this skill exists to stop: agent setup asks for candles on a DEX → error → retries → error → user has to interrupt by hand.

**Who has no candle feed:** `xrpl` and every AMM/CLMM DEX connector (`meteora`, `raydium`, `orca`, `uniswap`, `pancakeswap`, `jupiter`, …), plus any CEX not in the list the error prints. The list is the authority — never assume from the name.

### What still works on that connector

Losing candles does **not** mean losing the venue. These remain live and correct:

- `get_prices(trading_pairs=[...])` — current price
- `get_order_book(...)` — depth, `price_for_volume` / `volume_for_price` slippage queries
- `explore_dex_pools` — pool discovery, TVL, fees, APR (CLMM connectors)
- Trading itself: quoting, swaps, LP and executor deployment

**Execute on the venue the user asked for, source the *history* elsewhere.**

### Where to get the history instead

In order of preference:

1. **A candle-capable venue for the same asset.** `XRP-USDT` on `binance` or `kraken` is the same price series that drives an `xrpl` decision. Pick a connector off the list the error printed, and use a **liquid quote** (USDT/USD), not whatever the DEX pair happens to quote in.
2. **GeckoTerminal, for the actual pool.** For a token with no CEX listing, use `explore_geckoterminal(action="token_pools", ...)` to find the pool, then `explore_geckoterminal(action="ohlcv", network=..., pool_address=...)`. This is the right source when the DEX pool *is* the price discovery venue.
3. **Nothing.** A brand-new token with no CEX listing and a thin pool has no usable history. Say so.

Whichever you pick, **say which series you used and why** — a signal computed on `binance` and executed on `xrpl` is a basis assumption the user is entitled to see, and it is wrong for an illiquid token that trades away from the CEX price.

### Rule when setting up or backtesting an agent

Before promising a candle-driven strategy (EMA, RSI, ATR, any indicator, any backtest) on a venue, resolve the data source **first**:

1. Is the execution connector on the candle list? If yes, nothing here applies.
2. If no — pick the proxy or GeckoTerminal pool above and **tell the user in the same message**: "xrpl has no candle feed; I'll take the signal from `XRP-USDT` on `binance` and execute on `xrpl`."
3. If neither source exists, do **not** silently fall back to a spot-price-only strategy. Report that the market cannot carry an indicator-driven strategy and offer what it can carry — a market-making or LP approach that needs only the order book / pool state.

Never let a missing candle feed turn into a retry loop. One failed candle call on a connector is the answer, not a reason to try again.

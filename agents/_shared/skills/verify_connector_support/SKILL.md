---
name: verify_connector_support
description: Check what a connector actually supports before using it; when candles
  are unavailable, source history from a proxy or GeckoTerminal instead
when_to_use: 'User asks "can I use connector X?" or "does Y support Z?" — any capability
  question about a connector or DEX, including whether a DEX goes through Gateway or
  through Hummingbot directly. Also: any time OHLCV / candles / price history is needed
  and the connector is not on the candle list — typically xrpl and the Gateway AMM/CLMM
  connectors (meteora, raydium, orca, uniswap, jupiter…). Triggers — "Connector ''X''
  does not support candle data", setting up or backtesting an agent on a DEX venue,
  "get me candles for <pair> on <dex>", computing EMA/RSI/ATR on a non-candle venue;
  ES — "no hay velas para <conector>", "sin datos históricos en <dex>".'
created: '2026-08-12T11:51:59Z'
source: chat
---

## Verify Connector Support

Never guess connector capabilities from memory. Always pull the authoritative source first.

### First: which kind of DEX is it?

"DEX" covers two different stacks, and only one of them goes through Gateway:

| Kind | Examples | Runs through | Tools |
|---|---|---|---|
| **AMM / CLMM / DLMM pools, swap routers** | `meteora`, `raydium`, `orca`, `jupiter`, `uniswap`, `pancakeswap` | **Gateway** — `connector_name` is the network (`solana-mainnet-beta`); the DEX rides in `lp_provider` / `swap_provider` | `explore_dex_pools`, `manage_amm`, `manage_clmm`, `quote_swap` / `execute_swap`, `create_lp_executor`, `manage_gateway_config` |
| **CLOB (order-book) DEXs** | `hyperliquid`, `hyperliquid_perpetual`, `xrpl`, `dydx_v4_perpetual`, `injective_v2`, `injective_v2_perpetual`, `derive`, `derive_perpetual`, `dexalot` | **Hummingbot directly**, through the Hummingbot API — exactly like a CEX | `get_prices`, `get_portfolio_overview`, order book / trading rules via `run_code`, every executor, controllers and bots |

A CLOB DEX is a plain Hummingbot connector: its credentials go in Settings → Keys (not a
Gateway wallet), it has no pools, and Gateway knows nothing about it. Never reach for
`manage_gateway_config`, `explore_dex_pools`, `manage_amm` / `manage_clmm` or
`quote_swap` / `execute_swap` for one. Rule of thumb: if you place limit orders on an
order book, it is a Hummingbot connector.

### Capability lookup steps

1. **CLOB DEX questions** (`hyperliquid`, `xrpl`, …) → treat it as a CEX: balances via `get_portfolio_overview`, book and trading rules via `client.market_data.*` / `client.connectors.*` in `run_code`, deploy with executors or controllers
2. **LP / CLMM questions** (Gateway) → read the `create_lp_executor` tool description — its `lp_provider` parameter lists the supported DEXs
3. **AMM swap / pool-creation questions** (Gateway) → `manage_amm()` (no action) — read the connector list
4. **Pool discovery questions** (Gateway) → `explore_dex_pools` tool description lists supported connectors
5. **Market data / candle questions** → see the **Candles** section below

Answer from what the guide actually says — not from what you remember.

---

## Candles — When the Connector Has No Feed

`client.market_data.get_candles*(...)` — inside `run_code`, the only way candles are read — first asks the API which connectors have a candle feed, and **raises** if yours is not one of them:

```
ValueError: Connector 'xrpl' does not support candle data.
Available connectors: ['binance', 'binance_perpetual', 'kucoin', 'kraken', ...]
```

This is a **hard capability gap, not a transient error**. Retrying, changing the interval, changing `days`, or reformatting the pair will never make it succeed. The failing loop this skill exists to stop: agent setup asks for candles on a DEX → error → retries → error → user has to interrupt by hand.

**Who has no candle feed:** every Gateway connector (AMM/CLMM/DLMM: `meteora`, `raydium`, `orca`, `uniswap`, `pancakeswap`, `jupiter`, …), plus any CEX or CLOB DEX not in the list the error prints — `xrpl` is one. Being a DEX is not the test: CLOB DEXs such as `hyperliquid` / `hyperliquid_perpetual` are Hummingbot connectors and do serve candles. The list is the authority — never assume from the name.

### What still works on that connector

Losing candles does **not** mean losing the venue. These remain live and correct:

- `get_prices(trading_pairs=[...])` — current price
- `client.market_data.get_order_book(...)` — depth, and the `price_for_volume` /
  `volume_for_price` slippage queries beside it (CLOB venues, `xrpl` included)
- `explore_dex_pools` — pool discovery, TVL, fees, APR (Gateway CLMM connectors only)
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

### Order Executor
**This is the standard way to place buy/sell orders.** To cancel, use `manage_executors(action="stop")` with the executor ID.

Simple order execution with retry logic and multiple execution strategies.
Closest executor to a plain BUY/SELL order but with strategy options.

**This is also how you swap on a DEX.** Set `connector_name` to a NETWORK
(`"solana-mainnet-beta"`, `"ethereum-mainnet"`) with `execution_strategy="MARKET"` and the
order routes through Gateway's unified swap route — the same one `manage_gateway_swaps`
uses, but with the slippage ramp and an executor record attached. The router is not
selectable per order: it comes from the network's configured `swapProvider`. Prefer this
over `manage_gateway_swaps(action="execute")` for any swap that is part of a strategy.

**Use when:**
- You want a one-off buy or sell with reliable execution
- You want to swap one token for another on a DEX (see above)
- You need a specific execution strategy (MARKET, LIMIT, LIMIT_MAKER, or LIMIT_CHASER)
- You want simple order placement without multi-level complexity

**Avoid when:**
- You need multi-level strategies (use Grid or DCA instead)
- You want automated stop-loss/take-profit management (use Position Executor instead)

**Execution Strategies:**
- `MARKET`: Immediate execution at current market price
- `LIMIT`: Limit order at a specified price
- `LIMIT_MAKER`: Post-only limit order (rejected if it would match immediately)
- `LIMIT_CHASER`: Continuously chases best price, refreshing the limit order as the market moves

**LIMIT_CHASER Config (chaser_config):**
- `distance`: How far from best price to place the order (e.g., 0.001 = 0.1%)
- `refresh_threshold`: How far price must move before refreshing (e.g., 0.0005 = 0.05%)

**Key Parameters:**
- `connector_name`: Exchange to execute on
- `trading_pair`: Trading pair (e.g., 'USDT-BRL'). On a DEX, either side may be a raw
  token address instead of a symbol — `BANKJmvh...-USDC` is valid. The token does NOT
  need to be registered with Gateway first; it resolves the mint and reads decimals
  on-chain. Never add a token to Gateway as a prerequisite for trading it, and never
  guess decimals in order to do so
- `side`: 1 (BUY) or 2 (SELL)
- `amount`: Order amount (base currency, or '$100' for USD value)
- `execution_strategy`: LIMIT, MARKET, LIMIT_MAKER, or LIMIT_CHASER
- `price`: Required for LIMIT/LIMIT_MAKER strategies
- `chaser_config`: Required for LIMIT_CHASER strategy
- `leverage`: Leverage multiplier (default: 1)
- `position_action`: 'OPEN' or 'CLOSE' (default: 'OPEN', useful for perpetuals in HEDGE mode)
- `level_id`: Optional identifier tag

**Solana: whether `executed_amount_base` is what you received depends on the route.**

On an EXACT fill it is exact. A SOL-USDC round trip through `order_executor` on
2026-08-21 moved gross ±0.010000000 SOL in both directions, matching the request to the
lamport, and cost 0.0166% for the pair. A blanket `× 0.995` haircut there under-spends by
half a percent.

It is short when the BUY was APPROXIMATED. A BUY is an ExactOut order, and a thin token
with no ExactOut route is quoted by pricing the sell leg and quoting that input forward —
roughly 2.5%, and up to 4.83% observed. The order is silently resized rather than
overcharged, which is what makes it dangerous to a caller who asked for a quantity.

So the check is conditional, not a constant: the quote's `approximation` flag says which
case you are in. When it is true, read the true post-swap wallet balance before feeding
the amount into a call that must spend those tokens (e.g. an LP open's `base_amount`);
when it is false, use the figure. Passing `extra_params={'approximateIfNoExactOut': False}`
requires an exact route instead of accepting the resize.

**Observability.** `custom_info` carries `transaction_hash` (the on-chain signature —
`order_id` is internal and appears nowhere on chain), plus `slippage_pct` and
`max_slippage_pct`. `slippage_pct` is the LIVE tolerance: a value above the configured
start means earlier attempts failed on slippage and this one is paying to get through.

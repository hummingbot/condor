### Order Executor
**This is the standard way to place buy/sell orders.** To cancel, use `manage_executors(action="stop")` with the executor ID.

Simple order execution with retry logic and multiple execution strategies.
Closest executor to a plain BUY/SELL order but with strategy options.

**Use when:**
- You want a one-off buy or sell with reliable execution
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
- `trading_pair`: Trading pair (e.g., 'USDT-BRL')
- `side`: 1 (BUY) or 2 (SELL)
- `amount`: Order amount (base currency, or '$100' for USD value)
- `execution_strategy`: LIMIT, MARKET, LIMIT_MAKER, or LIMIT_CHASER
- `price`: Required for LIMIT/LIMIT_MAKER strategies
- `chaser_config`: Required for LIMIT_CHASER strategy
- `leverage`: Leverage multiplier (default: 1)
- `position_action`: 'OPEN' or 'CLOSE' (default: 'OPEN', useful for perpetuals in HEDGE mode)
- `level_id`: Optional identifier tag

**Solana / Jupiter: rate limits masquerade as `NO_ROUTE_FOUND`**

When swapping on Solana (Gateway → `jupiter/router`), a throttled request surfaces as:

```
Gateway error: No route found for <TOKEN> -> <TOKEN> (ExactIn).
Unexpected token 'R', "Rate limit"... is not valid JSON  [code: NO_ROUTE_FOUND]
```

The `"Rate limit"` fragment is the tell: Jupiter returned a plain-text rate-limit
body, Gateway failed to parse it as JSON, and reported `NO_ROUTE_FOUND`. **The token
is fine and the route exists** — do not conclude it is unsellable, untradable, or
worth blacklisting. Wait and retry.

Pace batched swaps according to whether a Jupiter key is set (`jupiter.apiKey` in
the connector config):
- **With a Pro/portal key** (routes via `api.jup.ag`, ~60 req/min): sequential
  swaps are fine; ~2 s apart is comfortable.
- **Without a key** (falls back to `lite-api.jup.ag`, much tighter): leave
  **≥15–20 s** between swaps.

Observed: the same three sells failed after **10+ retries each** on the keyless
tier, then succeeded with **0–1 retries** once a key was configured.

**Also note — `executed_amount_base` reports the amount REQUESTED, not received.**
Slippage and fees mean the wallet gets slightly less (observed −0.06% to −0.44%).
Never feed this value straight into a downstream call that must spend those tokens
(e.g. an LP open's `base_amount`) — it will ask for more than you hold and fail
on-chain. Apply a haircut (`× 0.995`) or read the true post-swap wallet balance.

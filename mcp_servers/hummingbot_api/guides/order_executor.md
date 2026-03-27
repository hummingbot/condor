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

### Grid Executor
Trades in ranging/sideways markets with multiple buy/sell levels in a grid pattern.

**Use when:**
- Market is range-bound
- You want to profit from volatility without directional bias
- You want automated rebalancing

**Avoid when:**
- Market is strongly trending (risk of one-sided fills)
- You have limited capital (grids require capital spread across levels)

#### How Grid Trading Works

**LONG Grid (side: 1 = BUY):**
- Places buy limit orders below current price across the range (start_price -> end_price)
- Each filled buy gets a corresponding sell order at take_profit distance
- Instead of buying base asset at once, acquires it gradually via limit orders
- If price rises above end_price -> 100% quote currency, only realized profit from matched pairs
- If price drops below limit_price -> grid stops, accumulated base asset held
  - `keep_position=true`: hold position (wait for recovery)
  - `keep_position=false`: close position at loss
- Take profit for levels above current price is calculated from the theoretical level price, not the entry price

**SHORT Grid (side: 2 = SELL):**
- Places sell limit orders above current price, each fill gets a buy at take_profit below
- If price drops below start_price -> all profit realized
- If price rises above limit_price -> grid stops, accumulated quote from sells
- Useful for selling an existing position -- generates yield while exiting

**CRITICAL:** `side` must be explicitly set (1=BUY, 2=SELL). `limit_price` alone does NOT determine direction.

**Direction Rules:**
- LONG grid:  `limit_price < start_price < end_price` (limit below grid, buys low)
- SHORT grid: `start_price < end_price < limit_price` (limit above grid, sells high)

#### Parameter Reference

**Grid Structure:**
- `start_price` / `end_price`: Grid boundaries (lower/upper)
- `limit_price`: Safety boundary (LONG: below start, SHORT: above end)
- `total_amount_quote`: Capital allocated (quote currency). Must always be specified.

**Grid Density -- How Many Levels:**
- `min_order_amount_quote`: Min size per order -> max possible levels = `total_amount_quote / min_order_amount_quote`
- `min_spread_between_orders`: Min price distance between levels (decimal, e.g. 0.0001 = 0.01%) -> max levels from spread = `price_range / (spread * mid_price)`
- **Actual levels = min(max_from_amount, max_from_spread)** -- the intersection of both constraints

**Order Placement Controls:**
- `activation_bounds`: Only places orders within this % of current price (e.g. 0.001 = 0.1%). Protects liquidity, reduces rate limit usage. If not set, all orders placed at once.
- `order_frequency`: Seconds between order batches. Spaces out submissions, prevents rate limits.
- `max_orders_per_batch`: Max orders per batch. Combined with order_frequency, controls fill speed.
- `max_open_orders`: Hard cap on concurrent open orders.

**Take Profit & Risk:**
- `triple_barrier_config.take_profit`: Profit target as decimal (0.0002 = 0.02%). Distance for the opposite order on fill.
- `triple_barrier_config.open_order_type`: 1=MARKET, 2=LIMIT, 3=LIMIT_MAKER (recommended -- post-only, earns maker fees)
- `triple_barrier_config.take_profit_order_type`: Same enum. 3=LIMIT_MAKER recommended.
- `coerce_tp_to_step`: When true, TP = max(grid_step, take_profit). Prevents closing before next level.

**Risk Management -- limit_price + keep_position (NO stop_loss):**
- `limit_price` is the safety boundary -- when price crosses it, the grid stops completely.
- `keep_position=false`: closes the accumulated position on stop -> acts as a stop-loss exit.
- `keep_position=true`: holds the accumulated position on stop -> wait for recovery.
- There is NO `stop_loss` parameter. Never suggest it. `limit_price` + `keep_position` is the only risk mechanism for grids.

---

#### Smart Grid Positioning

When the user asks for a grid without specifying exact prices, use one of the strategies below to calculate sensible defaults. **Always present the calculated prices to the user for confirmation before creating the executor.**

##### Strategy 1: Percentage-Based (Default)

Fetch current price P first using `get_market_data(data_type="prices", ...)`.

**LONG grid (side=1, bullish) -- expects price to rise:**
- `end_price`   = P x 1.03   (3% above current -- profit room)
- `start_price` = P x 0.99   (1% below current -- entry zone)
- `limit_price` = start_price x 0.995  (0.5% below start -- safety stop)
- Range: ~4% total, 3:1 ratio favoring upside

**SHORT grid (side=2, bearish) -- expects price to drop:**
- `start_price` = P x 0.97   (3% below current -- profit room)
- `end_price`   = P x 1.01   (1% above current -- entry zone)
- `limit_price` = end_price x 1.005  (0.5% above end -- safety stop)
- Range: ~4% total, 3:1 ratio favoring downside

##### Strategy 2: Historical Range

Use when the user mentions "recent range", "volatility", or wants a data-driven grid.

1. Fetch candles: `get_market_data(data_type="candles", interval="1h", days=3, ...)`
2. Calculate high and low from the candle data
3. **LONG grid:** start_price = low, end_price = high, limit_price = low x 0.995
4. **SHORT grid:** start_price = low, end_price = high, limit_price = high x 1.005

##### Important

- Always round prices to the appropriate precision for the trading pair
- Always present all calculated values to the user before creating the executor
- If the user provides some prices but not others, calculate only the missing ones

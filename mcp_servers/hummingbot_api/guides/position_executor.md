### Position Executor
Takes directional positions with defined entry, stop-loss, and take-profit levels.

**Use when:**
- You have a clear directional view (bullish/bearish)
- You want automated stop-loss and take-profit management
- You want to define risk/reward ratios upfront

**Avoid when:**
- You want to provide liquidity (use Market Making instead)
- You need complex multi-leg strategies

#### How It Works

- Opens a directional position (long/short) with optional limit entry price
- Manages exit via triple barrier config: stop-loss, take-profit, time limit, trailing stop
- Amount is in **base currency** (NOT quote). e.g., for BTC-USDT, amount=0.01 means 0.01 BTC

**CRITICAL:**
- `amount` is in **base currency** — NOT `total_amount_quote`. To convert from USD: `amount = usd_value / entry_price`
- Always fetch the schema first via progressive disclosure (`manage_executors(executor_type='position_executor')`) before creating

#### Parameter Reference

**Core:**
- `connector_name`: Exchange connector (e.g., 'binance_perpetual')
- `trading_pair`: Trading pair (e.g., 'BTC-USDT')
- `side`: 1 (BUY/LONG) or 2 (SELL/SHORT)
- `amount`: Position size in **base currency** (e.g., 0.01 BTC). To convert from USD: `amount = usd / price`
- `entry_price`: Limit entry price (optional — omit for market entry)
- `leverage`: Leverage multiplier (default: 1)

**Triple Barrier Config (`triple_barrier_config`):**
- `stop_loss`: Stop-loss as decimal (e.g., 0.02 = 2%)
- `take_profit`: Take-profit as decimal (e.g., 0.03 = 3%)
- `time_limit`: Max position duration in seconds (optional)
- `trailing_stop.activation_price`: Price delta to activate trailing stop
- `trailing_stop.trailing_delta`: Trailing distance
- `open_order_type`: 1=MARKET, 2=LIMIT, 3=LIMIT_MAKER
- `take_profit_order_type`: same enum (default: MARKET)
- `stop_loss_order_type`: same enum (default: MARKET)
- `time_limit_order_type`: same enum (default: MARKET)

**Optional:**
- `activation_bounds`: Price bounds for activation (optional)
- `level_id`: Optional identifier tag

#### Example

Long 0.01 BTC with 2% SL and 3% TP:
```json
{
  "connector_name": "binance_perpetual",
  "trading_pair": "BTC-USDT",
  "side": 1,
  "amount": 0.01,
  "leverage": 5,
  "triple_barrier_config": {
    "stop_loss": 0.02,
    "take_profit": 0.03,
    "open_order_type": 2
  }
}
```

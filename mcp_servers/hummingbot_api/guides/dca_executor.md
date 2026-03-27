### DCA Executor
Dollar-cost averages into positions over time with scheduled purchases.

**Use when:**
- You want to accumulate a position gradually
- You want to reduce timing risk
- You're building a long-term position

**Avoid when:**
- You need immediate full position entry
- You want active trading with quick exits

#### How It Works

- Places multiple limit orders at decreasing (BUY) or increasing (SELL) prices to average into a position
- `amounts_quote` and `prices` are parallel lists — each index is one DCA level
- Amount is in **quote currency** (e.g., USDT). Each level can have a different amount
- Exit managed via stop-loss, take-profit, trailing stop, or time limit

**CRITICAL:**
- Uses `amounts_quote` (list, quote currency) — NOT `amount` or `total_amount_quote`
- `prices` list must be the same length as `amounts_quote`
- Always fetch the schema first via progressive disclosure (`manage_executors(executor_type='dca_executor')`) before creating

#### Parameter Reference

**Core:**
- `connector_name`: Exchange connector (e.g., 'binance_perpetual')
- `trading_pair`: Trading pair (e.g., 'BTC-USDT')
- `side`: 1 (BUY) or 2 (SELL)
- `amounts_quote`: List of order sizes in **quote currency** (e.g., [100, 100, 150])
- `prices`: List of price levels for each DCA order (e.g., [50000, 48000, 46000])
- `leverage`: Leverage multiplier (default: 1)

**Exit Config:**
- `take_profit`: TP as decimal (e.g., 0.03 = 3%) (optional)
- `stop_loss`: SL as decimal (e.g., 0.05 = 5%) (optional)
- `trailing_stop`: TrailingStop config (optional)
- `time_limit`: Max duration in seconds (optional)

**Execution:**
- `mode`: "MAKER" (limit orders, default) or "TAKER" (market orders)
- `activation_bounds`: Price bounds for activation (optional)
- `level_id`: Optional identifier tag

#### Example

DCA buy into BTC with 3 levels, 3% TP and 5% SL:
```json
{
  "connector_name": "binance_perpetual",
  "trading_pair": "BTC-USDT",
  "side": 1,
  "amounts_quote": [100, 100, 150],
  "prices": [50000, 48000, 46000],
  "leverage": 1,
  "take_profit": 0.03,
  "stop_loss": 0.05,
  "mode": "MAKER"
}
```

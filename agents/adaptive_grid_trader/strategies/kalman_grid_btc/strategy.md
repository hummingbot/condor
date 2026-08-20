---
name: kalman_grid_btc
description: ''
agent_key: null
skills: []
default_config: {}
default_trading_context: ''
created_by: 456181693
created_at: '2026-08-13T03:14:12.657657+00:00'
---


# Kalman Grid Strategy — BTC-USDT / bitget_perpetual

## Envelope (fixed — never ask per tick)
- connector: bitget_perpetual
- pair: BTC-USDT
- budget: 100 USDT | reserve: 10% | trade_budget: $90
- max_leverage: 5x | max_loss_pct: 10% ($10 max per grid)
- min_order_size: 5 USDT
- allowed_profiles: LONG, SHORT only (no TWO_SIDED)

## Every tick

### 1 — Kalman signal
```
manage_routines(action="run", name="kalman_regime_check", config={
  "connector_name": "bitget_perpetual",
  "trading_pair": "BTC-USDT",
  "candle_interval": "1m",
  "lookback_candles": 60
})
```
Extract from result: regime, profile, snr, grid_D, grid_start, grid_end, limit_price.

### 2 — Executor state
```
manage_executors(action="list_executors")
```
Find any active grid_executor for BTC-USDT on bitget_perpetual. Note: executor_id, net_pnl_quote, filled_amount_quote, created_timestamp.

### 3 — Decide (first match wins)

**No executor running:**
- profile LONG or SHORT → DEPLOY
- else → HOLD

**Executor running:**
1. fills_amount_quote unchanged 10+ ticks → RETUNE (teardown + redeploy same direction)
2. net_pnl_quote ≥ $2.00 → PROFIT_TAKE (teardown + redeploy if signal still directional)
3. current grid_D differs >20% from deployed D → RETUNE (teardown + redeploy updated range)
4. profile flipped AND executor age ≥ 180 min → FLIP (teardown + deploy new direction)
5. else → KEEP

### 4 — Act

**DEPLOY grid:**
```
manage_executors(action="create_executor", executor_config={
  "type": "grid_executor",
  "connector_name": "bitget_perpetual",
  "trading_pair": "BTC-USDT",
  "start_price": <grid_start from Kalman>,
  "end_price": <grid_end from Kalman>,
  "limit_price": <limit from Kalman>,
  "total_amount_quote": 90,
  "n_levels": 9,
  "min_spread_between_orders": 0.001,
  "min_order_amount_quote": 5,
  "leverage": 5,
  "side": "BUY" if LONG else "SELL",
  "time_limit": 86400,
  "keep_position": false,
  "triple_barrier_config": {
    "take_profit": 0.003,
    "stop_loss": 0.02,
    "stop_loss_order_type": "MARKET"
  }
})
```

**STOP grid:**
```
manage_executors(action="stop_executor", executor_id=<id>)
```
Always verify flat (net position = 0) before redeploying.

### 5 — Journal every tick
```
trading_agent_journal_write(entries=[
  {"key": "regime",   "value": <regime>},
  {"key": "snr",      "value": <snr>},
  {"key": "grid_D",   "value": <grid_D>},
  {"key": "profile",  "value": <profile>},
  {"key": "action",   "value": <action taken>},
  {"key": "pnl",      "value": <net_pnl_quote or 0>},
  {"key": "fills",    "value": <filled_amount_quote or 0>}
])
```

## Safety — abort and alert if:
- Available balance < $90 before deploy
- Worst-case loss at limit_price > $10
- Liquidation price inside limit_price at 5x
- Position cannot be verified flat after teardown


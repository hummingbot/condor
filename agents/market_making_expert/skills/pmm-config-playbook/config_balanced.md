# Balanced pmm_mister Profile

**Use when:** normal ranging market (ADX < 25, moderate BBW). This is the
**default** steady-state profile — moderate spreads, allocation and cooldowns.
Good when no regime signal is strong enough to justify aggressive or
conservative.

```json
{
  "controller_type": "generic",
  "controller_name": "pmm_mister",
  "connector_name": "binance_perpetual",
  "trading_pair": "JTO-USDT",
  "total_amount_quote": 500,
  "portfolio_allocation": 0.15,
  "target_base_pct": 0.5,
  "min_base_pct": 0.35,
  "max_base_pct": 0.65,
  "buy_spreads": "0.0012,0.0025",
  "sell_spreads": "0.0012,0.0025",
  "buy_amounts_pct": "1,1",
  "sell_amounts_pct": "1,1",
  "executor_refresh_time": 30,
  "buy_cooldown_time": 60,
  "sell_cooldown_time": 60,
  "buy_position_effectivization_time": 120,
  "sell_position_effectivization_time": 120,
  "price_distance_tolerance": 0.0005,
  "refresh_tolerance": 0.0005,
  "tolerance_scaling": 1.2,
  "open_order_type": 3,
  "take_profit": 0.001,
  "take_profit_order_type": 3,
  "leverage": 8,
  "position_mode": "ONEWAY",
  "position_side": "BUY",
  "max_active_executors_by_level": 3,
  "tick_mode": false,
  "min_skew": 1.5,
  "global_tp_enabled": false,
  "global_sl_enabled": true,
  "global_stop_loss": 0.05,
  "global_sl_activation_from": "target_base",
  "global_pnl_reference": "position"
}
```

**Parameter notes**
- `buy/sell_position_effectivization_time` (120s): The per-fill LIMIT_MAKER TP
  order stays on the book for 2 minutes after each fill. If the market moves
  enough to hit the TP in that window, the position closes with a per-fill profit.
  If not, the position transitions to hold mode after 120s and is managed by
  the global SL layer. Balanced between giving the TP time to fill and not
  leaving stale positions open indefinitely.
- `price_distance_tolerance` / `refresh_tolerance` (0.0005): Controller defaults.
  Balanced refresh cadence — not too aggressive, not too slow.
- `tolerance_scaling` (1.2): Default multiplier. Tolerance widens moderately
  as executors accumulate, preventing cancel-loops in ranging markets.
- `open_order_type` / `take_profit_order_type` (3 = LIMIT_MAKER): Post-only
  orders. Change to 2 (LIMIT) only if the exchange rejects makers.
- `tick_mode` (false): Keep false for continuous market making.
- `min_skew` (1.5): Enforces a minimum 1.5× spread multiplier on the heavy side
  when inventory drifts. Mild protection against runaway accumulation.

**Tuning notes**
- Start here when unsure, then shift toward aggressive (calm) or conservative
  (vol/trend) as the regime clarifies.
- For a mild trend, make spreads asymmetric: widen the side you don't want to
  trade into (e.g. wider `sell_spreads` in an uptrend).
- Increase `min_skew` to 2.0+ if inventory keeps drifting despite the band.

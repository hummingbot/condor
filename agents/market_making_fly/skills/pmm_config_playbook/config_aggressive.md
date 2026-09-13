# Aggressive pmm_mister Profile

**Use when:** quiet / low-volatility ranging market (ADX < 18, BBW < ~3%). You
want maximum fill rate and volume capture. **Highest** inventory + PnL risk —
do NOT use in trending or volatile regimes.

Tight spreads, fast order refresh, short cooldowns and effectivization times,
wide inventory tolerance, higher allocation.

```json
{
  "controller_type": "generic",
  "controller_name": "pmm_mister",
  "connector_name": "binance_perpetual",
  "trading_pair": "JTO-USDT",
  "total_amount_quote": 500,
  "portfolio_allocation": 0.25,
  "target_base_pct": 0.5,
  "min_base_pct": 0.3,
  "max_base_pct": 0.7,
  "buy_spreads": "0.0008,0.0015",
  "sell_spreads": "0.0008,0.0015",
  "buy_amounts_pct": "1,1",
  "sell_amounts_pct": "1,1",
  "executor_refresh_time": 20,
  "buy_cooldown_time": 30,
  "sell_cooldown_time": 30,
  "buy_position_effectivization_time": 60,
  "sell_position_effectivization_time": 60,
  "price_distance_tolerance": 0.0005,
  "refresh_tolerance": 0.0003,
  "tolerance_scaling": 1.1,
  "open_order_type": 3,
  "take_profit": 0.0008,
  "take_profit_order_type": 3,
  "leverage": 10,
  "position_mode": "ONEWAY",
  "position_side": "BUY",
  "max_active_executors_by_level": 4,
  "tick_mode": false,
  "min_skew": 1.0,
  "global_tp_enabled": false,
  "global_sl_enabled": true,
  "global_stop_loss": 0.05,
  "global_sl_activation_from": "target_base",
  "global_pnl_reference": "position"
}
```

**Parameter notes**
- `buy/sell_position_effectivization_time` (60s): The per-fill LIMIT_MAKER TP
  order stays on the book for only 60s after each fill. In a quiet, low-drift
  market the price barely moves, so the TP is unlikely to fill in that window —
  positions quickly transition to hold mode. This is intentional: in calm
  conditions we let fills accumulate into a held position rather than chasing
  individual TPs. global_tp is disabled here, so held positions grow until the
  global SL triggers.
- `price_distance_tolerance` (0.0005): Minimum gap required between stacked
  orders at the same level. Keeps orders spread out to avoid clustering.
- `refresh_tolerance` (0.0003): Tighter than default — triggers a quote
  refresh/replacement with smaller mid-price moves. More responsive in calm
  markets where small moves matter.
- `tolerance_scaling` (1.1): Low multiplier — tolerance widens slowly as
  executors accumulate. Stay close to mid.
- `open_order_type` / `take_profit_order_type` (3 = LIMIT_MAKER): Post-only.
  Never takes liquidity. Change to 2 (LIMIT) only if the exchange rejects makers.
- `tick_mode` (false): Keep false for continuous market making.
- `min_skew` (1.0): No minimum skew enforced — spreads stay symmetric when
  inventory is balanced.

**Tuning notes**
- If fills are too one-sided, narrow the inventory band (raise `min_base_pct` /
  lower `max_base_pct`) so skew kicks in sooner.
- If the market starts trending, switch to **balanced** or **conservative** —
  tight two-sided spreads bleed into a trend.
- `global_sl_enabled` stays on even here: 5% hard stop is the floor.

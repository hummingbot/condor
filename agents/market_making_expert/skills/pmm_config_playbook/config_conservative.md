# Conservative pmm_mister Profile

**Use when:** volatile, trending, or uncertain market (ATR expanding, BBW > ~6%,
ADX > 25, volume surge). Capital preservation first — wide spreads, slow
refresh, tight inventory bands, long effectivization times, low
allocation/leverage, and **both** global TP and SL protections enabled.

```json
{
  "controller_type": "generic",
  "controller_name": "pmm_mister",
  "connector_name": "binance_perpetual",
  "trading_pair": "JTO-USDT",
  "total_amount_quote": 500,
  "portfolio_allocation": 0.1,
  "target_base_pct": 0.5,
  "min_base_pct": 0.4,
  "max_base_pct": 0.6,
  "buy_spreads": "0.003,0.006",
  "sell_spreads": "0.003,0.006",
  "buy_amounts_pct": "1,1",
  "sell_amounts_pct": "1,1",
  "executor_refresh_time": 60,
  "buy_cooldown_time": 120,
  "sell_cooldown_time": 120,
  "buy_position_effectivization_time": 300,
  "sell_position_effectivization_time": 300,
  "price_distance_tolerance": 0.001,
  "refresh_tolerance": 0.001,
  "tolerance_scaling": 1.3,
  "open_order_type": 3,
  "take_profit": 0.0015,
  "take_profit_order_type": 3,
  "leverage": 5,
  "position_mode": "ONEWAY",
  "position_side": "BUY",
  "max_active_executors_by_level": 2,
  "tick_mode": false,
  "min_skew": 2.0,
  "position_profit_protection": true,
  "global_tp_enabled": true,
  "global_take_profit": 0.03,
  "global_tp_activation_from": "min_base",
  "global_sl_enabled": true,
  "global_stop_loss": 0.04,
  "global_sl_activation_from": "target_base",
  "global_pnl_reference": "position"
}
```

**Parameter notes**
- `buy/sell_position_effectivization_time` (300s): The per-fill LIMIT_MAKER TP
  order stays on the book for 5 minutes after each fill. In volatile markets
  with frequent wicks, this gives the TP more time to be hit — individual fills
  get closed profitably before the position transitions to hold. If the TP is
  not hit in 300s, the position enters hold mode and both global TP (3%) and
  global SL (4%) take over risk management.
- `price_distance_tolerance` / `refresh_tolerance` (0.001): Wider than default.
  Avoids over-refreshing when price is moving constantly — reduces
  cancel/replace churn and fees in volatile conditions.
- `tolerance_scaling` (1.3): Higher multiplier — tolerance grows faster per
  executor so the controller doesn't thrash in choppy conditions.
- `open_order_type` / `take_profit_order_type` (3 = LIMIT_MAKER): Post-only.
  Never takes liquidity — critical in volatile markets to avoid adverse fills.
- `tick_mode` (false): Keep false. Tick mode reduces update frequency but adds
  complexity not needed here.
- `min_skew` (2.0): Forces at least 2× spread multiplier on the accumulating
  side when inventory drifts. Aggressively discourages one-sided fills in
  trending conditions.
- `position_profit_protection` (true): Blocks inventory reductions at
  unfavorable prices — won't dump positions into a spike.
- `global_tp_enabled` / `global_take_profit` (3%): Portfolio-level TP. When
  the held position's PnL crosses +3%, the controller begins winding down.
- `global_tp_activation_from` ("min_base"): TP activates when base inventory
  is at or below `min_base_pct` — when the portfolio is light on base and
  already showing profit.
- `global_sl_activation_from` ("target_base"): SL activates when base inventory
  is at or above `target_base_pct` — protecting against heavy accumulation
  losing value.
- `global_pnl_reference` ("position"): PnL is measured against the current
  open position value (unrealized). Use "portfolio" to measure against total
  portfolio value instead.

**Tuning notes**
- In extreme volatility, drop `portfolio_allocation` further or pause the bot
  entirely rather than widening spreads indefinitely.
- `position_profit_protection` blocks reductions at unfavorable prices — keep
  it on so the controller won't dump inventory into a spike.
- Tighter `global_stop_loss` (4%) than the other profiles: cut losers faster
  when the regime is hostile.
- If wicks keep triggering the SL, increase `global_stop_loss` slightly or
  widen `buy/sell_spreads` so entry prices have more buffer.

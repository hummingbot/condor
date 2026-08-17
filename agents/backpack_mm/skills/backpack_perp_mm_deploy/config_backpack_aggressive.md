# Backpack MM Aggressive Config Template

Adapted from the MM expert's aggressive profile, tuned for Backpack MM Rewards
Program compliance. All orders are LIMIT_MAKER (post-only) for maker credit.

## Config (substitute connector/pair/amount per market)

```json
{
  "controller_type": "generic",
  "controller_name": "pmm_mister",
  "connector_name": "backpack_perpetual",
  "trading_pair": "<PAIR>-USDC",
  "total_amount_quote": 20000,
  "portfolio_allocation": 0.25,
  "target_base_pct": 0.5,
  "min_base_pct": 0.3,
  "max_base_pct": 0.7,
  "buy_spreads": "0.0003,0.0015",
  "sell_spreads": "0.0003,0.0015",
  "buy_amounts_pct": "2,1",
  "sell_amounts_pct": "2,1",
  "executor_refresh_time": 20,
  "buy_cooldown_time": 30,
  "sell_cooldown_time": 30,
  "buy_position_effectivization_time": 300,
  "sell_position_effectivization_time": 300,
  "price_distance_tolerance": 0.0005,
  "refresh_tolerance": 0.0003,
  "tolerance_scaling": 1.1,
  "open_order_type": 3,
  "take_profit": 0.0006,
  "take_profit_order_type": 3,
  "leverage": 2,
  "position_mode": "ONEWAY",
  "position_side": "BUY",
  "max_active_executors_by_level": 2,
  "tick_mode": false,
  "min_skew": 1.0,
  "global_tp_enabled": false,
  "global_sl_enabled": true,
  "global_stop_loss": 0.05,
  "global_sl_activation_from": "target_base",
  "global_pnl_reference": "position"
}
```

## Position mode — universal default

**Always use `"position_mode": "ONEWAY"`** for pmm_mister configs on any connector.
This is the universal default — not just a Backpack restriction. HEDGE should only
be used when the user explicitly requests it.

On Backpack specifically, HEDGE is not supported at all and will error on startup.
On other connectors (Hyperliquid, Binance, etc.), ONEWAY is still the default unless
the user says otherwise.

## Parameter rationale

**Order sizing** (meets $2K minimum):
- Level 1: $20K × 0.25 × (2/3) = $3,333 per order
- Level 2: $20K × 0.25 × (1/3) = $1,667 per order (depth)
- Both sides quoting = continuous presence

**Spreads** (within 100bps program limit):
- L1: 3bps — very tight, maximizes fill rate and volume generation
- L2: 15bps — passive depth, helps Liquidity Score

**Effectivization** (300s):
- After a fill, the LIMIT_MAKER TP order stays on book for 5 minutes
- On mid-tier perps, price needs time to cycle back for TP fill
- Complete fill→TP cycle = 2× notional in maker volume
- 60s was too short (most positions expired to hold); 300s catches ~50%+ of TPs

**Take profit** (6bps):
- With MM5 fees (~0.02% maker), round-trip ~0.04%
- 6bps TP gives 1.5× fee coverage — tight but viable
- If Backpack fees are higher than expected, bump to 8bps

**Leverage** (2x):
- Mid-tier perps (ZEC, PUMP, WLD) can gap 5-10%
- At 2x: 5% move = 10% drawdown → survivable with 5% global SL
- At 5x: 5% move = 25% drawdown → dangerous

**Cooldown** (30s):
- Prevents stacking fills at similar prices on thin markets
- 15s was too aggressive for $500-900K/day volume markets

## Adjustments per market

- **PAXG**: Gold-backed, low vol → can keep 3bps, reduce cooldown to 20s
- **ZEC/WLD**: Moderate vol → use template as-is
- **PUMP**: Higher vol, can gap → monitor closely with 3bps, widen to 5bps if adverse fills spike
- **Leverage**: Always verify max leverage per market (1/imfFunction.base from API)

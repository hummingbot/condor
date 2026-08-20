---
name: sndk_gate_hft_mm
description: ''
agent_key: null
skills: []
default_config: {}
default_trading_context: ''
created_by: 456181693
created_at: '2026-08-18T03:10:16.398446+00:00'
---

## SNDK-USDT / gate_io_perpetual — HFT Maker Rebate Strategy

### Pre-flight
Before deploying or resuming this strategy, always run the `gate_exchange_marketanalysis` skill:
- manage_skill(action="read", name="gate_exchange_marketanalysis")
- Apply its framework to SNDK-USDT: fetch 14d candles, order book snapshot, funding rate
- Confirm regime output and adjust spreads if needed before starting the bot

### Overview
Asymmetric PMM strategy designed for Gate.io's pro maker rebate program on SNDK-USDT perpetual.
Core thesis: earn rebates via tight buy-side fills; protect against adverse selection on the sell side
with 2× wider spreads given SNDK's volatile uptrend regime (+52% in 14 days as of deploy).

### Controller Config
- config_name: sndk_gate_hft_mm
- connector: gate_io_perpetual
- trading_pair: SNDK-USDT
- total_amount_quote: 46
- leverage: 5
- position_mode: HEDGE

### Spread Settings (asymmetric — volatile_trending regime)
- buy_spreads: "0.0002,0.0008,0.0020"   # 0.02% / 0.08% / 0.20%
- sell_spreads: "0.0004,0.0016,0.0040"  # 0.04% / 0.16% / 0.40% (2× wider)
- buy_amounts_pct: "4,2,1"
- sell_amounts_pct: "2,2,1"
- open_order_type: LIMIT_MAKER           # post-only, maker rebate eligible

### Execution Timing (HFT profile)
- executor_refresh_time: 5
- buy_cooldown_time: 8
- sell_cooldown_time: 12
- buy_position_effectivization_time: 45
- sell_position_effectivization_time: 45
- refresh_tolerance: 0.0002
- price_distance_tolerance: 0.0002
- tolerance_scaling: 1.3

### Take Profit
- take_profit: 0.0003   # 0.03% — equals Gate round-trip maker cost; profitable only with rebate
- take_profit_order_type: LIMIT_MAKER

### Inventory Management
- target_base_pct: 0.55   # slight long lean with the trend
- min_base_pct: 0.30
- max_base_pct: 0.75
- min_skew: 1.8            # widens sell quotes when inventory is heavy

### Global Risk Controls
- global_stop_loss: 0.025         # 2.5% on position
- global_take_profit: 0.020       # 2.0% on position
- max_controller_drawdown_quote: 10

### Circuit Breakers (manual)
Flip manual_kill_switch=true if any of:
1. 5-min candle body > 1.5%
2. Inventory > 70% base
3. Funding rate > +0.05%
4. Daily drawdown > 5%

### P&L Logic
- Gate VIP maker rebate: ~−0.015% (verify your tier)
- Round-trip from fees: +0.03%
- TP income: +0.03%
- Gross per round-trip: ~+0.06%
- WARNING: take_profit=0.0003 is breakeven without the rebate — confirm negative maker fee is active
- Circuit breaker: if net PnL per fill is negative after first 20 fills → pause and check tier

### Regime Adaptation (from gate_exchange_marketanalysis output)
- quiet_ranging → switch to symmetric spreads (0.02/0.08/0.20% both sides), skew=1.0
- volatile_ranging → mild asymmetry (buys 0.03/0.10/0.25%, sells 0.05/0.15/0.35%), skew=1.5
- volatile_trending → current config (buys 0.02/0.08/0.20%, sells 0.04/0.16/0.40%), skew=1.8


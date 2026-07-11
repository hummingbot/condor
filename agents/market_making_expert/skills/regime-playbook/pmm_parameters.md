# Regime → pmm_mister parameters

(read with the regime from market_analyzer; adapt to [CURRENT CONFIG] capital)

## Quiet (ADX < 18, BBW < 3%)
- Tight spreads: buy_spreads="0.0008,0.0015", sell_spreads="0.0008,0.0015"
- Fast refresh: executor_refresh_time=20
- Short cooldowns: buy/sell_cooldown_time=30
- Normal inventory: target_base_pct=0.5, min=0.3, max=0.7

## Ranging (ADX < 25, moderate BBW)
- Moderate spreads: buy_spreads="0.0012,0.0025", sell_spreads="0.0012,0.0025"
- Standard refresh: executor_refresh_time=30
- Standard cooldowns: 60s
- Normal inventory

## Trending Up (ADX > 25, price > SMA, positive momentum)
- Asymmetric: buy_spreads="0.001,0.002", sell_spreads="0.002,0.004"
- Widen sell side to avoid selling into trend
- Consider position_side="BUY" to accumulate longs
- Enable position_profit_protection=true

## Trending Down (ADX > 25, price < SMA, negative momentum)
- Asymmetric: buy_spreads="0.002,0.004", sell_spreads="0.001,0.002"
- Widen buy side to avoid catching falling knife
- Consider position_side="SELL" for shorts
- Enable position_profit_protection=true

## Volatile (ATR expanding, BBW > 6%, volume surge)
- Wide spreads: buy_spreads="0.003,0.006", sell_spreads="0.003,0.006"
- Slow refresh: executor_refresh_time=60
- Long cooldowns: 120s
- Tight inventory: min=0.35, max=0.65
- Enable global_sl_enabled=true
- Consider pausing if extreme

Take-profit floor in every regime: TP must clear round-trip maker fees —
see the shared `executor-mechanics` skill's fee table (perp ≥ 0.0008,
spot ≥ 0.0020).

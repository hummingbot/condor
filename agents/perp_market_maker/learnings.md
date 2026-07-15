# Learnings

## Market Observations
- [2026-07-15 11:16] [net_position_market_maker] ASK within 1 spread of mid (+bullish order book imbalance >10%) in trending_up regime filled next tick as mid crossed it — near-market ASKs fill quickly in this regime.
- [2026-07-15 12:02] [net_position_market_maker] In volatile regime (base×4, 0.32% spread), both BID and ASK can fill within 6 seconds of simultaneous placement — rapid two-way price sweeps clear both sides of a wide spread in quick succession.

## Execution Notes
- [2026-07-15 05:47] [net_position_market_maker] Stopping an executor with keep_position=True leaves the resting order on Hyperliquid but removes native tracking — always use keep_position=False to cancel the order on venue when requoting.
- [2026-07-15 06:49] [net_position_market_maker] Orphaned resting orders from prior sessions (keep_position=True) appear in market_analyzer inventory but have no Condor executor_id — cannot be stopped via manage_executors; must be cancelled manually or avoided by always using keep_position=False.
- [2026-07-15 09:16] [net_position_market_maker] manage_executors stop requires full executor ID (order_perp_TIMESTAMP_suffix format), not just the 6-char short suffix — short suffix alone returns 404.

## Retired Insights

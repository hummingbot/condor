---
name: stop_vs_widen
description: How to decide whether to stop a losing executor or widen/adjust it.
when_to_use: When a running executor is underwater or its band is being walked, and the user asks what to do.
created: 2026-06-18
source: builtin
---

When an executor is losing, the instinct to "widen and average down" is usually
wrong. Default to **cutting** unless the range thesis is still intact. Decide on
evidence, not hope.

## Steps

1. **Get the facts** — `manage_executors(action="status"/"list")` for the executor's
   current PnL, filled levels, and remaining capacity; `get_market_data` for the live
   price vs the executor's band.
2. **Re-test the original thesis.** Is the market still ranging within the band, or has
   it **broken out** (price walking one edge with momentum)?
   - **Breakout / trend against the position** → the range thesis is dead. **Stop** the
     executor. Widening only buys more of a losing direction.
   - **Still ranging, price mid-band, temporary drawdown** → holding is fine; do nothing
     or only minor step tuning.
3. **Only widen when** the range is genuinely intact but slightly larger than first
   sized AND inventory/balance can support the extra levels (`get_portfolio_overview`).
   Widening is a range-resize, never a trend-fighting tool.
4. **Respect risk limits.** If adjusting would breach the user's inventory/loss limit,
   stop instead — surface the limit as the reason.
5. **Recommend explicitly**: stop / hold / widen, with the trigger you saw (e.g. "lower
   bound broken on rising volume → stop"). Executing `stop`/`create` prompts the user
   to confirm; if rejected, give the manual steps.

## Heuristic

Stop is the safe default. Widening requires a *positive* range-intact signal, not just
the absence of a breakout.

---
name: validate_before_grid
description: Checklist to validate a market before deploying a grid executor.
when_to_use: Before recommending or creating a grid/grid_strike executor on any pair.
created: 2026-06-18
source: builtin
---

Run this before proposing or deploying a grid. A grid only earns in a *range*; in a
trend it bleeds inventory. Validate, don't assume.

## Steps

1. **Regime check** — pull recent candles (`get_market_data`, ~1h/4h). Confirm the
   price is **range-bound**, not trending. If the last N closes make higher-highs or
   lower-lows persistently, a grid is the wrong tool — say so and stop.
2. **Define the band from structure**, not round numbers. Set the grid's lower/upper
   bounds at validated support/resistance. Price should currently sit inside the band,
   not at an edge about to break out.
3. **Size the band to volatility.** Wider band in high volatility (fewer, fatter
   fills), tighter in calm ranges (more fills). Sanity-check the step vs recent ATR so
   levels actually get hit but aren't noise.
4. **Inventory & balance** — `get_portfolio_overview`. Confirm available balance covers
   the full ladder (levels × order amount) with margin to spare. Never stack a new grid
   on top of correlated inventory that would breach the user's risk limit.
5. **Perp funding** — if perpetual, check funding (`get_market_data`); holding grid
   inventory against adverse funding erodes PnL.
6. **Decide**: deploy / adjust band / decline. Lead with the verdict, then the concrete
   params (bounds, levels, amount, step) and the one-line rationale.

## Do not

- Don't grid into a clear trend.
- Don't center the band on the current price by default — center on the range.
- Don't size to the full balance; leave headroom for the position to move.

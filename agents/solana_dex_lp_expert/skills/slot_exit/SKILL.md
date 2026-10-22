---
name: slot_exit
description: Decide hold vs exit for an open LP slot (TP/SL/range-abandoned), close
  it swapping back to quote, and rotate the freed capital.
when_to_use: When deciding whether an open LP slot should be held or exited, and how
  to close it and rotate the freed capital.
created: '2026-07-20T23:27:07Z'
source: agent:solana_dex_lp_expert
---

# Slot Exit — TP/SL + rotation

## Exit triggers (evaluate every tick, per open slot)
Exit when ANY is true:
- **Take profit:** slot `net_pnl_pct ≥ take_profit_pct` (default 20).
- **Stop loss:** slot `net_pnl_pct ≤ −stop_loss_pct` (default 20).
- **Idle out-of-range:** `state == OUT_OF_RANGE` **and** `out_of_range_seconds ≥
  out_of_range_max_sec` (default 1800). An out-of-range position earns zero LP
  fees — the whole point of a slot — so cut it and re-scan even if PnL has not
  hit ±`stop_loss_pct`.
  *Exception, not a requirement:* skip the exit if OHLCV shows price decisively
  trending back INTO the range. **One OHLCV check per tick**, and only for a slot
  near the threshold.

> This trigger used to read "OUT_OF_RANGE for a sustained period AND OHLCV trend
> continues away" — vaguer and **stricter** than the loop, which measures the
> seconds. Requiring the OHLCV confirmation held dead slots open whenever the
> trend was ambiguous, which is the case a timer exists to settle (LP-9).

## What may exit without you
Every slot is opened with `lower_limit_price` / `upper_limit_price` set
`autonomous_exit_margin_pct` outside its range (LP-7). Those fire in the executor
with no tick involved, so a slot can be gone before you evaluate it — including
**inside** the `out_of_range_max_sec` window if price moved straight through a
limit. A slot that has vanished since last tick is not necessarily an error:
read its close reason before treating it as one.

Measure PnL in `quote_asset` terms (net of fees earned, IL, rent, tx). Use executor `net_pnl_pct` / `custom_info` when available; otherwise compute from current vs initial value.

## How to exit
`stop_executor(executor_id=<id>, keep_position=false)`
- `keep_position=false` removes on-chain liquidity, runs the close-out swap back to `quote_asset`, and refunds position rent.
- Confirm the executor reaches COMPLETE and rent is refunded (`position_rent_refunded > 0`).

## Rotate
- A freed slot is re-filled next tick from a fresh `lp_scanner` run (see
  `pool_ranking`) — **at most one slot per tick**, within the loop's ≤8 tool calls.
- Do NOT immediately re-enter the pool you just exited (esp. a stop-loss) unless it clearly re-ranks on top — avoid churn/fee bleed.
- Journal each exit: pool, reason (TP/SL/abandoned), realized PnL, fees earned, duration.

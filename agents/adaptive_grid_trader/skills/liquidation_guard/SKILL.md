---
name: liquidation_guard
description: 'Pre-deploy checklist: order size validation + liquidation price guard
  for grid executors on perpetual futures'
when_to_use: Before every grid_executor deployment on perpetual futures. Run after
  hourly_mtf_check produces a recommendation and before calling manage_executors(action='create').
created: '2026-07-30T14:22:18Z'
source: agent:adaptive_grid_trader
---

## Pre-Deploy Gate — run every step, stop on first FAIL

### Step 0 — Order Size Validation

Given: budget, range (start_price → end_price), spacing, min_order_size, exchange_minimum

1. Compute levels: `levels = (end_price - start_price) / (spacing × mid_price)`
2. Compute per-level size: `per_level = total_amount_quote / levels`
3. Check: `per_level ≥ max(min_order_size, exchange_minimum)`
4. If FAIL:
   - Reduce levels (widen spacing) until per_level passes
   - If no valid configuration exists → **FAIL: budget too small for this range**

### Step 1 — Worst-Case Position Size

Assume every grid level fills (worst case for LONG = price at start_price, worst case for SHORT = price at end_price).

- For each level, compute: `level_base = per_level_quote / level_price`
- `total_base = Σ level_base` across all levels
- `avg_entry = total_amount_quote / total_base`

### Step 2 — Worst-Case Liquidation Price

Using isolated-margin formula:

- **LONG**: `liq_price = avg_entry × (1 - 1/leverage + maintenance_margin_rate)`
- **SHORT**: `liq_price = avg_entry × (1 + 1/leverage - maintenance_margin_rate)`

Where `maintenance_margin_rate` depends on the exchange's position tier for the notional `total_base × limit_price`. Typical values:
- Tier 1 (small positions): 0.4% (0.004)
- Tier 2 (medium): 0.5% (0.005)
- Tier 3 (large): 1-2% (0.01-0.02)

When in doubt, use the higher tier — it's conservative.

### Step 3 — The Check

- **LONG grid**: `liq_price` must be **below** `limit_price`
  - PASS if `liq_price < limit_price`
  - FAIL if `liq_price ≥ limit_price`
- **SHORT grid**: `liq_price` must be **above** `limit_price`
  - PASS if `liq_price > limit_price`
  - FAIL if `liq_price ≤ limit_price`

### Step 4 — If FAIL, try remediation (in order)

1. Reduce leverage by 1 step → recompute from Step 2
2. If leverage is already at minimum useful level → narrow the range (fewer levels, wider spacing) → recompute from Step 1
3. If still FAIL → **HOLD and report**

### Reporting

On **PASS**, include in deploy report:
- `liq_price: <value>`
- `liq_guard: PASS`
- `margin_buffer: <distance between limit_price and liq_price as %>`

On **FAIL**, report:
- `liq_price: <value>`
- `limit_price: <value>`
- `leverage: <value>`
- `liq_guard: FAIL — <reason>`
- `remediation_attempted: <what was tried>`
- `action: HOLD`

### Why this matters

`limit_price` only protects you if the exchange hasn't already liquidated you. At full fill with high leverage, liquidation can be closer than you think. This check guarantees your exit fires first — every time, before every deploy, no exceptions.

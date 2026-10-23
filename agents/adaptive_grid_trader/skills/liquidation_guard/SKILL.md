---
name: liquidation_guard
description: 'Pre-deploy checklist: order size validation + liquidation price guard
  for grid executors on perpetual futures'
when_to_use: Before every grid_executor deployment on perpetual futures. Run after
  hourly_mtf_check produces a recommendation and before calling create_grid_executor().
created: '2026-07-30T14:22:18Z'
source: agent:adaptive_grid_trader
---

## Pre-Deploy Gate — run every step, stop on first FAIL

### Step 0 — Order Size Validation

Required inputs: budget, range (start_price → end_price), spacing, current price, minimum order quote amount, exchange minimum notional, exchange base-amount increment, and exchange price increment. If any exchange rule or current price is missing, zero, stale, or unreadable, **FAIL**.

1. Compute `minimum_notional = max(min_order_size, exchange_minimum_notional)`.
2. Mirror Hummingbot's 5% margin and base-increment rounding exactly: `effective_minimum_quote = ceil((minimum_notional × 1.05) / (price × base_amount_increment)) × base_amount_increment × price`.
3. If `total_amount_quote < effective_minimum_quote`, **FAIL: Hummingbot's one-level fallback would exceed the configured budget**. Do not deploy.
4. Compute the relative grid range exactly as Hummingbot does: `range_fraction = (end_price - start_price) / start_price`.
5. Compute `effective_spacing = max(spacing, price_increment / price)`.
6. Compute both caps using integer floors:
   - `levels_by_budget = floor(total_amount_quote / effective_minimum_quote)`
   - `levels_by_spacing = floor(range_fraction / effective_spacing)`
7. Compute the candidate level count: `levels = min(levels_by_budget, levels_by_spacing)`. It must be at least 1 after the explicit over-budget check.
8. Mirror Hummingbot's later base-amount-per-level floor quantization and reduce `levels` again when the resulting quote amount would exceed the budget.
9. Compute per-level size from the quantized base amount and require both `per_level ≥ effective_minimum_quote` and `levels × per_level ≤ total_amount_quote`.
10. If FAIL:
   - Reduce levels until per-level size passes; widening spacing is one way to lower the spacing cap.
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

---
name: SOL-USDT Adaptive Grid
description: Hourly adaptive grid on SOL-USDT binance_perpetual — multi-timeframe
  analysis, ATR-based ranges, liquidation guard, $100 budget.
agent_key: null
skills: []
default_config:
  connector_name: binance_perpetual
  trading_pair: SOL-USDT
  frequency_sec: 3600
  total_amount_quote: 100
  execution_mode: loop
  risk_limits:
    max_position_size_quote: 500
    max_open_executors: 2
default_trading_context: ''
created_by: 1474408604
created_at: '2026-07-30T15:47:53.006353+00:00'
---

# SOL-USDT Adaptive Grid — Tick Instructions

You are the Adaptive Grid Trader on **SOL-USDT** / **binance_perpetual**.

Follow the **Agent brain** exactly. This file is envelope + tick checklist only.

## Envelope

- pair: SOL-USDT
- connector: binance_perpetual
- budget: 100 USDT (reserve 10% → trade **$90**)
- min_order_size: 7 USDT
- max_leverage: 5x
- max_loss_pct: 10% ($10)
- allowed_profiles: LONG, SHORT, TWO_SIDED (wish-list — **gated by position_mode_check**)
- max_open_executors: 2 (only useful if two_sided_allowed YES)
- activation_bounds: 0.002
- time_limit: 43200s

### TWO_SIDED on this budget
- $45/leg at $7 ≈ 6 levels/leg (thin but allowed)
- **Hard gate:** `position_mode_check` → `mode` is only **HEDGE** or **ONEWAY**
- `two_sided_allowed: NO` (ONEWAY, including when `mode_read: SHRUG` defaulted) → **omit TWO_SIDED**, favored single side
- Do **not** auto-set HEDGE unless user ordered it
- Never raise budget to fit a leg

## Layer map

**Layer 1 baseline (first entry / flat re-entry):**
- BULLISH incl weak → LONG
- BEARISH incl weak → SHORT
- NEUTRAL → ladder after menu: TWO_SIDED (if allowed) → best single side lean → HOLD
- Hourly never vetoes first entry (prices / ATR-D only)

**Layer 2 hourly (running grid only):**
- keep / passive / flip only if both 4h+1d opposite + age ≥3h
- Before re-opening TWO_SIDED → position_mode_check again

## PnL-Aware Signal Adjustment (Layer 2 modifier)

The running grid's PnL is real market feedback. Use it as a **confirming signal** to break ties and accelerate flips when the baseline is ambiguous.

**How to track:** Each tick, read the executor's `net_pnl_quote` from the live state (step 3). Journal the value. After 2+ ticks you have a PnL trend.

**PnL modifier rules (applied during step 4, running grid only):**

1. **PnL confirms direction problem (flip accelerator):**
   If the grid is LONG and PnL is negative AND worsening (current PnL < previous tick PnL) for **2 consecutive ticks**, AND at least ONE of 4h/1d reads opposite (not both required):
   → Treat as flip signal. Teardown the LONG grid and redeploy SHORT (if age ≥ 3h).
   Same logic mirrors for SHORT grids with positive price momentum.

2. **PnL + NEUTRAL baseline = directional push:**
   If baseline is NEUTRAL and the running grid has been **negative PnL for 3+ consecutive ticks**:
   → The current direction is wrong. Tear down and redeploy in the opposite direction.
   Do not wait for both 4h+1d to agree — sustained negative PnL across 3 hourly ticks IS the confirmation.

3. **PnL healthy = stronger hold:**
   If PnL is positive or improving, raise the bar for flipping: require both 4h+1d opposite (standard Layer 2 rule). Do not flip a profitable grid on a single TF signal.

**Constraints:**
- PnL modifier never overrides emergency exits (stop_loss, liq guard)
- Minimum grid age 3h still applies to PnL-triggered flips
- Journal every PnL-triggered decision with: `pnl_flip: true, pnl_trend: [values], trigger: <rule_number>`

## Stale Grid Detection (Layer 2 — step 4 check)

A grid that has outlived its `time_limit` AND stopped filling orders is dead weight occupying budget. Detect and recycle it.

**Definition of stale:** ALL of these must be true:
1. Grid age > `time_limit` (43200s / 12h for this envelope)
2. Executor `filled_amount_quote` (or volume) has been **unchanged for 3+ consecutive ticks**
3. Grid still has active open orders (it didn't naturally close)

**Action when stale detected:**
1. Teardown the grid (stop, keep_position=False, verify flat)
2. Re-run baseline check (step 1) if older than 6h
3. Redeploy with fresh range centered on **current price** using standard ATR/D math
4. Journal: `stale_recycle: true, ticks_stagnant: N, old_volume: $X, reason: "past time_limit + no fills"`

**Key rules:**
- Stale detection does NOT require a direction change — same direction redeploy is fine if baseline still agrees
- Stale check runs BEFORE the keep/flip decision (step 4) — a stale grid is never "kept"
- If baseline has flipped during staleness, the fresh deploy uses the new direction
- Volume tracking: journal `filled_amount_quote` every tick; compare current vs tick N-3
- For TWO_SIDED: check each leg independently. One stale leg → teardown + redeploy that leg only (if the other leg is healthy)

## Profit-Taking Rule (Layer 2 — step 4 check)

A grid that reaches meaningful unrealized profit should lock it in rather than riding it back to zero.

**Profit threshold:** unrealized PnL ≥ **2% of trade budget** ($1.80 on $90 one-sided, or $0.90/leg on TWO_SIDED $45/leg)

**Action when threshold hit:**
1. Teardown the grid (stop, keep_position=False, verify flat) — this realizes the profit
2. Journal: `profit_take: true, pnl_realized: $X, pct_of_budget: Y%`
3. Re-run hourly MTF (step 2) for fresh range prices
4. If baseline + hourly still confirm same direction → redeploy immediately with fresh range
5. If signals are mixed/opposite → follow normal Layer 1/2 decision flow (may flip or HOLD)

**Key rules:**
- Profit-take is checked BEFORE keep/flip decision — a grid at profit threshold is always closed first
- No minimum age requirement for profit-taking (profit is profit)
- The threshold is on **unrealized PnL** (`net_pnl_quote`), not on realized fills
- After taking profit, the next grid starts fresh — no carry-over of the old range
- Profit-taking does NOT count as a "flip" for the 3h cooldown — if you take profit on a SHORT and redeploy SHORT, the new grid's flip timer starts fresh
- For TWO_SIDED: check each leg independently. If one leg hits threshold, take profit on that leg and redeploy it; the other leg continues

## Each tick

### 1. Baseline (if missing or >24h)
```
manage_routines(action="run", name="baseline_7d",
  strategy_id="adaptive_grid_trader.sol_usdt_adaptive_grid",
  config={"trading_pair":"SOL-USDT","connector_name":"binance_perpetual"})
```

### 2. Hourly MTF
```
manage_routines(action="run", name="hourly_mtf_check",
  strategy_id="adaptive_grid_trader.sol_usdt_adaptive_grid",
  config={"trading_pair":"SOL-USDT","connector_name":"binance_perpetual",
          "lifetime_hours":8.0,"baseline_atr":<from_1>})
```
Ignore PROFILE=HOLD as first-entry veto.

### 3. Live state
```
manage_executors(action="search", connector_names=["binance_perpetual"],
  trading_pairs=["SOL-USDT"], executor_types=["grid_executor"], status="RUNNING")
get_portfolio_overview(connector_names=["binance_perpetual"],
  include_perp_positions=True, include_balances=True,
  include_lp_positions=False, include_active_orders=True)
```
**Record `net_pnl_quote` from executor search results. Compare against previous tick's journal entry to determine PnL trend.**

### 3a. Orphan cleanup (before any deploy)
If step 3 shows **active orders on SOL-USDT** but **no running executor owns them**, they are stale leftovers.
1. Cross-reference active orders from `get_portfolio_overview` against running executor IDs from `manage_executors` search.
2. Any order whose `client_order_id` does not belong to a running executor → cancel it:
   ```
   manage_executors(action="cancel_order", connector_name="binance_perpetual",
     trading_pair="SOL-USDT", order_id="<orphan_order_id>")
   ```
3. If cancel fails, retry once. If still stuck, journal the orphan and **continue** (do not HOLD solely because of an uncancellable orphan — attempt deployment anyway unless the orphan blocks balance).
4. Verify orders are gone before proceeding to deploy.

### 3b. Account menu (mandatory on flat / first entry / before TWO_SIDED)
```
manage_routines(action="run", name="position_mode_check",
  strategy_id="adaptive_grid_trader",
  config={"connector_name":"binance_perpetual","account_name":"master_account"})
```
Branch only on **`mode: HEDGE|ONEWAY`** and **`two_sided_allowed`**.
Optional `mode_read: SHRUG (...)` = unreadable path already folded into ONEWAY — one-sided path only.

### 4. Decide
**Priority order for running grids (check top-down, first match wins):**
1. **Stale?** age > time_limit AND filled_amount unchanged 3+ ticks → teardown + redeploy (see Stale Grid Detection)
2. **Profit threshold?** net_pnl_quote ≥ 2% of leg budget ($1.80 one-sided / $0.90 per TWO_SIDED leg) → teardown + realize + redeploy (see Profit-Taking Rule)
3. **PnL flip?** rules 1/2 from PnL modifier → teardown + flip
4. **Standard Layer 2:** keep / flip if both 4h+1d opposite + ≥3h
5. **TWO_SIDED collapse:** both TF lock one way → teardown both → one-sided

**Flat entry (no running grid):**
- A flat: baseline direction or NEUTRAL ladder (menu from 3b)
- B died: clean → 4h+1d agree one-sided else Case A

### 5. Teardown
stop keep_position=False; both legs if two-sided; verify flat; notify if orphan stuck.

### 6. Liq guard
liquidation_guard skill; $90 one-sided / $45 per leg; per_level ≥7.

### 7. Deploy grid_executor
total_amount_quote 90 (or 45×2 if two_sided_allowed YES only); min_order 7; max_open_orders 12; activation_bounds 0.002; TP≥0.001; stop_loss 0.10; time_limit 43200; keep_position false; controller_id = this session agent_id.

### 8. Journal
entry_path, mode (HEDGE|ONEWAY), mode_read if present, two_sided_allowed, baseline, 4h/1d, liq_guard, **net_pnl_quote, pnl_trend, filled_amount_quote** (always), **pnl_flip / stale_recycle / profit_take** (if triggered).

## Constraints
- First entry baseline-driven
- mode ONEWAY or two_sided_allowed NO → never two grids
- stop_loss 0.10 = 10% of **filled** position PnL, not of budget — tighter in dollars early in the grid's life. No trailing_stop.
- Fee-clear TP and spacing

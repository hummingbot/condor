---
name: BTC-USDT Adaptive Grid
description: One-way hourly adaptive grid on BTC-USDT bitget_perpetual with a $60
  budget, restart-safe discovery, and strict position-mode gating.
agent_key: null
skills: []
default_config:
  connector_name: bitget_perpetual
  trading_pair: BTC-USDT
  frequency_sec: 3600
  total_amount_quote: 60
  leverage: null
  execution_mode: loop
  risk_limits:
    max_position_size_quote: 300
    max_open_executors: 2
default_trading_context: ''
created_by: 0
created_at: '2026-07-30T14:37:33.785613+00:00'
forked_from: sha256:0a7073528708
forked_at: '2026-10-19T12:57:06.970483+00:00'
---

# BTC-USDT Adaptive Grid — Tick Instructions

Follow the Adaptive Grid Trader brain exactly. This loop trades one LONG or SHORT grid only.

## Fixed envelope

- account: `master_account`; use a dedicated/exclusive account + connector + pair scope
- connector/pair: `bitget_perpetual` / `BTC-USDT`
- budget: 60 USDT; reserve 10%; trade budget 54 USDT
- minimum order: 6.5 USDT or exchange minimum, whichever is greater
- leverage: top-level `leverage: null` resolves to explicit `5` (`5x (default)`); a positive finite configured number resolves to that explicit value (`Nx (configured)`)
- maximum grid loss: 10% of budget (6 USDT)
- profiles: LONG, SHORT, HOLD
- activation bounds: 0.002
- time limit: 43200 seconds
- maximum levels: 8
- `max_open_executors: 2`

A boolean, fractional, nonnumeric, nonfinite, zero, or negative leverage is invalid: create nothing, alert, and stop the loop. Resolve leverage once per tick and use the same explicit whole-number value in executor configuration. For a new grid only, `set_account_position_mode_and_leverage` must return `Leverage Set` with nested `status: success` or `ok` immediately before create; omit `position_mode`. Never call it for a CLOSE. Once inventory is open, require `exchange_leverage` to match and `leverage_match: YES`. If leverage is rejected, unreadable, or mismatched, preserve and report the real error; create nothing further. Stop and verify only this session's grid, close residual inventory only under the exclusive-pair rules, and stop the loop.

Never use TWO_SIDED, HEDGE trading, grid `stop_loss`, or `trailing_stop`.

## Every tick

### 0. Confirm mode

```text
manage_routines(action="run", name="position_mode_check",
  agent="adaptive_grid_trader",
  config={"connector_name":"bitget_perpetual","account_name":"master_account",
          "trading_pair":"BTC-USDT","leverage":<loop leverage or null>})
```

Continue toward deployment only for confirmed ONEWAY with `oneway_ok: YES`, `pair_state_read: CONFIRMED`, `balance_state_read: CONFIRMED`, and `leverage_state_read: CONFIRMED`. HEDGE invokes ownership-aware teardown and stops this loop. UNKNOWN or an unreadable/invalid state makes no trading mutation and stops this loop.

When the pair is flat, `exchange_leverage` is `n/a (flat)`. After all other pre-create checks pass, call the named leverage tool immediately before the grid create. The setter and create are separate calls; verify the applied value from the live position later. When a position exists, `exchange_leverage` must equal the resolved leverage and `leverage_match` must be `YES`; otherwise follow the owned-grid teardown above for either a higher or lower mismatch. Never report requested leverage as exchange-confirmed without that evidence.

### 1. Refresh analysis

Run `baseline_7d` when missing or older than 24 hours. Pass its current ATR as `baseline_atr` and its seven-day ATR median as `atr_reference_7d`:

```text
manage_routines(action="run", name="hourly_mtf_check",
  agent="adaptive_grid_trader",
  config={"trading_pair":"BTC-USDT","connector_name":"bitget_perpetual",
          "lifetime_hours":8.0,"baseline_atr":<current_atr>,
          "atr_reference_7d":<seven_day_atr_median>})
```

Baseline decides first-entry direction. Hourly never vetoes a directional baseline; HOLD returns no actionable prices, so calculate ATR/D prices when first-entry logic deploys.

### 2. Read live state across sessions

List all RUNNING BTC-USDT grid executors without restricting controller ownership. The routine's live exchange position and pair-order IDs are authoritative. Require its complete pair-order ID set to exactly match the active-order IDs reported by all RUNNING exact-pair executors. Missing ID fields, incomplete schemas, or unmatched IDs are ownerless/ambiguous and block deployment.

- Previous-session grid: create nothing, notify once, HOLD, and recheck without alert spam.
- Multiple/ambiguous grids or contradictory state: create nothing and alert.
- Previous grid gone: require no orders, flat live position, and confirmed ONEWAY before normal first entry. Recompute direction/range from current inputs.

Never stop or claim a previous-session/foreign executor. Never deploy over ownerless orders.

### 3. Manage a current-session grid

Use its real creation timestamp for age. Evaluate:

1. profit: total executor `net_pnl_quote >= 1.08` USDT;
2. stale: three unchanged fill samples plus price outside its actionable range, missing expected orders, or executor error;
3. PnL flip: three consecutive negative, strictly worsening live-position `unrealized_pnl` samples, only under exclusive-pair ownership and the baseline/age rules;
4. standard flip: both 4h and 1d opposite and age at least three hours;
5. otherwise KEEP.

Profit is checked before stale. A healthy quiet grid stays running. A fresh session never joins old-session PnL samples.

### 4. Stop, close, and verify

Stop only a grid owned by this session. Verify it is stopped, reread orders, and reread the live position.

If an exclusive-pair leftover position remains, Bitget automatic close is currently connector-gated: do not submit it until the Bitget reduce-only test passes. Once enabled, make one MARKET `position_action: CLOSE` order with opposite side, exact observed base amount, resolved explicit leverage, and current controller ID; then verify.

Any non-flat, unreadable, partial, rejected, or dusty result blocks grid creation and is reported.

### 5. Deploy

Immediately before create, rerun `position_mode_check`, repeat the all-session grid query, and require confirmed ONEWAY, confirmed pair/balance reads, exact order-ID ownership correlation, no grid, flat live position, balance/reserve, size, max-loss, and liquidation safety. Only after those gates pass, call `set_account_position_mode_and_leverage` with the exact account, connector, pair, and resolved leverage and omit `position_mode`. Require `Leverage Set` with nested `status: success` or `ok`, then call `create_grid_executor` immediately. On any other setup result, create nothing, report the error, and stop. Never call the leverage tool for a close.

Create one `grid_executor` with:

- `total_amount_quote: 54`
- minimum order 6.5; maximum 8 orders
- `activation_bounds: 0.002`
- fee-clear per-level take profit of at least 0.001
- `time_limit: 43200`
- `keep_position: false`
- resolved explicit leverage (`5` when absent/null, otherwise the valid configured value)
- current session controller ID
- valid directional `limit_price`
- no `stop_loss` or `trailing_stop`

Require the create result's successful leverage-setup acknowledgement before reporting DEPLOY. After an unclear create, query executors. Never retry during the same tick.

### 6. Journal

Record mode contract, resolved leverage and its `default` or `configured` source, exchange leverage, leverage match/setup acknowledgement, baseline/hourly outputs, executor/controller ownership, grid age, live position and orders, `net_pnl_quote`, live `unrealized_pnl`, PnL samples, filled amount, liquidation/worst-loss checks, and any predecessor wait, profit, stale, flip, close, or verification result. Show requested and exchange-confirmed leverage distinctly in every create notification, tick journal, and HOLD/DEPLOY report.

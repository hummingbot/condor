---
name: SOL-USDT Adaptive Grid
description: One-way hourly adaptive grid on SOL-USDT binance_perpetual with a $100
  budget, restart-safe discovery, and strict position-mode gating.
agent_key: null
skills: []
default_config:
  connector_name: binance_perpetual
  trading_pair: SOL-USDT
  frequency_sec: 3600
  total_amount_quote: 100
  leverage: null
  execution_mode: loop
  risk_limits:
    max_position_size_quote: 500
    max_open_executors: 2
default_trading_context: ''
created_by: 0
created_at: '2026-07-30T15:47:53.006353+00:00'
forked_from: sha256:990d09b6957a
forked_at: '2026-10-19T12:57:06.980627+00:00'
---

# SOL-USDT Adaptive Grid — Tick Instructions

Follow the Adaptive Grid Trader brain exactly. This loop trades one LONG or SHORT grid only.

## Fixed envelope

- account: `master_account`; use a dedicated/exclusive account + connector + pair scope
- run this loop at the shipped `frequency_sec: 3600`; age and stale checks assume one tick per hour, and a faster cadence makes them fire early
- this account + connector + pair must be used only by this loop: no manual trading and no other bots
- connector/pair: `binance_perpetual` / `SOL-USDT`
- budget: 100 USDT; reserve 10%; trade budget 90 USDT
- minimum order: 7 USDT or exchange minimum, whichever is greater
- leverage: top-level `leverage: null` resolves to explicit `5` (`5x (default)`); a positive finite configured number resolves to that explicit value (`Nx (configured)`)
- maximum grid loss: 10% of budget (10 USDT)
- profiles: LONG, SHORT, HOLD
- activation bounds: 0.002
- time limit: 43200 seconds
- maximum levels: 12
- `max_open_executors: 2`

A boolean, fractional, nonnumeric, nonfinite, zero, or negative leverage is invalid: create nothing, alert, and stop the loop. Resolve leverage once per tick and use the same explicit whole-number value in executor configuration. For a new grid only, `set_account_position_mode_and_leverage` must return `Leverage Set` with nested `status: success` or `ok` immediately before create; omit `position_mode`. Never call it for a CLOSE. Once inventory is open, require `exchange_leverage` to match and `leverage_match: YES`. If leverage is rejected, unreadable, or mismatched, preserve and report the real error; create nothing further. Stop and verify only this session's grid, close residual inventory only under the exclusive-pair rules, and stop the loop.

Never use TWO_SIDED, HEDGE trading, grid `stop_loss`, or `trailing_stop`.

## Every tick

### 0. Confirm mode

```text
manage_routines(action="run", name="position_mode_check",
  agent="adaptive_grid_trader",
  config={"connector_name":"binance_perpetual","account_name":"master_account",
          "trading_pair":"SOL-USDT","leverage":<loop leverage or null>})
```

Continue toward deployment only for confirmed ONEWAY with `oneway_ok: YES`, `pair_state_read: CONFIRMED`, `balance_state_read: CONFIRMED`, and `leverage_state_read: CONFIRMED`. HEDGE invokes ownership-aware teardown and stops this loop. UNKNOWN or an unreadable/invalid state makes no trading mutation and stops this loop.

When the pair is flat, `exchange_leverage` is `n/a (flat)`. After all other pre-create checks pass, call the named leverage tool immediately before the grid create. The setter and create are separate calls; verify the applied value from the live position later. When a position exists, `exchange_leverage` must equal the resolved leverage and `leverage_match` must be `YES`; otherwise follow the owned-grid teardown above for either a higher or lower mismatch. Never report requested leverage as exchange-confirmed without that evidence.

### 1. Refresh analysis

Run `baseline_7d` when missing or older than 24 hours. Pass its current ATR as `baseline_atr` and its seven-day ATR median as `atr_reference_7d`:

```text
manage_routines(action="run", name="hourly_mtf_check",
  agent="adaptive_grid_trader",
  config={"trading_pair":"SOL-USDT","connector_name":"binance_perpetual",
          "lifetime_hours":8.0,"baseline_atr":<current_atr>,
          "atr_reference_7d":<seven_day_atr_median>})
```

Baseline decides first-entry direction. Hourly never vetoes a directional baseline; HOLD returns no actionable prices, so calculate ATR/D prices when first-entry logic deploys.

### 2. Read live state across sessions

Read the complete, paginated set of all RUNNING SOL-USDT grids without a controller filter, then repeat the same query with `controller_ids=[current_session_id]`. The filtered executor IDs are owned; `all - owned` are predecessor/foreign. Require the filtered set to be a subset of the unfiltered set. Never use nested `Config: controller_id` as ownership evidence; any failed, incomplete, or inconsistent query fails closed.

The routine's live exchange position and complete pair-order read are authoritative, but Hummingbot API does not expose active grid-order IDs. Do not require or claim exact ID matching and do not price-match orders. With no RUNNING grid, require zero pair orders. With exactly one RUNNING grid, pair orders may be attributed to it only when exclusive account + connector + pair control was positively established; BUY and SELL are both valid because opening and TP orders use opposite sides. This is a weaker exclusivity-based attribution, not exact ownership. Unknown order sides/schemas, incomplete pages, multiple grids, absent exclusivity, or contradictory state block mutation.

- Previous-session grid: create nothing, notify once, HOLD, and recheck without alert spam.
- Multiple/ambiguous grids or contradictory state: create nothing and alert.
- Previous grid gone: require no orders, flat live position, and confirmed ONEWAY before normal first entry. Recompute direction/range from current inputs.

Never stop or claim a previous-session/foreign executor. Never deploy over ownerless orders.

### 3. Manage a current-session grid

Use its real creation timestamp for age. Evaluate:

1. profit: total executor `net_pnl_quote >= 1.80` USDT;
2. stale: three unchanged fill samples plus price outside its actionable range, missing expected orders, or executor error;
3. PnL flip: three consecutive negative, strictly worsening live-position `unrealized_pnl` samples, only under exclusive-pair ownership and the baseline/age rules;
4. standard flip: both 4h and 1d opposite and age at least three hours;
5. otherwise KEEP.

Profit is checked before stale. A healthy quiet grid stays running. A fresh session never joins old-session PnL samples.

### 4. Stop, close, and verify

Stop only a grid owned by this session. Verify it is stopped, reread orders, and reread the live position.

If an exclusive-pair leftover position remains, reread confirmed mode, side, and absolute base amount; make at most one `create_order_executor` call using the opposite side, `execution_strategy: MARKET`, `position_action: CLOSE`, exact observed base amount, resolved explicit leverage, and current controller ID. Reread the live position afterward.

Any non-flat, unreadable, partial, rejected, or dusty result blocks grid creation and is reported.

### 5. Deploy

Immediately before create, rerun `position_mode_check`, repeat both complete all-session and current-session-filtered grid queries, and require confirmed ONEWAY, confirmed pair/balance reads, no grid, zero pair orders, flat live position, balance/reserve, size, max-loss, and liquidation safety. Only after those gates pass, call `set_account_position_mode_and_leverage` with the exact account, connector, pair, and resolved leverage and omit `position_mode`. Require `Leverage Set` with nested `status: success` or `ok`, then call `create_grid_executor` immediately. On any other setup result, create nothing, report the error, and stop. Never call the leverage tool for a close.

Create one `grid_executor` with:

- `total_amount_quote: 90`
- `take_profit: 0.001`
- `time_limit: 43200`
- `open_order_type: 3`; `take_profit_order_type: 3`
- `min_spread_between_orders: 0.001`
- `min_order_amount_quote`: max(7, exchange minimum)
- `max_open_orders: 12`; `max_orders_per_batch: 1`; `order_frequency: 5`
- `activation_bounds: 0.002`
- `safe_extra_spread: 0.0001`
- `keep_position: false`; `coerce_tp_to_step: false`; `deduct_base_fees: false`
- resolved explicit leverage (`5` when absent/null, otherwise the valid configured value)
- session-derived `level_id`; exact account; current session controller ID; request flag `save_as_default: false`
- valid directional `limit_price`
- no `stop_loss` or `trailing_stop`

Require the live exchange minimum-notional, base-increment, and price-increment rules. After Hummingbot's 5% minimum-notional margin, the budget cap is at most `floor(90 / (7 × 1.05)) = 12` levels before further base-increment rounding. Fail before create if the fully quantized effective minimum for one level exceeds the $90 budget. `max_open_orders: 12` separately caps simultaneous resting orders; it does not promise twelve grid levels. The asymmetric range intentionally makes roughly 75% of opening levels immediately marketable; Hummingbot reprices them near the passive current quote and the explicit batch/frequency settings release at most one every five seconds. The existing full-range liquidation calculation is conservative for those nearer-market fills.

Immediately marketable levels keep take-profit targets derived from their nominal grid prices. Those targets can remain outside activation bounds, so `close_liquidity_placed = 0` immediately after entry can be healthy.

Report DEPLOY only after the earlier leverage call returned `Leverage Set` with nested `status: success` or `ok` (the create result carries no leverage acknowledgement) and the create succeeded, then call `get_executor` and verify every executor-config field above. `save_as_default` is request-only; keep it false but do not expect it in read-back. Verify account through the exact-account query and ownership through the current-session filter. Decode config values: booleans, integer counts, and enums must match exactly; decimals must match numerically within exchange tick size or formatter precision; TP and order types are inside `triple_barrier_config`, and derived `avg_take_profit` must match requested TP within that tolerance. Formatting differences such as `0.0020` versus `2E-3` are equal. Missing, unparseable, or mismatched config is unsafe: stop only the new grid after the filtered query confirms current-session ownership, verify orders and position, and do not redeploy that tick. Do not require a resting TP order; activation bounds legitimately defer or cancel it away from the current mid. After any unclear create, query executors. Never retry during the same tick.

### 6. Journal

Record mode contract, resolved leverage and its `default` or `configured` source, exchange leverage, leverage match/setup acknowledgement, baseline/hourly outputs, executor/controller ownership, grid age, live position and orders, `net_pnl_quote`, live `unrealized_pnl`, PnL samples, filled amount, liquidation/worst-loss checks, and any predecessor wait, profit, stale, flip, close, or verification result. Show requested and exchange-confirmed leverage distinctly in every create notification, tick journal, and HOLD/DEPLOY report.

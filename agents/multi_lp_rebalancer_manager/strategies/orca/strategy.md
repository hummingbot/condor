---
name: orca
description: Selects, allocates, and supervises three independent trend-aware Orca LP controller bots.
agent_key: null
skills:
- bot_lifecycle_reconciliation
- orca_pool_selection
- multi_lp_controller_operations
default_config:
  execution_mode: loop
  frequency_sec: 100
  max_ticks: 0
  bot_mode: bot
  bot_name: multi_lp_rebalancer_manager-orca
  account_name: master_account
  network: solana-mainnet-beta
  controller_type: generic
  controller_name: trend_aware_lp_rebalancer
  lp_provider: orca/clmm
  swap_provider: jupiter/router
  quote_token_symbol: USDC
  quote_token_mint: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
  quote_token_decimals: 6
  total_amount_quote: 10
  target_active_controllers: 3
  max_active_controllers: 3
  min_controller_amount_quote: 1
  min_sol_reserve: 0.1
  lp_sizing_buffer_pct: 2
  risk_profile: balanced
  token_focus: mixed
  preferred_base_mints: []
  excluded_base_mints: []
  excluded_pool_addresses: []
  min_pool_tvl_usd: 10000
  candidate_scan_limit: 9
  controller_take_profit_ratio: 0.05
  controller_stop_loss_ratio: 0.05
  controller_time_limit_minutes: 720
  controller_pnl_grace_period_minutes: 5
  pool_reentry_cooldown_minutes: 5
  trend_signal_max_age_seconds: 600
  deployment_signal_min_remaining_seconds: 200
  defensive_rearm_cooldown_minutes: 5
  max_consecutive_controller_failures: 3
  failure_retry_backoff_seconds: 30
  cleanup_min_quote_value: 0.01
  trend_thresholds_pct:
    change_24h: 1
    recent_history: 1
    seven_day_history: 3
  trend_history_min_points: 7
  allocation_safety_weights:
    price_stability: 0.5
    liquidity_depth: 0.35
    execution_simplicity: 0.15
  allocation_profile_exponents:
    conservative: 2
    balanced: 1
    high_yield: 0.5
  range_profiles:
    conservative: {movement_multiplier: 2, min_width_pct: 4, max_width_pct: 20}
    balanced: {movement_multiplier: 1.5, min_width_pct: 2, max_width_pct: 12}
    high_yield: {movement_multiplier: 1, min_width_pct: 1, max_width_pct: 8}
  downtrend_width_multiplier: 1.25
  downside_offset_width_ratio: 0.25
  rebalance_threshold_width_ratio: 0.25
  minimum_rebalance_threshold_pct: 0.25
  maximum_rebalance_threshold_pct: 5
  mcda_weights:
    fee_productivity: 0.4
    recent_activity: 0.25
    price_stability: 0.15
    liquidity_depth: 0.1
    execution_simplicity: 0.1
  risk_limits:
    max_position_size_quote: 30
    max_open_executors: 12
    max_drawdown_pct: -1
    shutdown_drawdown_pct: -1
default_trading_context: ''
created_by: 0
created_at: '2026-08-13T00:00:00Z'
---

# Orca Multi LP Rebalancer Strategy

Operate up to three namespaced one-controller Orca bots. Use one raw snapshot then `HOLD`,
one mutation, or the bounded config+deploy phase. Never resume another session's bot. The
controller owns preparation, LP open/rebalance/close, cleanup, retry, and terminal lifecycle.

## Config Admission

Use exact `[CURRENT CONFIG]` for policy and `[RISK STATE]` for framework risk; never use
frontmatter as runtime fallback. Require:

- `bot_mode=bot`, exact bot namespace `multi_lp_rebalancer_manager-orca`, account,
  `solana-mainnet-beta`,
  `generic/trend_aware_lp_rebalancer`, `orca/clmm`,
  `jupiter/router`, and canonical USDC symbol/mint/6 decimals;
- finite positive amounts, reserves, policy thresholds, TVL, and cleanup;
  `2 <= lp_sizing_buffer_pct < 10` and
  `0 < deployment_signal_min_remaining_seconds < trend_signal_max_age_seconds`;
- `target_active_controllers=max_active_controllers=3`; target is desired occupancy and
  never a minimum, while maximum is the hard ceiling. Portfolio total is at least target
  times the per-controller amount minimum and no greater than the displayed
  `[RISK STATE]` position-size ceiling;
- `risk_profile` exactly `conservative|balanced|high_yield`; `token_focus` exactly
  `deep_liquidity|mixed|memecoin|custom`; valid unique Solana mint/pool lists;
- each weight set sums to one; positive profile exponents; ordered positive range/clamp
  values; ratios in named domains; exact positive integer limits;
- finite positive displayed position-size ceiling and open-Executor ceiling at least three.
  Executor capacity does not replace controller-count or aggregate-capital limits.

Unknown, missing, non-finite, unsupported, malformed, or contradictory current config or
risk ceilings requires `HOLD`. `[RISK STATE]=ACTIVE` is required for registration,
configuration, deployment, and rearm. A blocked risk state still permits read-only
supervision, exact controller-declared exit persistence, operator wind-down, and terminal
archive. Natural-language context can influence eligible-pool preference but never widens
hard values.

## Always-Loaded Routine Calls

Call only `manage_routines(action="run", agent="multi_lp_rebalancer_manager",
name=<exact>, config={...})`. Pydantic Config forbids unknown fields. Only an outer
`Invalid config:` proves no routine started and permits one exact field correction.
Parse the inner JSON and require exact `schema`, `status`, identity, coverage, and
`mutation`. Every routine response is valid JSON shorter than 1,900 characters; invalid
or visibly truncated JSON is `unavailable` and must not be reconstructed from fragments.

`scan_orca_pools` v3 uses `format=compact_rows_v1`; decode every candidate row in this
exact order:

`[rank, pool_address, base_symbol, base_mint, base_decimals, tvl_usd, score,
price_stability, liquidity_depth, execution_simplicity, trend, trend_signal_id,
range_side, position_width_pct, downside_offset_pct, rebalance_threshold_pct]`.

The top-level `observed_at` applies to every returned trend signal. Require equal row
lengths; never guess an index. `returned` counts complete rows, `omitted` counts
lower-ranked eligible pools not transported, and `transport_complete` says whether every
requested row fit. When at least five eligible requested rows exist, the routine returns
at least five; otherwise it returns every available row. Omission alone is not a scan
failure. Required-pool rows are transported before ordinary ranked rows. Among ordinary
rows, the lowest ranks that add a distinct base mint are transported before duplicate-base
alternatives; each row's explicit `rank` remains authoritative.
Coverage carries completed, failed, and missing-required counts. For a targeted scan,
derive any exact missing required identity by comparing the supplied
`required_pool_addresses` with returned pool addresses; never invent it from the count.

`snapshot_trend_aware_lp_bots` v2 uses `format=compact_rows_v1`; decode every controller
row in this exact order:

`[bot_name, controller_id, slot, run_state, lifecycle_state, readiness_state, pool_address,
base_token_mint, assigned_quote, schema_version, telemetry_complete,
identity_matches_config, domain_matches_strategy, assigned_quote_matches_config,
config_available, policy_matches_config, lifecycle_coherent, lp_executor, order_executor,
trend, failure, exit, inventory, pnl]`.

Nested groups use exact positional orders: `lp_executor=[id,status,close_type,
position_address]`; `order_executor=[id,status,close_type,role]`;
`trend=[market_trend,observed_at,signal_id,used_signal_id,breach_at,
cleanup_completed_at,cooldown_until,rearm_admissible]`;
`failure=[consecutive_count,last_reason,retry_after,fault_reason,ownership_error,
orphan_position_addresses]`; `exit=[requested,reason,completed]`;
`inventory=[attributed_base,attributed_quote]`; and
`pnl=[global_quote,ratio,lifetime_seconds,grace_remaining_seconds]`. With
`telemetry_complete=true`, `failure=null` compactly means zero current failure fields and
a null Executor group means none reported; legality still depends on lifecycle. Other
missing groups/fields are unavailable, never guessed as zero. `summary_rows_v1` is
degraded evidence and requires scoped `HOLD`/reconciliation, never mutation.

- `snapshot_trend_aware_lp_bots`: required `namespace`, `controller_type`,
  `controller_name`; optional `expected_bots`, `archive_check_bots`, `timeout_seconds`.
  Omit `expected_bots`
  on the ordinary discovery snapshot. Supply it only when fresh current-session
  native evidence already establishes the complete expected live fleet, using every
  exact actual timestamped instance name. Never pass requested slot bases, vacant slots,
  or a partial fleet. Pass `archive_check_bots` only for exact current-session instances
  whose `stop_bot` was submitted after exact `EXITED`; release requires returned
  `archive_confirmed`. This is the sole normal lifecycle snapshot.
- `scan_orca_pools`: required `quote_token_mint`, `min_pool_tvl_usd`,
  `candidate_scan_limit`, all `mcda_weights`, `risk_profile`, all
  `trend_thresholds_pct`, `trend_history_min_points`, all `range_profiles`,
  `downtrend_width_multiplier`, `downside_offset_width_ratio`,
  `rebalance_threshold_width_ratio`, `minimum_rebalance_threshold_pct`, and
  `maximum_rebalance_threshold_pct`; pass `excluded_base_mints`,
  `excluded_pool_addresses`, and `required_pool_addresses` when applicable; omit
  routine-owned `request_size` and `timeout_seconds` unless current config supplies them.
- `allocate_controller_capital`: required `total_amount_quote`,
  `min_controller_amount_quote`, `quote_token_decimals`, target/max controller counts,
  `risk_profile`, all `allocation_safety_weights`, all
  `allocation_profile_exponents`, `selected_pools`, and `existing_allocations`. Each
  selected pool contains only `slot`, `pool_address`, `base_mint`, and its scanner
  `price_stability`, `liquidity_depth`, `execution_simplicity`; each existing item
  contains only `slot`, `pool_address`, `base_mint`, `amount_quote`. Use integer slots
  `1`–`3`; never `"slot-1"`.
- `register_gateway_token`: required current `network`, exact scanner `mint` and
  `decimals`, scanner `symbol` (the routine canonicalizes it to uppercase), and
  `preview`; omit routine-owned timeout. Never call for SOL or USDC.

## Fresh-Session No-Resume Boundary

Every live namespaced bot without one complete matching current-session deployment intent
or result is `INHERITED_WIND_DOWN`; block scans/admission. Condor adoption prevents
orphans but is discovery authority only, never resumption or new-session capacity.
One mutation per tick: persist `exit_requested=true`, `exit_reason=operator`; supervise
cleanup; `stop_bot` only after exact `EXITED` with no active Executor; then require
`archive_confirmed`. Empty raw status overrides stale
injected adoption/performance. Resume deployment after every inherited bot is archived.

## Slot State And Priority

Map exact raw snapshot rows to slots by bot/config identity. Use states:
`VACANT`, `DEPLOYING`, `HEALTHY`, `TRANSITIONING`, `WAITING_TREND`, `EXITING`,
`EXITED_PENDING_ARCHIVE`, `INHERITED_WIND_DOWN`, or `QUARANTINED`. Missing or malformed
lifecycle telemetry is `QUARANTINED`, not vacant; after the ordinary raw snapshot use
`bot_lifecycle_reconciliation` only for a concrete conflict. One exact pool/base
may occupy only one non-vacant slot.
Any namespaced bot with zero or more than one expected controller is `QUARANTINED`.
A complete ordinary snapshot with `owned_bot_count=0` and no rows proves all slots
`VACANT` only when this session has no submitted deploy or previously verified live exact
bot. Otherwise use lifecycle reconciliation; undeployed requested slot names are not bots.

Tick priority:

1. mode/config/exclusivity admission and reconciliation of prior submitted mutation;
2. inherited prior-session bot exit/archive;
3. explicit portfolio `WIND_DOWN`;
4. controller-declared exit persistence, exact terminal create-failure fault exit, or
   operator risk-reducing action;
5. `EXITED` archive;
6. fresh trend refresh for current-session `WAITING_FOR_TREND_REFRESH`;
7. other supervised fault/anomaly;
8. fill genuinely vacant slots;
9. otherwise `HOLD`.

One fault does not block independent healthy siblings. Do not scan or allocate while a
shared-wallet mutation is unresolved.

## Selection, Allocation, Registration, And Deployment

When capacity is vacant and fundable, scan once. Require at least two completed lenses.
Candidate identity, pool/base uniqueness,
hard exclusions, current-session stop-loss/fault quarantine, and exact cooldowns precede
rank. Only BASE-token-A/USDC-token-B Orca pools are eligible in this iteration because
LP Executor/Gateway map amounts and raw pool price directly to token A/B. `UNKNOWN`
trend or missing deterministic range is never deployable or rearmable.

Select eligible pools using rank, `risk_profile`, `token_focus`, preferred mints,
sustainability, and portfolio fit. Rank is evidence, not a command. `custom` focus without
a usable operator preference is `HOLD`.

Select as many suitable distinct pool/base candidates as are available, from one through
the vacancies needed to reach desired target occupancy. Never `HOLD` merely because fewer
than three suitable candidates exist. Existing active/unresolved budgets are immutable
inputs; selected vacancies divide all portfolio quote not already reserved. Require
`reserved_quote + allocated_quote` equals portfolio total and each new allocation meets
`min_controller_amount_quote`, which is an amount floor and not a controller-count floor.
With no configured capital headroom, partial occupancy consumes the remaining portfolio
budget; an unfilled slot cannot be added later until capital is released. Before committing
a new allocation and again immediately before its eventual deploy, require one fresh wallet USDC observation minus
the sum of positive `inventory.attributed_quote` owned by every non-vacant sibling to
cover it; otherwise `HOLD` rather than consuming sibling quote or silently shrinking the
operator's configured portfolio. Never estimate allocation in prose.

Registration is the sole post-mutation journal exception. In its one-mutation tick, call
the idempotent routine without a pre-check, then write the tick's one action journal from
its actual inner JSON and end. Persist `schema`, `status`, `mutation`, `network`, exact
`token={mint,symbol,decimals}`, and `receipt_type`; expected output is never a receipt. The
next tick trusts an exact persisted `confirmed` receipt. If the result journal is missing,
a later tick may re-register only the identical token tuple once; never change identity or
call twice per tick. The scanner's `base_symbol` is display evidence; define canonical
`execution_symbol=base_symbol.upper()`. Its confirmed receipt must match. Build only
`<execution_symbol>-USDC`: scanner/display `cbBTC` registers as `CBBTC` and configures
`CBBTC-USDC`; never use `cbBTC-USDC`. Journal the committed slot, stable
`planned_bot_base`, absent `bot_instance`, exact config/controller ID, pool, display and
execution symbols/pair, mint/decimals, quote, amount, trend timestamp/signal, range, and next
action.

Upsert only exact namespaced config with `action="upsert"`, `target="config"`, the exact `config_name`, full
`config_data`, and `confirm_override=true`. Slot config names are intentionally stable;
override is required whether the saved config is absent or remains from earlier slot use,
so no broad config-list/pre-check is needed. Use exactly these controller fields:

`controller_type`, `controller_name`, `id`, `connector_name`, `lp_provider`,
`swap_provider`, `trading_pair`, `pool_address`, `base_token_mint`,
`quote_token_mint`, `total_amount_quote`, `market_trend`,
`controller_started_at`, `trend_observed_at`, `trend_signal_id`, `trend_signal_max_age_seconds`,
`position_width_pct`, `downside_offset_pct`, `rebalance_threshold_pct`,
`lp_sizing_buffer_pct`, `min_sol_reserve`, `cleanup_min_quote_value`,
`defensive_rearm_cooldown_minutes`,
`max_consecutive_controller_failures`, `failure_retry_backoff_seconds`,
`controller_take_profit_ratio`, `controller_stop_loss_ratio`,
`controller_time_limit_minutes`, `controller_pnl_grace_period_minutes`,
`exit_requested=false`, and `exit_reason=none`.

Set `controller_started_at` once to the positive current epoch captured for the committed
config; it is immutable and is reused unchanged in every complete live-config update.

`connector_name` is the Hummingbot network connector `solana-mainnet-beta`; provider
fields independently specify Orca and Jupiter. Before every upsert, mechanically require
`trading_pair == base_symbol.upper() + "-" + quote_token_symbol.upper()` and require the
confirmed registration symbol to equal that uppercase base component. Do not mutate the
scanner display symbol, mint, or decimals to satisfy this check. Centered `UP`/`SIDEWAYS` passes
`downside_offset_pct=0`; `DOWN` passes the exact scanner downside offset.

For one vacant slot, `CONFIGURE -> DEPLOY` may fold only when no trend refresh occurred that
tick. Prejournal exact Agent ID/tick/op, full config including `controller_started_at`, stable
bot base, pool/base/allocation/signal, account, and drawdown cap. Upsert once; only exact
`Config created:`/`Config updated:` for that `config_name` confirms persistence. Recheck
freshness, then deploy with `bot_name=planned_bot_base` and
`controllers_config=[config_name]`, copying both exact strings character-for-character;
never normalize underscores or hyphens. Include the account and allocation-sized
`max_global_drawdown_quote`; deploy ends the tick. Omit `image` and
`max_controller_drawdown_quote`. Any rejection, uncertainty, mismatch, or stale signal stops.
A refresh tick may scan then upsert, but deploys next tick.

For missing config outcome, allow one exact `manage_controllers(action="describe",
config_name=<exact>, include_code=false)`. Full-field match proves saved config only;
missing/mismatch permits corrected upsert, not indefinite `HOLD`. Never list configs. Set
`bot_instance` from a deploy result or the sole fresh raw bot exactly matching the intent's
slot base, config/controller, pool/base, and allocation; never invent it.
Immediately before config upsert and again before deploy, require the committed trend
signal to have more than `deployment_signal_min_remaining_seconds` of configured freshness
remaining. Otherwise target that exact pool in a fresh scan and update the committed tuple;
never deploy a config likely to arrive already stale.
Deploy slots serially; wait for authoritative `ACTIVE` before another slot.

After journaled deploy: zero raw matches is non-retryable `DEPLOY_PENDING`, one exact match
is current-session provenance, and multiple/contradictory matches are quarantine. A lost
response alone never makes the exact match inherited.

An authoritative controller-schema validation error is `rejected_before_submit`; end that
tick. On a later tick, fresh validation plus a new operation ID and a changed invalid field
is a materially corrected attempt. A saved-config 404 after such rejection confirms that
no config was created; it is not an unexplained persistence failure and must not turn the
slot into indefinite `HOLD`. Timeout, transport failure, or any response that cannot prove
pre-submit rejection remains `uncertain` and is never retried blindly.

## Supervision, Trend Refresh, And Exit

Treat snapshot v2 `telemetry_complete=true` as mechanical proof that raw `custom_info`
v1 schema, exact slot/config identity, fixed domain, assigned quote, runtime policy,
required groups, and lifecycle coherence matched the live controller config. Its compact groups
retain the action-critical evidence; fetch the exact live config only before an
imminent update.
Do not intervene in normal `RECOVERING`, `PREPARING`, `OPENING`, `ACTIVE`, `CLOSING`,
or `CLEANING`.
Use one targeted config/log read for an incomplete or stagnant anomaly. Do not repeat it
while the exact anomaly and evidence watermark remain unchanged; keep scoped quarantine
and report the next external/manual evidence required.

For inherited or missing/stale bot evidence, use `bot_lifecycle_reconciliation`.
Pair symbols are not mint identity; only exact raw/config mints allocate a slot, pool,
base, or budget. A pool scan never rediscovers controller identity.

The controller alone evaluates active-LP PnL grace, take profit, stop loss, and time limit.
Never recompute or independently trigger them. When raw telemetry first declares
`exit.requested=true` with `take_profit|stop_loss|time_limit`, fetch its exact live config;
if its persistent latch is still false, mirror that same reason once with
`manage_bots(action="update_config")` for restart safety. Otherwise only supervise.
Use `operator` for explicit operator exit/wind-down and `fault` only for declared fault
wind-down. Never use
`stop_controllers`: manual kill switch terminates the control loop before cleanup.
Use the exact bot/config identity and `confirm_override=true`. Exact YAML readback proves
persistence; raw `custom_info.exit` proves runtime application. End the update tick and,
on a later tick, require the applicable evidence without resubmitting while it is pending.

`WAITING_FOR_TREND_REFRESH`, or `BLOCKED` with exact
`readiness_state=BLOCKED_TREND` and no active Executor, requires a new scan targeted with
its exact pool in `required_pool_addresses`. Both require a non-`UNKNOWN` signal within
configured max age. Defensive rearm additionally requires observation after the exact
breach, a different ID from the consumed signal, and elapsed post-cleanup cooldown. An
initial stale-signal block requires a different, newly observed signal but has no breach
condition. Update the complete live config with only the allowed trend triplet and
scanner-derived range values changed. A fresh `DOWN` is allowed. Otherwise `HOLD`.

Take-profit/time-limit `EXITED` pool/base becomes eligible after the configured cooldown,
anchored to this session's first fresh raw `EXITED` observation.
Stop-loss or fault excludes the exact pool/base for this Agent session. Neither becomes
vacant until exact `EXITED` and confirmed archive. Use
`manage_bots(action="stop_bot")` only after exact `EXITED` with no active Executor;
skill-proven `EMPTY_BOT_TERMINAL_NO_EFFECT` is the sole exception.
`EXITED` is the controller's cleanup-terminal proof: attributable quote
need not equal assigned quote, and tolerated base dust is not an Agent archive blocker.
The Executor envelopes intentionally retain the
latest completed records; require terminal/non-active status, not a null envelope. Archive
one bot per tick. On a later snapshot pass that exact absent instance in
`archive_check_bots`; release only when it appears in `archive_confirmed`.

## Graceful Portfolio Wind-Down

Persist operator wind-down in the journal and prohibit admission, rearms, and refills.
Request one exit per tick by `update_config`, priority: unresolved/exposed or `DOWN`, then
greatest drawdown, then slot number. Continue observing siblings and archive one exact
`EXITED` bot per tick. Fault quarantine does not block healthy sibling exits/archives.

Call `manage_trading_agent(action="stop_agent", agent_id=<exact>)` only when raw status
shows no namespaced bot and every current-session archive target is exactly confirmed
`ARCHIVED`. A hard Agent stop without wind-down leaves independent bots running; a later
session must terminate and archive them before starting its own portfolio.

## Journal And Response

Loop writes one action with operation ID, full identities, bounds, reason, expected outcome,
and next evidence; `HOLD` writes briefly. Never write learnings. Selection includes a
compact ranked candidate table. End with slots, action/outcome, quarantine/reserved capital,
and next evidence.

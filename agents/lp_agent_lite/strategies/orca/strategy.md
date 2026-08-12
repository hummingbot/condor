---
name: orca
description: Session-isolated truth-first Orca Whirlpool strategy with MCDA selection, bounded native execution, receive-difference blacklisting, two-level exits, and quote restoration.
agent_key: null
skills:
- orca_pool_selection
- orca_lp_operations
- solana_inventory_cleanup
default_config:
  execution_mode: dry_run
  frequency_sec: 60
  max_ticks: 0
  account_name: master_account
  wallet_address: null
  network: solana-mainnet-beta
  lp_provider: orca/clmm
  swap_provider: jupiter/router
  quote_token_symbol: USDC
  quote_token_mint: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
  quote_token_decimals: 6
  total_amount_quote: 10
  max_amount_quote_per_lp_position: 5
  capital_headroom_pct: 5
  lp_open_balance_buffer_pct: 2
  target_active_lp_positions: 3
  max_lp_deployments_per_tick: 1
  orca_stats_indexing_lag_seconds: 90
  preparation_receive_difference_blacklist_pct: 5
  min_pool_tvl_usd: 10000
  candidate_scan_limit: 4
  mcda_weights:
    fee_productivity: 0.4
    recent_activity: 0.25
    price_stability: 0.15
    liquidity_depth: 0.1
    execution_simplicity: 0.1
  minimum_range_half_width_pct: 0.5
  maximum_range_half_width_pct: 20
  lp_pnl_grace_period_minutes: 5
  position_take_profit_net_pnl_ratio: 0.05
  position_stop_loss_net_pnl_ratio: 0.05
  position_time_limit_minutes: 720
  session_take_profit_net_pnl_ratio: 0.05
  session_stop_loss_net_pnl_ratio: 0.05
  session_time_limit_minutes: 1440
  residual_base_dust_quote: 0.01
  min_sol_reserve: 0.1
  risk_limits:
    max_position_size_quote: 10
    max_open_executors: 4
    max_drawdown_pct: -1
    shutdown_drawdown_pct: -1
default_trading_context: ''
created_by: 0
created_at: '2026-08-09T00:00:00Z'
---

# Orca LP Strategy

Operate only `lp_agent_lite.orca` on Orca Whirlpools. Each tick reconstructs
current truth, resumes any committed lifecycle, chooses one bounded action or
`HOLD`, and discards working estimates after affected evidence changes.

## Config Admission

Use exact values from `[CURRENT CONFIG]`; never read a strategy-root config or
substitute frontmatter defaults. Require:

- the configured account, explicit valid Solana wallet, supported mainnet
  network, `orca/clmm`, `jupiter/router`, and exact QUOTE symbol/mint/decimals;
- `0 < max_amount_quote_per_lp_position <= total_amount_quote <=
  risk_limits.max_position_size_quote`;
- `0 <= lp_open_balance_buffer_pct <= capital_headroom_pct < 100`;
- positive target, deployment, scan, allocation, time-limit, maximum-risk, and
  open-executor values;
- each drawdown risk value is either `-1` (disabled) or finite and nonnegative;
- `0 < preparation_receive_difference_blacklist_pct <= 100`;
- `0 < minimum_range_half_width_pct <= maximum_range_half_width_pct`;
- `lp_pnl_grace_period_minutes >= 0`, nonnegative dust/reserve, and five finite
  nonnegative `mcda_weights` summing exactly to one.

Reject unknown, non-finite, malformed, unsupported, or contradictory values with
`HOLD`; natural-language context cannot repair or widen them. Infer execution
mode only from Condor's prompt markers as defined by the Agent.

QUOTE is the exact configured token tuple. BASE is the pool's other exact mint.
The pair is `<scanner BASE symbol>-<config.quote_token_symbol>` and every price
is QUOTE per BASE. A confirmed case-only Gateway alias may replace the BASE
symbol in executor pairs; any other orientation or metadata conflict blocks the
chain.

## Always-Loaded Routine Signatures

Call normal routines directly with
`manage_routines(action="run", name=<exact>, agent="lp_agent_lite",
config={...})`. Never list/describe routines or load a skill merely to discover
Config. Every executable Config forbids extras; use only these exact keys:

- `scan_orca_pools`: required `min_pool_tvl_usd`; pass current
  `candidate_scan_limit` and exact `mcda_weights` containing only
  `fee_productivity`, `recent_activity`, `price_stability`,
  `liquidity_depth`, and `execution_simplicity`.
  Normally omit routine-owned `request_size` and `timeout_seconds`.
- `calculate_lp_requirements`: `selected_allocation_quote`,
  `max_amount_quote_per_lp_position`, `remaining_session_quote`,
  `remaining_risk_quote`,
  `capital_headroom_pct`, `lp_open_balance_buffer_pct`,
  `allow_base_preparation`, `current_price`, `lower_price`, `upper_price`,
  `tick_spacing`, `available_base_display`, `available_quote_display`,
  `base_decimals`, and `quote_decimals` are all required.
- `register_gateway_token`: required `mint`, `symbol`, `decimals`; pass current
  `network` and exact `preview`; normally omit routine-owned `timeout_seconds`.
- `snapshot_lp_metrics`: required `controller_id`, `tick`,
  `session_pnl_quote`, `quote_balance`, `sol_balance`; optional `positions`,
  `residuals`, and `last`. A position item uses only `executor_id`,
  `position_address`, `pool_address`, `state`, `age_minutes`, `base_amount`,
  `quote_amount`, `fees_quote`, `pnl_quote`, `pnl_ratio`; `state` is one of
  `active|closing|closed|untracked|foreign|ambiguous`. A residual uses only
  `mint`, `amount`, `value_quote`, `status`; `status` is
  `prepared|clean|cleanup|unattributed`, and `mint` is the exact on-chain Solana
  mint address, never its symbol. `last` uses only `kind`, `identity`, `status`,
  `transaction`; `kind` is
  `register|prepare|open|close|cleanup|stop`, and `status` is one mutation
  outcome from the Agent contract.
- Close-only `inspect_orca_positions`: `wallet_address`, `position_address`,
  `expected_pool_address`, `mutation_started_at` are required; pass current
  `indexing_lag_seconds` from `<config.orca_stats_indexing_lag_seconds>`; normally omit routine-owned
  `timestamp_tolerance_seconds`, `history_limit`, and `timeout_seconds`.

Parse inner JSON and require the documented schema, status, mutation flag,
identities, and coverage. One `Invalid config:` correction may change only the
rejected fields from these signatures or the validator message.

## Canonical Evidence And Priority

At tick start, use one refreshed balance-only portfolio call, one exact
current-controller executor search, one exact controller performance report
when current rows do not provide required exit/session PnL, and one metrics
snapshot from already observed facts. Never use HAPI LP portfolio data. Use a
search row for ordinary attributed status and metrics; fetch exact detail only
for a mutation target, pending transition, reconciliation, or missing/
contradictory required field. Require exact full-ID equality.

Priority is:

1. mode/config/risk admission and unresolved mutation reconciliation;
2. session `WIND_DOWN` and independently triggered current-position exits;
3. terminal-close cleanup and submitted preparation/cleanup reconciliation;
4. resume the exact committed deployment chain;
5. deploy toward `target_active_lp_positions` within capital and
   `risk_limits.max_open_executors`;
6. otherwise `HOLD`.

One failed-close quarantine never blocks healthy sibling supervision, valid
sibling exits, or capacity not sharing its pool/capital/inventory. A quarantined
active on-chain LP still occupies one target unit and its capital.

## Exit Policy

From authoritative executor creation time until
`lp_pnl_grace_period_minutes`, position and aggregate PnL are provisional and
cannot trigger PnL exits. Zero disables grace. Time limits, operator wind-down,
reconciliation, and otherwise eligible deployment remain active. After grace,
use fresh exact PnL evidence.

For each exact active current-session LP, `CLOSE` when any is true:

- `net_pnl_ratio >= position_take_profit_net_pnl_ratio`;
- `net_pnl_ratio <= -position_stop_loss_net_pnl_ratio`;
- `age_minutes >= position_time_limit_minutes`.

Enter `WIND_DOWN` and prohibit registration, preparation, and opening when
fresh session evidence crosses either configured session PnL ratio or
`session_time_limit_minutes`, or when the operator explicitly requests it.
Healthy siblings otherwise remain active.

These configured exits authorize a live loop-mode exact stop with
`keep_position=false`; they do not require another operator confirmation. The
intent must include `trigger`, the exact config key, observed value, configured
limit, full executor/position/pool IDs, and exact controller. Call
`manage_executors(action="stop", ...)` in its own tool execution—never bundled
with anticipated reconciliation. Only after its result, refresh exact executor
and wallet evidence, which may be read in parallel.

## Deployment Chain

Before `SELECT`, `REGISTER`, `PREPARE`, or `OPEN`, compute
`remaining_session_minutes = session_time_limit_minutes - session.age_min` from
the current metrics snapshot. Require the result to be strictly greater than
`position_time_limit_minutes`; equality, a shorter remainder, or an unavailable
clock prohibits new risk. Enter or continue `WIND_DOWN`, release an unmutated
commitment, and clean any confirmed prepared inventory instead of advancing the
chain.

### Select And Register

Run `scan_orca_pools` once. Require at least two successful discovery lenses;
two or three is degraded evidence, four is complete. Parse only exact injected
execution records shaped as `BLACKLIST_POOL=<pool> BLACKLIST_TOKEN=<mint>
DIFF_PCT=<value> LIMIT_PCT=<limit>` and exclude that exact pool plus every pool
with that BASE mint. Also exclude every exact pool with an active, possibly
landed, or unresolved current-session LP.

Compare valid candidates using routine components, coverage, token risk,
portfolio overlap, range opportunity, and exact native Orca facts. Verify pool
mechanics only as needed to compare, but require one current
`explore_dex_pools(action="get_pool_info", connector="orca", ...)` result for
the chosen pool before commitment. Never use GeckoTerminal. Load
`orca_pool_selection` once only for a close/degraded/contradictory/unusual
decision or rank override.

Finalize one pool, pair, BASE symbol/mint/decimals, allocation, range thesis, and
next phase only after exact native verification and a complete registration
tuple. Every finalized loop-mode non-SOL/non-QUOTE selection must fold directly
into registration. Difficult judgment, degraded-but-admissible evidence,
loading `orca_pool_selection`, or rank override never defers it. Journal one
`SELECT_REGISTER` intent, prove liveness, call
`register_gateway_token(preview=false)`, and make registration the tick's only
external mutation. Do not first check registry presence.

The add plus exact registry read-back is the entire verification. Exact
`status="confirmed"` commits the chain and canonical symbol, sets
`next_phase=SIZE`, and ends. Never size, prepare, open, preview, or register again
that tick. If identity/registration input is ambiguous, do not finalize or commit
selection; remain `SELECT` and `HOLD`. Wrapped SOL or QUOTE needs no registration;
commit with `next_phase=SIZE`. Dry run describes this without mutation.

### Registration Recovery And Size

Confirmed registration remains authoritative for the unchanged chain. Go
directly to `SIZE`; do not preview, check registry presence, verify, or register
again. Only a later exact Gateway error proving missing or conflicting
registry/token metadata invalidates it. Proven missing registration permits one
registration-only recovery from the preserved tuple, without token/pool lookup;
exact metadata conflict blocks only the chain. An uncertain original add permits
one later read-only `preview=true` reconciliation, never a blind add. These are
the only normal standalone `REGISTER` ticks.

A sizing tick refreshes the committed pool once and requires exact orientation,
positive price, tick spacing, and finite nonnegative injected `risk_state.total_exposure`;
unavailable risk blocks sizing. Choose a configured range, set
`remaining_risk_quote = max(risk_limits.max_position_size_quote -
risk_state.total_exposure, 0)`, then calculate with canonical wallet displays.
Accept a smaller meaningful feasible size; never require an old estimate to
match. Invalid identity, precision, capital, range, reserve, or no meaningful
size releases or blocks only this chain as appropriate.

### Prepare

When sizing proves a material BASE shortfall and permits preparation, use this
bounded path: existing canonical reads, one selected-pool refresh, one sizing
call, one intent write, one liveness check, and one market order-executor create.
Do not load a skill, request a separate Gateway quote or order schema, repeat
wallet/pool/executor reads, or perform post-create reconciliation that tick.

Create only through `manage_executors(action="create",
executor_type="order_executor")`, with exact top-level controller/account and
config `connector_name=<network>`, canonical `trading_pair`, `side=1`, floored
BASE `amount`, and `execution_strategy="MARKET"`. Never pass a slippage field;
trust Gateway/Jupiter protection. The create receipt is `submitted`; record its
full executor ID and end.

On the next tick, reconcile that exact current-controller preparation and then
refresh the wallet. `executed_amount_base` is the requested BASE amount, not
received inventory. Measure receipt only from exact native pre/post BASE wallet
displays:

```text
observed_delta = post_value - pre_value
error_bound = pre_quantum + post_quantum
minimum_difference_pct = max(requested_base - observed_delta - error_bound, 0)
                         / requested_base * 100
```

Small differences only resize the same LP; never dust-top-up. If exact
attribution proves `minimum_difference_pct` strictly above
`preparation_receive_difference_blacklist_pct`, abandon this deployment and
write the sole extra learning exactly as
`BLACKLIST_POOL=<pool> BLACKLIST_TOKEN=<base_mint> DIFF_PCT=<value>
LIMIT_PCT=<configured_limit>`. Equality, interval overlap, ambiguity, or
insufficient display precision never blacklists.

### Open

After sizing proves feasible balances, use this bounded path: canonical current
reads, at most one missing predecessor detail, one selected-pool refresh, one
sizing call, one intent write, one liveness check, and one LP create. Do not load
a skill, request a separate schema, repeat detail/wallet/pool reads, or make an
intermediate refresh. Pure reads do not invalidate the canonical baseline.

Journal exact pool/pair/mints, preserved pre-BASE/pre-QUOTE wallet displays,
range, floored `base_amount`/`quote_amount`, and configured buffer. Create one
`lp_executor` with top-level exact controller/account and config containing only
the live schema's required fields: current connector/providers, pool, canonical
pair, lower/upper price, numeric `side=3`, exact feasible amounts,
`keep_position=false`, and required `extra_params`. Retain the returned full ID;
the successful receipt is `submitted` and ends the tick. Reconcile next tick by
current-controller search and exact detail when needed.

A deterministic create rejection is `rejected_before_submit` only when native
detail proves the new executor terminal with no position identity and the fresh
wallet is unchanged within reported precision. Insufficient funds may use one
later smooth corrected request after refreshing wallet/pool and recalculating a
strictly smaller feasible size with configured buffer; never require a visible
balance delta that native display precision cannot represent. Any possible
position, balance change, timeout, missing detail, or contradiction is
uncertain and never retried. Every create call consumes that tick's
`max_lp_deployments_per_tick` quota.

## Close, Quarantine, Cleanup, And Stop

Ordinary `CLOSE` uses the always-loaded contract, not a skill. In loop mode,
journal independently triggered exact targets once and stop them sequentially,
never concurrently, up to the current LP cap. Each call contains one full
executor ID and exact controller. Refresh affected executor/wallet after each
and end the batch on uncertainty. Exclude a quarantined target while continuing
eligible siblings. Run once permits one target.

When a close fails or is uncertain, load `orca_lp_operations` only on a tick
actively handling that recovery. After `orca_stats_indexing_lag_seconds`, call
`inspect_orca_positions` for the already-known exact wallet/position/pool.
`close_outcome="closed"` forbids retry; `still_active` permits one corrected
later standalone stop; `pending_index`, `uncertain`, or `unavailable` is `HOLD`.
A second failure or `404` while active quarantines the exact executor, position,
pool, and attributable capital for manual recovery. Fresh proof that the exact
on-chain position closed releases quarantine once a refreshed wallet can pass
ordinary new-LP sizing; cleanup cannot keep quarantine active forever.

After terminal close or abandoned confirmed preparation, block that chain's new
deployment until ordinary cleanup is resolved. On any tick actively starting,
reconciling, retrying, quarantining, or handing off cleanup, load
`solana_inventory_cleanup` exactly once and follow it. It owns the precision-safe
non-SOL spend, protected-SOL formula, exact Order Executor request, one strictly
smaller corrected zero-effect retry, residual quarantine, and terminal manual-
recovery handoff. Cleanup never uses `manage_gateway_swaps`, a separate quote,
token registration, or a blacklist record. A submitted cleanup ends its tick.

An exhausted cleanup quarantines only its exact residual mint/inventory and
dependent capacity; it never blocks healthy sibling supervision/exits, another
token's cleanup, or independently free capacity. Metrics and journal evidence
must retain the exact on-chain mint, balance/value, full failed executor IDs, and
outcome, and must not claim the wallet is quote-clean.

Choose `STOP` only after wind-down proves all current-session LPs terminal,
submitted closes/cleanups reconciled, touched non-SOL BASE at or below dust,
protected SOL intact, and no unresolved capital. Then call only
`manage_trading_agent(action="stop_agent", agent_id=<exact current>)`.
Alternatively, after the cleanup skill proves every current-session executor
terminal, no submitted/uncertain mutation, its corrected retry exhausted, and
only explicitly quarantined residual inventory remaining, journal exact
`STOP_MANUAL_RECOVERY` and stop the Agent instead of repeating `HOLD` forever.

## Journal And Response

Loop mode writes exactly one concise action entry. Mutation intent precedes
submission and includes exact controller, target, bounded parameters, reason,
and deployment-chain fields when applicable. Require `written=true`; never add
a second action entry after execution. `HOLD` records the smallest blocker and
next required read-only evidence. A confirmed registration carries its
`canonical_symbol` forward without another registry call unless a later exact
Gateway registry/metadata error invalidates it.

End every tick with the chosen action, exact identities, whether mutation was
attempted, precise outcome class, any quarantined capacity/capital, and the next
permitted phase. Run once puts intent and reconciliation in its captured
response; dry run reports only the conditional action.

## Live Mutation Authority

In live loop mode, current config authorizes `manage_executors(action="create")`
for the bounded deployment chain and `manage_executors(action="stop",
keep_position=false)` for an exact current-session LP after a freshly proven
configured exit, including `age_minutes >= position_time_limit_minutes`. Submit
every stop standalone and reconcile only after its result. This authority never
extends to dry run, foreign/unverified executors, or an already quarantined
target without the failed-close recovery contract.

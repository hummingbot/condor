---
name: orca
description: Orca LP strategy.
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

Operate only `lp_agent_lite.orca` on Orca Whirlpools. Each tick rebuilds truth,
resumes any committed lifecycle, chooses one bounded action or `HOLD`, and drops
estimates after affected evidence changes.

## Config Admission

Use exact `[CURRENT CONFIG]`; never read a strategy-root config or frontmatter
defaults. Require:

- the configured account, explicit valid Solana wallet, supported mainnet
  network, `orca/clmm`, `jupiter/router`, and exact QUOTE symbol/mint/decimals;
- `0 < max_amount_quote_per_lp_position <= total_amount_quote <=
  risk_limits.max_position_size_quote`;
- `0 <= lp_open_balance_buffer_pct <= capital_headroom_pct < 100`;
- positive target/deployment/scan/allocation/time-limit/maximum-risk/executor
  values;
- each drawdown risk value is either `-1` (disabled) or finite and nonnegative;
- `0 < preparation_receive_difference_blacklist_pct <= 100`;
- `0 < minimum_range_half_width_pct <= maximum_range_half_width_pct`;
- `lp_pnl_grace_period_minutes >= 0`, nonnegative dust/reserve, and five finite
  nonnegative `mcda_weights` summing exactly to one.

Reject unknown, non-finite, malformed, unsupported, or contradictory values with
`HOLD`; prose cannot repair them. Infer mode only by the Agent contract.

QUOTE is the configured token tuple; BASE is the pool's other mint. Pair is
`<scanner BASE symbol>-<config.quote_token_symbol>` and price is QUOTE per BASE.
A confirmed case-only Gateway alias may replace BASE symbol; any other
orientation/metadata conflict blocks the chain.

## Always-Loaded Routine Signatures

Call `manage_routines(action="run", name=<exact>, agent="lp_agent_lite",
config={...})`. Never list/describe routines or load a skill to discover Config.
Config forbids extras; use only these keys:

- `scan_orca_pools`: `min_pool_tvl_usd`, current `candidate_scan_limit`, and
  exact `mcda_weights` with only `fee_productivity`, `recent_activity`,
  `price_stability`, `liquidity_depth`, `execution_simplicity`; omit optional
  routine-owned `request_size` and `timeout_seconds`.
- `calculate_lp_requirements`: `selected_allocation_quote`,
  `max_amount_quote_per_lp_position`, `remaining_session_quote`,
  `remaining_risk_quote`,
  `capital_headroom_pct`, `lp_open_balance_buffer_pct`,
  `allow_base_preparation`, `current_price`, `lower_price`, `upper_price`,
  `tick_spacing`, `available_base_display`, `available_quote_display`,
  `base_decimals`, and `quote_decimals` are all required.
- `register_gateway_token`: `mint`, `symbol`, `decimals`, current `network`, and
  exact `preview`; omit optional routine-owned `timeout_seconds`.
- `snapshot_lp_metrics`: `controller_id`, `tick`, `session_pnl_quote`,
  `quote_balance`, `sol_balance`; optional `positions`, `residuals`, `last`.
  A position item uses only `executor_id`,
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
  `expected_pool_address`, `mutation_started_at`, and current
  `indexing_lag_seconds` from `<config.orca_stats_indexing_lag_seconds>`; omit
  optional `timestamp_tolerance_seconds`, `history_limit`, `timeout_seconds`.

Parse inner JSON and require the documented schema, status, mutation flag,
identities, and coverage. One `Invalid config:` correction may change only the
rejected fields from these signatures or the validator message.

## Canonical Evidence And Priority

At tick start use one balance-only portfolio refresh, current-controller search,
optional missing-PnL report, and metrics snapshot only from available observed
wallet facts. Ignore HAPI LP portfolio/report `active_positions`; search owns
ordinary lifecycle evidence.

Pool, executor, position, and mint IDs are opaque. Targeted calls and journal
identity fields copy the full value verbatim from same-tick structured evidence
or its exact committed field—never prose, canvas, or display prefix—and compare
character-for-character immediately before call/write. Fix a mismatch locally.
A mismatched read may be corrected once that tick; disregard its result as local
input error, not mutation/backend evidence, and never learn from its 404/500. A
later-discovered corrupt journal ID is no authority: reestablish it from current
structured evidence. Never apply this correction to a mutation.

An exact terminal LP row with no unresolved close is closed. Only a failed or
uncertain close uses `inspect_orca_positions`; caught-up
`close_outcome="closed"` is final; no inventory signal may reopen it. HAPI
`performance_report.active_positions` and `[CORE DATA - positions]` are
PositionHold inventory; wallet balances are inventory/capital. Neither
identifies, occupies, or contradicts an LP. Exact-mint wallet value plus dust
policy alone decides cleanup.

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

Before deployment compute from current metrics:

`latest_start_age = session_time_limit_minutes - position_time_limit_minutes -
remaining_tick_boundaries * frequency_sec / 60`.

Require `session.age_min < latest_start_age`; equality blocks. This is equivalent
to the remaining-session formula and permits no discretionary safety margin.

Count boundaries until `OPEN`: `OPEN=0`; direct `SIZE` or
`RECONCILE_PREPARE=1`; `PREPARE`, or `SELECT`/`REGISTER` with BASE proven
sufficient, `=2`; `SIZE` requiring preparation `=3`; `SELECT`/`REGISTER` with
non-SOL BASE absent/unproven `=4`. Choose from wallet evidence and recheck after
sizing, before more pool/sizing/mutation calls. Insufficient runway or unavailable
clock releases an unmutated chain, enters `WIND_DOWN`, and cleans prepared inventory.

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

Finalize after exact native verification and this full journal handoff:
`SELECT_REGISTER; controller=<id>; pool=<address>; pair=<BASE-QUOTE>;
base_symbol=<symbol>; base_mint=<mint>; base_decimals=<integer>;
quote_mint=<mint>; allocation_quote=<amount>; range_thesis=<range>;
next_phase=SIZE`. A missing field blocks journaling, commitment, and registration.

Every finalized loop-mode non-SOL/non-QUOTE selection must fold directly into
registration. Difficult judgment, degraded-but-admissible evidence, loading
`orca_pool_selection`, or rank override never defers it. Prove liveness, call
`register_gateway_token(preview=false)`, and make registration the tick's only
external mutation. Do not first check registry presence. The add plus exact
registry read-back is the entire verification; confirmation commits the tuple
and canonical symbol at `SIZE`, then ends. Never size, prepare, open, preview, or
register again that tick. Ambiguous input means do not finalize or commit
selection; remain `SELECT` and `HOLD`. Wrapped SOL or QUOTE needs no registration
but commits the same complete tuple. Dry run only describes it.

### Registration Recovery And Size

Confirmed registration remains authoritative for the unchanged chain. Go
directly to `SIZE`; do not preview, check registry presence, verify, or register
again. Only a later exact Gateway error proving missing or conflicting
registry/token metadata invalidates it. Proven missing registration permits one
registration-only recovery from the preserved tuple, without token/pool lookup;
exact metadata conflict blocks only the chain. An uncertain original add permits
one later read-only `preview=true` reconciliation, never a blind add. These are
the only normal standalone `REGISTER` ticks.

If the journal proves one exact committed pool/BASE mint but omitted
`base_symbol` or `base_decimals`, run `scan_orca_pools` once with current config.
Require one candidate whose full `pool` and `base[1]` equal the commitment. Copy
only missing `base[0]` and `base[2]`; ignore rank, score, and every other candidate.
Never change pool, mint, allocation, range, or registration state. Journal the
full tuple as `METADATA_RECOVERY`, `next_phase=SIZE`, no mutation, then end.
Missing, duplicate, or contradictory match is `HOLD`; never guess. Native Orca
`get_pool_info` does not supply token decimals. This sole committed-chain rescan
exception cannot select an alternative.

A sizing tick refreshes the committed pool once; require exact orientation,
positive price/tick spacing, and finite nonnegative `risk_state.total_exposure`.
Set `remaining_risk_quote=max(risk_limits.max_position_size_quote-
risk_state.total_exposure,0)` and calculate from canonical wallet displays.
`allow_base_preparation` is a routine lifecycle switch, not a `[CURRENT CONFIG]`
field or separate authorization; its absence there never blocks sizing. Pass
`true` on pre-preparation `SIZE` and `PREPARE`; `false` after confirmed
preparation and for corrected `OPEN` sizing. Every `SIZE` journal carries exact pool/pair,
mints/decimals, allocation, `lower_price`, `upper_price`, feasible amounts, and
next phase. `preparation_required` at `SIZE` journals `next_phase=PREPARE` and ends. Invalid
identity, precision, capital, range, reserve, risk, or zero size blocks this chain.

### Prepare

A material BASE shortfall enters `PREPARE`: one pool refresh, sizing, intent,
liveness check, and market order create; no skill/schema/quote, repeat reads, or
same-tick reconciliation. `PREPARE` and `RECONCILE_PREPARE` carry the full `SIZE`
tuple unchanged plus preparation executor/receipt. Never overwrite a committed
chain. If it becomes unusable, journal explicit `ABANDON -> CLEANUP`; clean its
confirmed prepared non-SOL BASE before any new `SELECT`.

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

After feasible sizing, reuse canonical reads; fetch at most one missing
predecessor detail, then make one pool refresh, sizing call, intent write,
liveness check, and LP create. Do not load a skill/schema, repeat reads, or make
an intermediate refresh. Pure reads keep the canonical baseline valid.

Journal exact pool/pair/mints, preserved pre-BASE/pre-QUOTE wallet displays,
range, floored `base_amount`/`quote_amount`, and configured buffer. Create one
`lp_executor` with top-level exact controller/account and config containing only
the live schema's required fields: current connector/providers, pool, canonical
pair, lower/upper price, numeric `side=3`, exact feasible amounts,
`keep_position=false`, and required `extra_params`. Retain the returned full ID;
its receipt is `submitted` and ends the tick. The next loop tick is
`RECONCILE_OPEN`-only: classify the exact open and end; never start `SELECT`,
`REGISTER`, `SIZE`, `PREPARE`, or `OPEN`. Mandatory risk-reducing exits remain
eligible.

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

Guardian rejection before `manage_executors` runs is
`rejected_before_submit`, never uncertain/backend failure or a learning. End that
tick. Next tick load `orca_lp_operations`; after the indexing lag run close-only
`inspect_orca_positions` for the exact wallet/position/pool. `closed` forbids
stop; `still_active` permits at most one corrected later stop.
Pending/uncertain/unavailable is `HOLD`. A second failure or `404`, including a
second Guardian rejection, quarantines only the exact executor, position, pool,
and attributable capital. Continue independently proven siblings and free
capacity. Release it after fresh evidence proves the exact on-chain position
closed and a refreshed wallet can pass ordinary new-LP sizing; cleanup cannot
keep quarantine active forever.
Never learn from close errors.

After terminal close or abandoned confirmed preparation, block that chain's new
deployment until ordinary cleanup is resolved. On any tick actively starting,
reconciling, retrying, quarantining, or handing off cleanup, load
`solana_inventory_cleanup` exactly once and follow it. It owns the precision-safe
non-SOL spend, protected-SOL formula, exact Order Executor request, one strictly
smaller corrected zero-effect retry, residual quarantine, and terminal manual-
recovery handoff. Cleanup never uses `manage_gateway_swaps`, a separate quote,
token registration, or a blacklist record. A submitted cleanup ends its tick.

The next loop tick is `RECONCILE_CLEANUP`-only: reconcile that executor/mint,
journal the next phase, and end. Do no other lifecycle work. Stay cleanup-related
unless confirmed with none remaining; then record `HOLD` with
`next_phase=SUPERVISE` and resume supervision next tick.

An exhausted cleanup quarantines only its exact residual mint/inventory and
dependent capacity; it never blocks healthy sibling supervision/exits, another
token's cleanup, or independently free capacity. Metrics and journal evidence
must retain the exact on-chain mint, balance/value, full failed executor IDs, and
outcome, and must not claim the wallet is quote-clean.

Choose `STOP` when wind-down proves all exact current-session LP lifecycles
terminal, no submitted/uncertain LP transition, cleanups reconciled, touched
non-SOL BASE at/below dust, protected SOL intact, and no unresolved capital.
HAPI PositionHold/`active_positions` cannot veto. If fresh current-tick evidence
proves these gates, journal `STOP` and stop that tick rather than `HOLD`. Call only
`manage_trading_agent(action="stop_agent", agent_id=<exact current>)`.
Alternatively, after the cleanup skill proves every current-session executor
terminal, no submitted/uncertain mutation, its corrected retry exhausted, and
only explicitly quarantined residual inventory remaining, journal exact
`STOP_MANUAL_RECOVERY` and stop the Agent instead of repeating `HOLD` forever.

## Journal And Response

Loop writes one concise action. Before write, compare every opaque identity to its
structured source; mismatch is corrected locally, and a corrupt old journal value
is never authority. Intent precedes submission with controller, target, bounded
parameters, reason, chain fields, and `written=true`; no post-execution entry.
Generic error-learning guidance does not apply—only the exact preparation
receive-difference blacklist may use `entry_type="learning"`.

End with action, exact identities, mutation attempt/outcome, quarantined
capacity/capital, and next phase. Run once reports intent/reconciliation; dry run
only the conditional action.

## Live Mutation Authority

Live-loop config authorizes `manage_executors(action="create")` for this bounded
deployment chain and `manage_executors(action="stop", keep_position=false)` for
an exact current-session LP on a fresh configured exit, including
`age_minutes >= position_time_limit_minutes`. Every stop is standalone and
reconciled after its result. Authority never extends to dry run,
foreign/unverified executors, or an already quarantined target
without the failed-close recovery contract.

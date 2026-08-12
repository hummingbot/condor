---
name: orca_lp_operations
description: Exact routine and native-executor guide for the Orca LP lifecycle.
when_to_use: Read only when this tick actively inspects or resolves a failed or uncertain close, evaluates its one corrected stop, or handles a genuinely exceptional LP recovery question. Mere quarantine presence and ordinary PREPARE, OPEN, close, cleanup, or wind-down do not trigger this skill.
source: agent:lp_agent_lite
---

# Orca LP Operations

This is an operational reference, not a state machine. The Strategy owns phase
order, admission, and lifecycle decisions. Begin from the exact current
controller, frozen config, committed pool when present, and fresh affected
executor and wallet evidence.

Load this reference because the current tick chose active failed-close recovery,
not merely because a quarantined position exists in the portfolio. Use it once
for that recovery work and do not carry its presence into unrelated sibling
supervision, deployment, normal close, cleanup, or quiet `HOLD` work.

Routine JSON and native executor detail are operational truth. Condor diagnostic
`report_id` and `report_error` fields are human-review metadata only; a reporting
failure never changes or repeats an operation.

## Routine Invocation Guides

Call every routine only through
`manage_routines(action="run", name=<exact name>, agent="lp_agent_lite",
config={...})`. The following tables are exact Config APIs. Never rename a key,
change an enum's case, or substitute a synonym. Optional routine-owned controls
are normally omitted unless current Strategy config explicitly supplies them.
Parse the inner structured result; outer MCP completion is not the routine
outcome. Only an outer `Invalid config:` rejection permits one corrected Config
call because no routine instance or effect started.

### `calculate_lp_requirements`

Use only after selecting one exact pool/range and refreshing native pool and
wallet facts. It is pure calculation and performs no external mutation.

#### Top-Level Config Parameters

<!-- routine-config:calculate_lp_requirements -->
| Config key | Presence | Exact source and use |
|---|---|---|
| `selected_allocation_quote` | required | LLM-selected quote allocation within current limits. |
| `max_amount_quote_per_lp_position` | required | Current per-position hard cap. |
| `remaining_session_quote` | required | Current uncommitted session quote capacity. |
| `remaining_risk_quote` | required | Fresh nonnegative capacity remaining below core `risk_limits.max_position_size_quote`. |
| `capital_headroom_pct` | required | Current configured aggregate headroom. |
| `lp_open_balance_buffer_pct` | required | Current configured per-leg open buffer. |
| `allow_base_preparation` | required | Exact boolean: `true` before preparation; `false` after preparation and for corrected open retry. |
| `current_price` | required | Fresh native QUOTE-per-BASE pool price. |
| `lower_price` | required | Selected lower range price, below current price. |
| `upper_price` | required | Selected upper range price, above current price. |
| `tick_spacing` | required | Fresh native pool tick spacing. |
| `available_base_display` | required | Exact `"0"` when BASE is absent from the native wallet response; otherwise preserve the native four-decimal string and optional uppercase `K` or `M`. |
| `available_quote_display` | required | Exact `"0"` when QUOTE is absent from the native wallet response; otherwise preserve the native four-decimal string and optional uppercase `K` or `M`. |
| `base_decimals` | required | Exact selected BASE mint decimals. |
| `quote_decimals` | required | Exact configured QUOTE mint decimals. |
<!-- /routine-config -->

Require `lower_price < current_price < upper_price` and
`lp_open_balance_buffer_pct <= capital_headroom_pct`. Consume the returned
aligned prices/ticks, floored amounts, buffered requirements, safe wallet bounds,
and `base_shortfall`; never reconstruct them in prose.

### `snapshot_lp_metrics`

Call once before the decision using only already-observed facts. It provides the
exact current-session clock and may write only the loop tick's metrics artifact;
experiments return a preview.

#### Top-Level Config Parameters

<!-- routine-config:snapshot_lp_metrics -->
| Config key | Presence | Exact source and use |
|---|---|---|
| `controller_id` | required | Exact injected current Agent/controller ID. |
| `tick` | required | Exact injected positive tick integer. |
| `session_pnl_quote` | required | Fresh current-session net quote PnL. |
| `quote_balance` | required | Fresh configured QUOTE wallet balance. |
| `sol_balance` | required | Fresh SOL wallet balance used for reserve supervision. |
| `positions` | optional | De-duplicated list using the exact nested schema below. |
| `residuals` | optional | De-duplicated list using the exact nested schema below. |
| `last` | optional | Latest exact current-session lifecycle mutation using the exact nested schema below. |
<!-- /routine-config -->

#### Nested `positions[]` Item Parameters

<!-- routine-config:snapshot_lp_metrics.positions -->
| Config key | Presence | Exact source and use |
|---|---|---|
| `executor_id` | optional | Full executor ID; required by the routine for active, closing, or closed tracked states. |
| `position_address` | required | Exact on-chain position identity; never substitute position mint. |
| `pool_address` | required | Exact Whirlpool address. |
| `state` | required | Canonical output is `active`, `closing`, `closed`, `untracked`, `foreign`, or `ambiguous`; native case/separator aliases described below are accepted and normalized. |
| `age_minutes` | optional | Fresh nonnegative age. |
| `base_amount` | optional | Fresh nonnegative native position BASE amount. |
| `quote_amount` | optional | Fresh nonnegative native position QUOTE amount. |
| `fees_quote` | optional | Fresh finite fees in QUOTE. |
| `pnl_quote` | optional | Fresh finite PnL in QUOTE. |
| `pnl_ratio` | optional | Fresh finite PnL ratio. |
<!-- /routine-config -->

#### Nested `residuals[]` Item Parameters

<!-- routine-config:snapshot_lp_metrics.residuals -->
| Config key | Presence | Exact source and use |
|---|---|---|
| `mint` | required | Exact Solana mint address, never a symbol. |
| `amount` | required | Fresh nonnegative amount. |
| `value_quote` | optional | Fresh nonnegative estimated QUOTE value. |
| `status` | required | Exactly `prepared`, `clean`, `cleanup`, or `unattributed`. |
<!-- /routine-config -->

#### Nested `last` Parameters

<!-- routine-config:snapshot_lp_metrics.last -->
| Config key | Presence | Exact source and use |
|---|---|---|
| `kind` | required | Exactly `register`, `prepare`, `open`, `close`, `cleanup`, or `stop`. |
| `identity` | required | Full exact executor, token, or Agent identity for that mutation. |
| `status` | required | Exactly `rejected_before_submit`, `submitted`, `confirmed`, `uncertain`, `ambiguous`, or `unavailable`. |
| `transaction` | optional | Exact transaction identity when known. |
<!-- /routine-config -->

Position-state normalization is case-insensitive and ignores spaces, hyphens,
and underscores: `RUNNING`, `ACTIVE`, `IN_RANGE`, `BELOW_RANGE`, and
`ABOVE_RANGE` become `active`; `SHUTTING_DOWN` and `CLOSING` become `closing`;
`TERMINATED`, `COMPLETED`, and `CLOSED` become `closed`. Unknown states reject.
Use
`position_address`, never `position_mint`. Residual `mint` is an address, not a
symbol. Optional `last` preserves the latest lifecycle mutation; a preparation
Order Executor is `kind="prepare"`, never `"preparation"`. `HOLD`, `WIND_DOWN`,
reads, metrics, and journal writes are not valid `last.kind` values. Omit `last`
when no exact mutation is known. `last.status` is a classified mutation outcome,
not a raw executor lifecycle status: do not pass `RUNNING`, `SHUTTING_DOWN`, or
`TERMINATED`; omit `last` until the outcome can be classified. Read compact
tuples through their returned column arrays.

Require `status="complete"` and `artifact_write=true` in loop, or
`status="preview"` and `artifact_write=false` in experiments. Metrics failure
blocks only its dependent clock or metric, never an independently proven native
risk-reducing exit.

### `inspect_orca_positions`

Use only after a failed or uncertain close for one already-known exact position.
It has no action selector and never discovers or reconciles an LP open.

#### Top-Level Config Parameters

<!-- routine-config:inspect_orca_positions -->
| Config key | Presence | Exact source and use |
|---|---|---|
| `wallet_address` | required | Exact configured wallet address. |
| `position_address` | required | Exact already-known on-chain position address. |
| `expected_pool_address` | required | Exact expected Whirlpool address. |
| `mutation_started_at` | required | Close mutation-start integer Unix epoch in seconds. |
| `indexing_lag_seconds` | optional | Pass current configured Orca indexing lag. |
| `timestamp_tolerance_seconds` | optional | Routine-owned event tolerance; normally omit. |
| `history_limit` | optional | Routine-owned bounded history size; normally omit. |
| `timeout_seconds` | optional | Routine-owned request timeout; normally omit. |
<!-- /routine-config -->

Consume `close_outcome` exactly: `closed` forbids another close;
`still_active` permits one corrected close no earlier than a later tick;
`pending_index`, `uncertain`, or `unavailable` requires HOLD. One endpoint,
missing coverage, or disagreement never proves closure.

### `register_gateway_token`

Use only for a committed selected pool's non-SOL, non-quote BASE token. Normal
live registration is an unconditional add followed by one exact registry
read-back; it does not check presence first. Normal registration is the external
mutation completing the selection tick, not a separate tick. Pass
`preview=false` on that live call.

#### Top-Level Config Parameters

<!-- routine-config:register_gateway_token -->
| Config key | Presence | Exact source and use |
|---|---|---|
| `network` | optional | Pass exact current `solana-mainnet-beta`; no other literal is accepted. |
| `mint` | required | Exact selected BASE Solana mint address. |
| `symbol` | required | Exact selected BASE symbol preserved from the scanner tuple. |
| `decimals` | required | Exact selected BASE decimals preserved from the scanner tuple. |
| `preview` | optional | Pass `false` for normal live add-and-verify; use `true` only to reconcile an earlier uncertain add. |
| `timeout_seconds` | optional | Routine-owned request timeout; normally omit. |
<!-- /routine-config -->

Do not call it when the exact BASE mint is wrapped SOL or the configured quote
mint. Do not perform a registry-presence check before a normal live call;
re-registering an existing exact token is allowed. Continue only on inner
`status="confirmed"` with exact mint and decimals. A case-only symbol alias may
confirm; use returned `canonical_symbol` thereafter. The confirmed add plus
read-back is final registration reconciliation: end the tick and go directly to
committed sizing next tick without previewing, checking presence, or repeating
registration. Trust it for the unchanged chain unless a later exact Gateway
registry/metadata error invalidates it. Any other metadata conflict is ambiguous,
and an uncertain live add is never blindly repeated.

## Shared Operation Rules

- Dry run is read-only. Run once never starts preparation or a new LP. Loop uses
  the exact `lp_agent_lite.orca_N` controller and one bounded lifecycle decision.
- The controller is top-level mutation authority and a server-side session
  filter. The full executor ID is lifecycle identity. On later ticks, each full
  `id` returned by the exact current-controller filtered search is a
  current-session executor identity; fetch exact detail and require matching
  detail `id` before lifecycle action. Never require the earlier create response.
- Ignore embedded raw/config `controller_id`, including `main`. It is
  non-authoritative metadata. Eight-character prefixes are display-only.
- Older, foreign, and untracked positions are observation-only. Shared wallet
  balances affect feasibility but do not establish position ownership.
- A finalized loop-mode non-SOL/non-QUOTE selection completes registration in
  the same `SELECT_REGISTER` tick. SOL and QUOTE commit read-only. A committed
  continuation does not rescan, compare alternatives, reread selection
  guidance, or fetch unrelated pools. Never fold `PREPARE` into `OPEN`;
  reconcile a mutation and end its tick.
- Use one external mutation phase per tick. The sole exception is a bounded,
  sequential same-phase close batch of independently triggered current-session
  LPs; stop on uncertainty.

## Native Executor Request Guide

For every request, `QUOTE` is the configured quote tuple, `BASE` is the pool's
other token, pair is `<canonical BASE symbol>-<config.quote_token_symbol>`, and
price is QUOTE per BASE. Ordinary `PREPARE`, `OPEN`, and cleanup use their exact
documented requests and let native create perform live schema validation without
a separate schema-only call.

### Preparation and cleanup Order Executor

- top level: exact `controller_id`, `account_name=<config.account_name>`, and
  `executor_type="order_executor"`;
- config: `type="order_executor"`, `connector_name=<config.network>`, canonical
  pair, `execution_strategy="MARKET"`, and `amount` in BASE units;
- preparation: `side=1` to BUY BASE with QUOTE;
- cleanup: `side=2` to SELL BASE for QUOTE.

This is the exclusive swap path. Trust Gateway/Jupiter's internal slippage
protection; do not request a quote, pass a slippage field, or call
`manage_gateway_swaps`. On the checked-out path, `executed_amount_base` repeats
the requested BASE amount and is not received-inventory evidence.

### LP Executor

- top level: exact `controller_id`, `account_name=<config.account_name>`, and
  `executor_type="lp_executor"`;
- config: `type="lp_executor"`, `connector_name=<config.network>`,
  `lp_provider=<config.lp_provider>`, `swap_provider=<config.swap_provider>`,
  canonical pair, exact pool and aligned bounds, `side=3`, calculator-returned
  BASE/QUOTE amounts, and `keep_position=false`.

Retain the full returned ID. In loop mode, a successful LP create receipt ends
the tick; use the next tick's canonical controller search and wallet refresh,
fetching exact detail only if its broad row is insufficient and requiring detail
`id` equality when fetched. One create consumes the configured per-tick LP
deployment quota even when schema or Gateway rejects it.

### LP stop

Call `manage_executors(action="stop", controller_id=<exact current>,
executor_id=<one full executor ID>, keep_position=false)`. Never concatenate
IDs. Refresh that executor and wallet after each stop.

## Lifecycle Safety Notes

### Preparation

Use `calculate_lp_requirements` with `allow_base_preparation=true`. Journal the
committed pool, exact pre-BASE wallet display, requested shortfall, and receive
threshold before one preparation BUY. Do not load this skill, request a separate
schema, repeat a pool or wallet read, or reconcile after create during an
ordinary committed `PREPARE`. Retain the full create-returned executor ID and
end the tick; the successful receipt is `submitted`, not received inventory or
terminal success.

On the next tick, use the canonical controller search and wallet read to
reconcile the full-ID preparation, then size the same committed pool with
`allow_base_preparation=false`. Small receive differences downsize the LP; they
do not trigger failure or a dust top-up. Only a conservatively bounded wallet
difference strictly above `preparation_receive_difference_blacklist_pct`, with
exact exclusive attribution, blacklists the pool and BASE mint. Equality,
ambiguity, or insufficient precision never blacklists.

### Open and retry

Immediately before LP create, require a fresh feasible calculation, capacity,
and the canonical current-tick wallet baseline in the action intent. When no
same-tick wallet mutation occurred, reuse that wallet through submit and do not
make a second precautionary wallet call. Do not load this skill, make a separate
schema-only call, repeat predecessor detail, or make an intermediate wallet
refresh during an ordinary committed `OPEN`. Do not call
`inspect_orca_positions` for an LP open.

Only the Strategy's full `rejected_before_submit` proof permits one corrected
later-tick OPEN. Any position identity, nonzero native actual amount, changed
exact-mint balance, missing baseline, or contradictory evidence forbids retry.
Metrics and Orca Stats never repair failed-open proof, and an unchanged request
is never repeated.

### Close quarantine

Use `inspect_orca_positions` only when a close failed or remains uncertain and
the exact position is known. `still_active` permits at most one corrected stop on
a later tick. Another failure or `404` quarantines only that executor, position,
pool, and attributable capital for manual recovery; healthy siblings and other
available capacity continue.

Fresh proof that the on-chain position is closed plus a wallet able to pass
normal new-LP sizing releases close quarantine. Remaining inventory follows the
ordinary cleanup rule and cannot keep close quarantine active.

### Quote restoration

After terminal close, the Strategy activates ordinary cleanup and loads
`solana_inventory_cleanup` on the cleanup tick. That skill exclusively owns the
precision-safe amount, protected-SOL formula, retry, residual quarantine, and
manual-recovery handoff. Never request a full rounded display balance or use
this failed-close reference as substitute cleanup guidance.

### Wind-down and stop

During `lp_pnl_grace_period_minutes` after an LP's authoritative creation time,
its position PnL and whole-session aggregate PnL are provisional and cannot
trigger a PnL close or wind-down. Zero disables this grace. Non-PnL exits and
otherwise eligible deployment continue. After grace, require fresh PnL evidence;
never reuse a provisional breach.

Wind-down forbids new registration, preparation, and open. Close exact
current-session LPs, reconcile each, and clean each BASE in later admitted
cleanup work. Follow the Strategy's normal clean-stop gate or the cleanup
skill's exact `STOP_MANUAL_RECOVERY` handoff after its corrected retry is
exhausted; do not repeat `HOLD` solely because quarantined residual inventory is
not quote-clean. An external kill requires manual cleanup; a later session never
adopts it.

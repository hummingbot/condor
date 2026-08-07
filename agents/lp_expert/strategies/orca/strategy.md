---
name: orca
description: LLM-led multi-executor Orca CLMM strategy using separate compact portfolio and pool evidence with exact bounded transitions.
agent_key: null
skills: []
default_config:
  execution_mode: dry_run
  frequency_sec: 60
  account_name: master_account
  default_risk_posture: balanced
  total_amount_quote: 10
  max_open_executors: 3
  max_slot_deployments_per_tick: 1
  candidate_scan_limit: 3
  min_quote_per_executor: 3
  max_quote_per_executor: 5
  max_slippage_pct: 1
  min_sol_reserve: 0.1
  use_existing_base_inventory: true
  residual_base_dust_quote: 0.01
  minimum_range_half_width_pct: 0.5
  maximum_range_half_width_pct: 20
  rebalance_threshold_pct: 1
  executor_max_age_minutes: 1440
  executor_take_profit_net_pnl_ratio: 0.05
  executor_stop_loss_net_pnl_ratio: 0.05
  session_max_age_minutes: 1440
  session_take_profit_net_pnl_ratio: 0.05
  session_stop_loss_net_pnl_ratio: 0.05
  risk_limits:
    max_position_size_quote: 10
    max_open_executors: 3
    max_drawdown_pct: -1
    shutdown_drawdown_pct: -1
default_trading_context: ''
created_by: 0
created_at: '2026-07-29T00:00:00Z'
---

# Orca LP Strategy

Operate Orca Whirlpools with canonical USDC quote under the exact current
`lp_expert.orca_<N>` loop controller or `lp_expert.orca_e<N>` experiment
controller. Choose one portfolio action per tick: `DEPLOY`, `CLOSE`, or `HOLD`.
`CLOSE` includes reconciliation and residual cleanup continued from an earlier
close.

## Resolve Mode and Authority

Infer mode once from the injected prompt:

- `🧪 DRY RUN mode`: observation only; never mutate or journal.
- `[EXECUTION MODE — RUN ONCE]` without dry-run markers: one live `_eN` tick,
  no journal, and no assumed follow-up tick.
- Neither marker: live loop `_N`; write one journal entry as described below.

Controller form and mode must agree. If the markers conflict, required current
config is incomplete, or the exact controller cannot be validated, HOLD.

The current frozen config and hard routine validation are authoritative. Context
and skill guidance may never widen its capital, executor, pool, range, reserve,
slippage, ownership, or mutation limits.

## Risk Posture

Resolve `steady`, `balanced`, `opportunistic`, or `exploratory` from explicit
session context. Otherwise use configured `default_risk_posture`. Ambiguous or
contradictory language falls back to the configured default or HOLD. State the
posture in the decision. It changes comparison judgment only.

## Skill Routing

The ordinary complete portfolio-and-scan paths are self-contained and require
no skill read. Read at most the one relevant playbook:

- `lp_pool_review` when valid candidates are close, unusual, contradictory, or
  need an evidence-based rank override or deeper diversification judgment;
- `lp_range_and_inventory` when strategic range intent, inventory attribution,
  or a post-swap replan is genuinely ambiguous;
- `lp_portfolio_supervision` when several executors create a genuine ownership,
  capacity, lifecycle, or diversification ambiguity;
- `lp_close_and_recovery` when closing, reconciling a prior close, handling an
  uncertain stop/swap, or restoring an exact residual.

Skills interpret already-valid evidence. They never make missing evidence
sufficient, calculate hard limits, change retry permission, or authorize a
mutation.

## Portfolio-Then-Scan Tick

1. Resolve execution mode and exact controller.
2. Run `lp_snapshot` exactly once with the exact current controller and active
   Strategy. Do not duplicate its broad portfolio, executor, or balance reads
   with other tools.
3. Require its structured result and preserve `report_id` or `report_error`.
   Incomplete ownership, pagination, config, or lifecycle evidence blocks the
   dependent action.
4. Inspect every current-controller executor and apply this priority:
   - unresolved current-session mutation reconciliation;
   - post-close verification due from an earlier tick;
   - global stop latch and mandatory per-executor close triggers;
   - healthy portfolio supervision;
   - one or more serialized deployments when capacity is clean.
   When an unresolved operation contains `reconcile`, run its named routine
   once with `reconcile.config` unchanged. Do not scan, reconstruct missing
   inputs, or consult a report first.
   When it contains `restore`, journal the exact restoration intent in loop
   mode and run `restore.routine` once with `restore.config` unchanged. A
   confirmed unconsumed preparation blocks scanning until that restoration is
   confirmed.
5. If and only if `scan_allowed=true` and deployment remains under
   consideration, run `lp_pool_scan` exactly once for the same controller and
   tick. Require complete source coverage and preserve its report metadata.
6. Choose `DEPLOY`, `CLOSE`, or `HOLD`.
7. In loop mode, write exactly one journal entry. For a mutation it is the
   pre-mutation intent with exact controller, operation IDs, targets, ordered
   transitions, parameters, and reason. For HOLD it is the final reason. In
   run-once, put the same information in the experiment response.
8. Execute only the chosen path. Stop all affected-scope mutation on uncertainty.
9. End with posture, action, exact identities, mutation/reconciliation state,
   quarantined capacity, next permitted transition, and ordered routine report
   metadata.

Never write cross-session learnings. Never call journal tools in dry-run or
run-once.

## Mutation Outcome Rules

Use the routine's explicit outcome:

- `rejected_before_submit`: no mutation occurred; a corrected future attempt
  requires fresh validation;
- `submitted`: one external identity exists but the required lifecycle is not
  yet confirmed;
- `confirmed`: the declared effect is authoritatively verified;
- `uncertain`, `ambiguous`, or `unavailable`: quarantine affected authority,
  HOLD, and never resubmit blindly.

A journaled intent is not proof of submission. A report is not a receipt. One
confirmed transaction is not terminal executor lifecycle evidence unless the
routine explicitly proves both.

## DEPLOY

DEPLOY is allowed only when the snapshot and pool scan prove:

- no session stop latch, unresolved mutation, pending cleanup, foreign live
  wallet scope, or conflicting wallet mutation;
- fewer than the configured maximum current-controller executors;
- clean aggregate capital and at least one available virtual slot;
- no active or reconciling executor in the selected pool;
- at least one technically complete registered-token Orca candidate and
  returned selection bounds.

The active frozen config controls executor capacity, the ranked scan universe,
and the maximum deployments per tick. The pool-scan transport ceiling controls
only how many top-ranked candidates are returned. Never exceed the second value
in `selection_constraints.deployments`.

1. Compare the compact returned candidate prefix and select an ordered set of
   distinct candidates, allocations, and strategic range half-widths inside the
   returned bounds, or HOLD. The sum of selected allocations must fit the
   returned remaining portfolio budget. Candidate `base` is
   `[symbol, mint, decimals]`;
   `mcda` is `[fee_productivity, recent_activity, price_stability,
   liquidity_depth, execution_simplicity]`. Compare `rank`, `score`, `tvl`,
   `yield_24h`, `yield_floor_h`, `accel_1h`, `turnover_24h`, `move_24h`,
   source coverage, and current portfolio overlap. `transport_limited=true`
   means lower-ranked valid pools were omitted for response size; it does not
   make the returned prefix incomplete.
2. In loop mode, journal every selected candidate and the ordered swap/create
   operation IDs before the first mutation. Derive them from the exact current
   controller, tick, full pool address, and transition using the Agent's
   operation-ID contract; never copy the dotted controller ID verbatim.
3. For each selection in order, call `lp_order_request` with no
   `swap_executor_id`. The routine derives token identity, base amount, quote
   cap, slippage, and the deterministic selection plan. When
   `use_existing_base_inventory=true`, it may authorize only currently
   available base, capped at the selected LP requirement. For SOL it first
   subtracts `min_sol_reserve`. A full allocation returns
   `status="confirmed"`, `mutation=false`, and `deployment_input` without an
   order executor. A partial allocation reduces only the exact preparation
   shortfall. Otherwise the routine returns either a hard outcome or
   `status="ready"` with one exact `order_executor` `executor_request`. It does
   not submit an executor, perform a swap, or transfer funds.
4. For `ready`, pass that entire `executor_request` unchanged to
   `manage_executors` once. Do not reconstruct, omit, or override either
   controller ID. Require one returned executor ID. A tool error, cancellation,
   or response without one ID is uncertain: stop mutation and never resubmit.
5. For a `ready` preparation, call `lp_order_request` again with only the exact
   current `controller_id`,
   original `operation_id`, and returned `swap_executor_id`. Its durable
   current-session receipt restores the immutable request. It verifies exact
   executor ownership/config, extracts one transaction identity, derives actual
   input/output from the finalized Solana transaction's bound-wallet and
   token-mint balance deltas, applies the quote/slippage/attribution guards, and
   emits its own reconciliation report. Continue only from its exact confirmed
   attributed receipt; never query the Gateway swap ledger for this native
   order-executor transaction.
   Before emitting the order request, the routine requires available quote to
   cover both the quoted preparation input and the later planned LP quote leg.
   Field-specific failures report required and available amounts.
6. If preparation remains submitted, end the deployment chain for this tick.
   On the following tick, reconcile it first from snapshot `reconcile.config`;
   once confirmed, `lp_create` may proceed in that same second tick because it
   freshly revalidates native schema, executor capacity, exposure, pool
   occupancy, and balances. If `same_tick_lp_create_allowed=false` on the
   original admission, do not create in that original tick. Run-once
   preparation is rejected before submission when it would require a follow-up.
7. For either a confirmed swap receipt or a confirmed no-swap wallet
   allocation, use its exact `deployment_input` as candidate, allocation,
   range, and preparation-ID authority. Add the current tick and a new create
   operation ID, then call `lp_create` without `lp_executor_id`. It combines
   authorized existing base with only exact finalized swap output, reconstructs
   the selection plan, refreshes price and token registration, replans, and
   revalidates schema, capacity, exposure, pool occupancy, reserve, and
   field-specific balances. `status="ready"` includes one exact `lp_executor`
   `executor_request`; the routine does not create it. If a legacy confirmed
   receipt lacks `deployment_input`, HOLD rather than rebuilding it from a
   report.
8. Pass that entire LP `executor_request` unchanged to `manage_executors` once,
   require one returned executor ID, then call `lp_create` again with only the
   current `controller_id`, original `operation_id`, and returned
   `lp_executor_id`. The admitted receipt restores the complete frozen request;
   reconciliation must not refresh price or rebuild the plan. Continue only
   after exact executor detail is confirmed. Use one exact
   `manage_executors(action="search", executor_id=...)` only when the routine
   says the known ID remains submitted; never broaden the search.
   Missing or not-yet-initialized detail remains `submitted` for later exact-ID
   reconciliation. Only the routine's explicit immutable-field `conflict`
   classification requires manual review.
9. Start the next selection only after the prior create is confirmed. A
   submitted, uncertain, ambiguous, manual-review, or rejected chain ends
   deployment for the tick.
10. A confirmed preparation BUY whose create was definitely rejected remains
    attributed inventory. On the next snapshot it appears as
    `phase="confirmed_unconsumed"` with exact `prepared_inventory` and a
    `restore` capsule. Run that capsule unchanged before scanning. Only actual
    preparation-swap output is restored; pre-existing wallet base is never
    mislabeled as residual inventory.

A later tick may reassess an alternate from a fresh snapshot and scan.

### Exact routine inputs

Use only these fields; do not copy derived plan or technical values into a
routine request:

- `lp_snapshot`: `controller_id`, `tick`, and `prior_closes`.
- `lp_pool_scan`: `controller_id` and `tick`.
- preparation `lp_order_request`: `controller_id`, `operation_id`,
  `reason="inventory_preparation"`, the unchanged selected `candidate`,
  `amount_quote`, and `range_half_width_pct`.
- reconciliation `lp_order_request`: only `controller_id`, the same
  `operation_id`, and the exact returned `swap_executor_id`, preferably copied
  unchanged from snapshot `reconcile.config`.
- new `lp_create` admission: `controller_id`, `tick`, `operation_id`, the same
  unchanged `candidate`, the same `amount_quote` and
  `range_half_width_pct`, and `preparation_operation_id`.
- reconciliation `lp_create`: only `controller_id`, the same `operation_id`,
  and the exact returned `lp_executor_id`, preferably copied unchanged from
  snapshot `reconcile.config`.

Operation IDs must satisfy the Agent's dynamic safe-ID contract before they are
journaled or sent to either routine. Do not hardcode a session, tick, pool,
executor, or symbol into an operation-ID template.

For the compact snapshot bounds,
`selection_constraints.allocation_quote` is `[minimum, maximum,
remaining_portfolio_budget]`, `range_half_width_pct` is `[minimum, maximum]`,
and `deployments` is `[configured, available_this_tick]`.

Do not send `candidate_limit`, `plan`, `plan_digest`, calculated base amount,
quote cap, token identity fields, or slippage for preparation. Those values are
derived and verified inside the routines from current frozen config.

## CLOSE — Stop Tick

Every hard close target returned by `lp_snapshot` must be included. A global
session stop latch closes the whole current-controller portfolio. Order multiple
targets explicitly and keep their identities independent.

1. Read `lp_close_and_recovery`.
2. In loop mode, journal one CLOSE intent containing the ordered exact executor,
   position, pool, `keep_position=false`, and later-tick verification identities.
3. For each target, call
   `manage_executors(action="stop", controller_id=<exact current controller>,
   executor_id=..., keep_position=false)` once.
   Use an exact executor-ID search only when needed to reconcile the stop before
   proceeding to another mandatory target.
4. Never issue another stop after possible submission.
5. Never call `lp_order_request` for close residuals on this tick. Record the
   exact stop result and that post-close verification is due on the following
   tick.

Uncertainty stops conflicting mutation. Read-only supervision and safely ordered
mandatory exits may continue only when exact independent authority is proven.

## CLOSE — Following-Tick Residual Verification

The first tick after a stop prioritizes that executor's verification before any
deployment. The initial `lp_snapshot` must tie the evidence to one exact prior
current-controller executor, position, pool, base mint and decimals, stop
evidence, and native close-out state.

- If the executor or native close-out remains pending, choose CLOSE and perform
  read-only reconciliation. Do not swap.
- If exact evidence proves no material attributable base residual, or only
  configured economic dust remains, record the evidence. Capacity may be
  released, but do not deploy until a later fresh tick.
- If exact evidence identifies one material attributable residual because native
  restoration failed, was omitted, or reported success despite leaving that
  residual, derive a new cleanup operation ID from the exact current controller,
  tick, and executor using the Agent's operation-ID contract, then call
  `lp_order_request` with reason `post_close_residual_cleanup`. On `ready`,
  submit its exact `order_executor` request once through `manage_executors`,
  then call `lp_order_request` again with only the controller, operation ID,
  and returned `swap_executor_id`.
- Continue only from a confirmed cleanup receipt with finalized Solana
  wallet/mint balance-delta amounts and refreshed scoped evidence.
  Release capacity only when the routine explicitly proves cleanup completion.
- Broad wallet balance, several possible executors, conflicting close evidence,
  or uncertain cleanup causes manual quarantine with no retry.

Executors sharing a base mint remain isolated.
Never sell the wallet's total base balance or another executor's attributed inventory.

Run-once has no following Agent tick. A run-once stop that cannot prove clean
native restoration before the response ends must require manual post-close
verification; it may not perform same-tick residual cleanup.

## HOLD and Healthy Supervision

Choose HOLD when no mutation has better valid evidence or a hard gate blocks the
dependent action. A healthy active executor is not a reason to duplicate broad
reads. Use snapshot lifecycle, in-range state, range distance, fees/net yield,
inventory drift, pool-quality change, and portfolio diversification to supervise
non-triggered executors.

HOLD is mandatory for incomplete candidate coverage, stale or contradictory
exact evidence, invalid schema, foreign ownership, overlapping live wallet
scope, insufficient reserve/capital, same-pool use, unresolved mutation,
ambiguous attribution, or an action outside this Strategy.

## Human-Review Trace

Every `lp_snapshot`, `lp_pool_scan`, `lp_order_request`, and `lp_create`
invocation produces one routine-specific report covering sanitized input,
structured output, ordered debug stages, timing, and error/uncertainty
classification. The snapshot report retains full portfolio evidence; the
pool-scan report retains the full ranked scan even when its model result omits
lower-ranked candidates. Preserve each `report_id`/`report_error` in execution
order.

Report failure is diagnostic only. It never changes trading truth, permits a
retry, or substitutes for the snapshot, durable swap receipt, executor result,
or exact native lifecycle evidence.

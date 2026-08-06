---
name: orca
description: LLM-led multi-executor Orca CLMM strategy using one compact snapshot and exact bounded transitions.
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
  min_quote_per_executor: 3
  max_quote_per_executor: 5
  max_slippage_pct: 1
  min_sol_reserve: 0.05
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

The ordinary complete-snapshot paths are self-contained and require no skill
read. Read at most the one relevant playbook:

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

## One-Snapshot Tick

1. Resolve execution mode and exact controller.
2. Run `lp_snapshot` exactly once with the exact current controller and active
   Strategy. Do not duplicate its broad portfolio, executor, pool, or balance
   reads with other tools.
3. Require its structured result and preserve `report_id` or `report_error`.
   Incomplete ownership, pagination, source coverage, config, or lifecycle
   evidence blocks the dependent action.
4. Inspect every current-controller executor and apply this priority:
   - unresolved current-session mutation reconciliation;
   - post-close verification due from an earlier tick;
   - global stop latch and mandatory per-executor close triggers;
   - healthy portfolio supervision;
   - one new deployment when capacity is clean.
5. Choose `DEPLOY`, `CLOSE`, or `HOLD`.
6. In loop mode, write exactly one journal entry. For a mutation it is the
   pre-mutation intent with exact controller, operation IDs, targets, ordered
   transitions, parameters, and reason. For HOLD it is the final reason. In
   run-once, put the same information in the experiment response.
7. Execute only the chosen path. Stop all affected-scope mutation on uncertainty.
8. End with posture, action, exact identities, mutation/reconciliation state,
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

DEPLOY is allowed only when the snapshot proves:

- no session stop latch, unresolved mutation, pending cleanup, foreign live
  wallet scope, or conflicting wallet mutation;
- fewer than the configured maximum current-controller executors;
- clean aggregate capital and one available virtual slot;
- no active or reconciling executor in the selected pool;
- one technically complete registered-token Orca candidate and valid bounded
  range/inventory plan.

The shipped portfolio may hold three executors but may prepare and create only
one new executor per tick. Do not treat this cap as a quota.

1. Compare the compact candidate set and select one candidate or HOLD. The LLM
   owns the candidate, allocation, and strategic range intent inside returned
   limits.
2. In loop mode, journal the exact candidate, plan identity, swap operation ID
   when needed, and create operation ID before the first mutation.
3. If the plan requires inventory preparation, call `lp_swap` once for the exact
   bounded Jupiter BUY. Continue only from its exact confirmed attributed
   receipt. `submitted` or any uncertain state ends mutation for this tick.
4. Call `lp_create` once with the selected snapshot evidence and, when
   applicable, the exact confirmed preparation receipt. It refreshes technical
   feasibility, schema, capacity, exposure, pool occupancy, and balances before
   one create.
5. If create reconciliation needs native evidence, use one exact
   `manage_executors(action="search", executor_id=...)` call for the returned
   identity. Never broaden the search or inspect residual-position summaries.
6. A confirmed preparation BUY whose create was definitely rejected remains
   attributed inventory. Leave it quarantined for an exact restoration path;
   do not prepare another candidate in the same tick.

Do not call a second candidate after deterministic rejection. A later tick may
reassess an alternate from a fresh snapshot.

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
5. Never call `lp_swap` for close residuals on this tick. Record the exact stop
   result and that post-close verification is due on the following tick.

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
  residual, call `lp_swap` once with a new operation ID and reason
  `post_close_residual_cleanup` to SELL exactly that amount to USDC.
- Continue only from a confirmed cleanup receipt and refreshed scoped evidence.
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

Every `lp_snapshot`, `lp_swap`, and `lp_create` invocation produces one
routine-specific report covering sanitized input, compact output, ordered debug
stages, timing, and error/uncertainty classification. Preserve each
`report_id`/`report_error` in execution order.

Report failure is diagnostic only. It never changes trading truth, permits a
retry, or substitutes for the snapshot, durable swap receipt, executor result,
or exact native lifecycle evidence.

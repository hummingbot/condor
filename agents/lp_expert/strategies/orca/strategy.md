---
name: orca
description: LLM-led multi-executor Orca CLMM strategy using full-universe MCDA evidence and native lifecycle tools.
agent_key: null
skills: []
default_config:
  execution_mode: dry_run
  frequency_sec: 60
  account_name: master_account
  default_risk_posture: balanced
  total_amount_quote: 10
  max_open_executors: 2
  max_slot_deployments_per_tick: 2
  min_quote_per_executor: 5
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
  onchain_reconcile_before_seconds: 15
  onchain_reconcile_after_seconds: 180
  onchain_reconcile_signature_limit: 100
  onchain_reconcile_rpc_timeout_seconds: 15
  risk_limits:
    max_position_size_quote: 10
    max_open_executors: 2
    max_drawdown_pct: -1
    shutdown_drawdown_pct: -1
default_trading_context: ''
created_by: 0
created_at: '2026-07-29T00:00:00Z'
---

# Orca LP Strategy

Operate Orca Whirlpools under controller `lp_expert.orca_<N>`. Each tick chooses
exactly one portfolio action: `DEPLOY`, `CLOSE`, or `HOLD`. A live DEPLOY or
CLOSE action may handle multiple executors through serialized per-executor
transition chains.

## Resolve Execution Mode

Infer mode once from the injected prompt:

- `🧪 DRY RUN mode`: observation only; never mutate or journal.
- `[EXECUTION MODE — RUN ONCE]`: live single-tick `_eN`; never journal. Start a
  transition only when every required safety guard can complete or reconcile it
  in this tick.
- Neither marker: live loop `_N`; journal before the first mutation.

The `_eN` suffix does not mean read-only. If the prompt and controller form
conflict, HOLD.

## Orca Mutation Authorization

The owner-authorized exception to the generic
`Trade ONLY via manage_executors(action="create")` rule is required for this
Strategy. In live loop or run-once mode, do not treat that generic line as
blocking these exact Strategy-declared calls:

1. `gateway_swap(action="ensure_token")` for the selected pool's exact base-token
   mint, symbol, and decimals from the current complete scan;
2. `gateway_swap(action="execute")` for the bounded preparation BUY or exact
   attributed fallback SELL already selected and capped below;
3. `lp_create_guard` for the resulting exact planned LP create;
4. `manage_executors(action="stop", keep_position=false)` for an exact
   current-controller close.

Scope, quote, recover, and status through `gateway_swap` are authorized parts of
the same path. This is not broad permission to run mutating routines: direct
executor create, direct Gateway tools, direct orders, foreign routines, bulk
token registration, every other Gateway configuration change, and any mutation
in dry-run remain prohibited.

## Config Admission

Before any mutation, require all configured fields and enforce:

```text
total_amount_quote > 0
max_open_executors is a positive integer
max_slot_deployments_per_tick is a positive integer
0 < min_quote_per_executor <= max_quote_per_executor <= total_amount_quote
max_open_executors * min_quote_per_executor <= total_amount_quote
risk_limits.max_position_size_quote >= total_amount_quote
risk_limits.max_open_executors >= max_open_executors
minimum_range_half_width_pct <= range_half_width_pct
range_half_width_pct <= maximum_range_half_width_pct
0 < rebalance_threshold_pct < 100
executor_max_age_minutes is a positive integer
executor_take_profit_net_pnl_ratio > 0
executor_stop_loss_net_pnl_ratio > 0
onchain_reconcile_before_seconds and onchain_reconcile_after_seconds are positive
onchain_reconcile_before_seconds + onchain_reconcile_after_seconds <= 900
1 <= onchain_reconcile_signature_limit <= 1000
1 <= onchain_reconcile_rpc_timeout_seconds <= 30
```

The four sizing values in frontmatter are defaults, not fixed trading rules. Also
require finite positive per-executor and session limits, bounded slippage, the
configured SOL reserve, and the exact account/network/default-wallet binding returned by
`gateway_swap(action="scope", account_name=<configured account_name>,
network="solana-mainnet-beta")`. `lp_create_guard` must successfully retrieve and
validate the live `lp_executor` schema immediately before create. The routines
verify the configured server internally. Config or identity uncertainty means
HOLD.

## Resolve Risk Posture

Read `[SESSION CONTEXT]`:

- exact `steady`, `balanced`, `opportunistic`, or `exploratory` selects it;
- safer/conservative/lower risk means `steady`;
- balanced/neutral means `balanced`;
- more risky/aggressive/higher risk means `opportunistic`;
- maximum risk/speculative means `exploratory`.

With no risk instruction, use configured `default_risk_posture`. If instructions
conflict or are unclear, use the configured default or HOLD. State the active
posture in the decision. It changes evidence emphasis only, never hard limits.

## Skill Routing

The ordinary DEPLOY and healthy-supervision paths below are self-contained: do
not read a skill before following them. The generic skill-catalog instruction to
read before a known flow does not override this Strategy-specific routing.

Read `orca_venue_intelligence`, `lp_pool_review`, or `lp_range_and_inventory`
only when routine evidence is missing, stale, contradictory, or technically
unfamiliar. Read `lp_portfolio_supervision` only for a genuine ownership,
lifecycle, capacity, or multi-executor ambiguity after exact executor evidence.
Read `hummingbot_mcp_operations`, `hummingbot_api_contracts`, or
`gateway_dex_operations` only for an unfamiliar action/result or recovery. Read
`lp_close_and_recovery` for CLOSE, terminal reconciliation, or residual
restoration. Read a companion file only when its exact detail is needed.

## Verifiable Routine Trace

Every `orca_pool_scan`, `clmm_position_plan`, `lp_portfolio_limits`,
`gateway_swap`, `solana_transaction_reconcile`, and `lp_create_guard` result
must contain `report_id` and `report_error`. Keep an ordered list of these values
in the final tick response beside the corresponding action, pool, operation, or
executor identity. A missing report with a stated `report_error` is diagnostic
only: preserve the underlying routine result, never retry a mutation to obtain
a report, and never use report content as current trading authority.

## LP Evidence Semantics

- Exact current-controller executor detail is the authority for an active LP:
  require its lifecycle state, expected config identity, pool, and embedded
  on-chain position address.
- Preloaded executor state is sufficient for active count and initial capacity
  assessment. Fetch at most one exact executor detail per ordinary decision when
  lifecycle, range, or position fields are needed.
- `positions_summary` is not an active-LP inventory view. It reports residual
  positions held outside active executors, usually relevant after stop/close.
- A `RUNNING` LP executor with its expected on-chain position plus an empty
  `positions_summary` is healthy and not contradictory. It must not block the
  remaining configured executor capacity.
- A genuine contradiction means incompatible exact evidence for the same
  executor, controller, config, receipt, or on-chain position—not disagreement
  between an active executor and an empty held-position summary.
- Clean capacity comes from current-controller nonterminal executor count,
  remaining configured capital, and unresolved mutation state. `lp_create_guard`
  is the final authority immediately before each create.

## Each Tick

Never call `consult`. Apply the Agent's one-shot, silent grouped-preload rule.
Do not retry or discuss the group result. If this decision needs an unloaded
surface, target only that exact tool or routine and treat it as missing only
after its targeted discovery or direct call fails. An available read-only tool
may replace a preferred observation only when it provides equivalent
current-session evidence. Missing evidence blocks only the dependent action or
slot, not the whole portfolio. Never substitute a mutation or journal surface:
its exact declared tool or routine and normal guards are still required.

1. Resolve execution mode, then validate current controller and frozen config.
2. If a recent mutation result is uncertain, reconcile that exact action first.
   A journal entry marked as intent is not proof of submission. In a live loop,
   call `gateway_swap(action="recover")` with the exact candidate operation
   identity plus its original amount, slippage, maximum quote input or attributed
   base input: `not_submitted` or `rejected` with `mutation=false` releases that
   operation; `confirmed` supplies its receipt; `pending` requires status; and
   `uncertain` or `manual_review` advances to the exact finalized-chain fallback
   below before quarantine. Submit nothing else against that capital meanwhile.
   Use `solana_transaction_reconcile` only after the native/Gateway path remains
   uncertain because of a timeout, cancellation, transport error, missing hash,
   or unknown result. Require the original journal/receipt timestamp, wallet,
   provider/venue program, pool or token/position accounts, and bounded
   wallet-owned asset deltas. `confirmed` with exactly one match supplies a
   finalized transaction hash and exact chain deltas; continue only after the
   relevant Gateway/executor lifecycle is refreshed. `not_found`, `ambiguous`,
   `unavailable`, truncated history, or contradictory evidence quarantines that
   operation and never authorizes a retry.
   When `confirmed` recovers a preparation BUY whose executor was never created,
   finish that chain before normal deployment: refresh the relevant same-base
   candidate evidence and replan using the receipt's exact `output_amount` as
   `attributed_base_amount` and exact `input_amount` as preparation spend. If no
   suitable same-base candidate remains or the refreshed plan is invalid, quote
   and execute one SELL with a new operation ID for exactly that recovered
   `output_amount`, confirm restoration to USDC, then release the slot. Never
   submit another BUY for already recovered inventory.
3. Use preloaded current-controller executors first, then call
   `lp_portfolio_limits(controller_id=<current controller>)` exactly once even
   when none is active. Require `status=complete` and preserve its report
   metadata. `session.stop_latched=true` is an immediate portfolio-wide
   wind-down: do not scan or deploy, and CLOSE every ID in
   `close_required_executor_ids`. Per-executor triggers are also hard CLOSE
   targets. Every ID in `reconcile_required_executor_ids` is already closing,
   swapping, or failed; reconcile it without another stop call. Do not make a
   second search merely to recalculate session/executor age or net PnL. Fetch one exact
   `manage_executors(action="search", executor_id=...)` only when the decision
   needs lifecycle, range, config, or on-chain position detail. Do not query
   `positions_summary` while supervising a healthy active LP.
4. Use only the routine's exact session-age and controller-scoped cumulative
   net-PnL classification for configured session limits. If it is incomplete,
   do not deploy.
5. Inspect every owned executor before choosing one action.
6. Choose `DEPLOY`, `CLOSE`, or `HOLD`.
7. In a live loop, call `trading_agent_journal_write` exactly once with the
   injected current tick. For DEPLOY or CLOSE, that one entry is the compact
   pre-mutation intent; do not write a second outcome entry. It must contain a
   distinct Gateway operation ID for every candidate, controller, wallet/account,
   exact target, ordered transitions, parameters, and reason. For HOLD, write the
   final reason once. In run-once, put the same intent in the response snapshot
   instead of calling the journal.
8. Execute the transitions in order. Continue only after confirmation. Stop the
   chain immediately on uncertainty.
9. End with the active posture, action, exact identities, reconciliation state,
   and ordered routine report trace.

Never write a learning entry. Never call journal tools in dry-run or run-once.

## Per-Executor Exit Limits

The three `executor_*` settings apply independently to every active
current-controller LP executor:

- `executor_max_age_minutes` triggers when age measured from the executor's
  authoritative creation `timestamp` reaches the configured value;
- take profit triggers when `net_pnl_pct` is greater than or equal to
  `executor_take_profit_net_pnl_ratio`;
- stop loss triggers when `net_pnl_pct` is less than or equal to the negative
  `executor_stop_loss_net_pnl_ratio`.

These are hard Agent-supervised close triggers checked once per tick through
`lp_portfolio_limits`. They do not latch the session stop or close unrelated
executors; clean capacity may be reused on a later tick after exact close and
inventory reconciliation. A simultaneous session-level trigger takes precedence
and closes the whole portfolio.

The routine separates `close_required_executor_ids` from
`reconcile_required_executor_ids`. Never issue another stop for an executor
already in `CLOSING`, `SWAPPING`, or `FAILED`; reconcile its current lifecycle.

The current native `lp_executor` schema does not contain time-limit, stop-loss,
take-profit, or triple-barrier fields. Never add the `executor_*` settings to
`executor_config`, guess native aliases, or bypass live schema validation.

## DEPLOY

Deployment is allowed only when capacity is clean and no session stop is latched.

### Canonical Deployment Calls

Use these exact routine config keys directly. Never list or describe a known
routine first, guess an alias, or read a skill for this ordinary path:

```text
gateway_swap(action="scope",
  controller_id, account_name, network="solana-mainnet-beta")

gateway_swap(action="ensure_token",
  controller_id, account_name, network="solana-mainnet-beta",
  token_address=<selected token_a mint>,
  token_symbol=<selected token_a symbol>,
  token_decimals=<selected token_a decimals>,
  token_name=<selected token_a symbol>)

clmm_position_plan(
  pool_address, base_symbol, base_mint, base_decimals, current_price,
  tick_spacing, amount_quote, range_half_width_pct,
  minimum_range_half_width_pct, maximum_range_half_width_pct,
  rebalance_threshold_pct, max_slippage_pct,
  attributed_base_amount=<omit before BUY; confirmed output_amount after BUY>)

gateway_swap(action="quote",
  controller_id, account_name, network="solana-mainnet-beta",
  connector="jupiter", trading_pair="<BASE>-USDC", side="BUY",
  amount=<planned base_shortfall>, slippage_pct)

gateway_swap(action="execute",
  controller_id, account_name, network="solana-mainnet-beta",
  connector="jupiter", operation_id, wallet_address,
  trading_pair="<BASE>-USDC", side="BUY",
  amount=<planned base_shortfall>, slippage_pct,
  max_quote_input=<planner max_usdc_for_preparation_swap>,
  reserve_token="SOL", minimum_reserve_amount=<configured min_sol_reserve>)

gateway_swap(action="recover",
  controller_id, account_name, network="solana-mainnet-beta",
  connector="jupiter", operation_id, wallet_address,
  trading_pair="<BASE>-USDC", side="BUY",
  amount=<original planned base_shortfall>, slippage_pct,
  max_quote_input=<original planner cap>)

gateway_swap(action="status",
  controller_id, account_name, network="solana-mainnet-beta",
  connector="jupiter", operation_id, wallet_address, transaction_hash,
  trading_pair="<BASE>-USDC", side="BUY",
  amount=<original planned base_shortfall>, slippage_pct,
  max_quote_input=<original planner cap>)

solana_transaction_reconcile(
  controller_id, operation_id, account_name, network="solana-mainnet-beta",
  wallet_address, operation_kind="swap",
  window_start=<submission time - configured onchain_reconcile_before_seconds>,
  window_end=<submission time + configured onchain_reconcile_after_seconds>,
  transaction_hash=<known hash or omit>,
  signature_limit=<configured onchain_reconcile_signature_limit>,
  rpc_timeout_seconds=<configured onchain_reconcile_rpc_timeout_seconds>,
  required_accounts=[<base mint>, <USDC mint>],
  required_program_ids=["JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"],
  asset_changes=[
    {mint: <USDC mint>, decimals: 6, direction: "decrease",
     minimum_amount: <quote floor>, maximum_amount: <planner quote cap>},
    {mint: <base mint>, decimals: <scan decimals>, direction: "increase",
     minimum_amount: <slippage floor>, maximum_amount: <planned amount>}
  ])

lp_create_guard(
  controller_id, operation_id=<candidate create operation>, wallet_address,
  attributed_base_amount=<confirmed output_amount>,
  preparation_quote_spent=<confirmed input_amount>,
  plan=<refreshed complete planner result>,
  plan_digest=<refreshed planner plan_digest>)
```

The only valid native-reserve key is `minimum_reserve_amount`; never try
`min_reserve`, `minimum_reserve`, or `reserve_amount`. A Pydantic config rejection
means the routine did not run and no report or mutation exists. Correct one
deterministic pre-submission typo only from the canonical call above—never by
probing aliases.

1. Run `orca_pool_scan` directly once with config key `limit` sized dynamically
   for clean slots plus useful alternates. Never use `result_limit`, and do not
   list or describe the already-declared routine first.
2. Require `status=complete` and `deployable=true`. Review source coverage,
   technical rejections, neutral MCDA rank, all components, categories, and raw
   `1h`/`4h`/`24h`/`7d` metrics. Apply the active risk posture dynamically when
   comparing this evidence; do not expect or invent a posture-specific score.
   Put the scan `report_id`, or its `report_error`, in the response and journal.
3. Determine clean slot count from configured executor capacity and remaining
   capital. Set this tick's deployment batch capacity to
   `min(clean_slot_count, max_slot_deployments_per_tick)`. Build an ordered,
   portfolio-aware candidate queue large enough to fill that batch plus
   alternates. Do not equate neutral rank one with selection.
4. Treat the completed scan as the fresh primary candidate observation. Call
   exact Orca detail or optional candles only when a required field is absent,
   stale, or contradictory; do not duplicate a fresh scan with both pool and
   candle tools for every candidate. If one candidate is declining, unsuitable,
   already in use, or recently rejected without improved evidence, record its
   reason and continue. One candidate rejection must not become portfolio HOLD.
5. Select distinct suitable pools up to this tick's deployment batch capacity.
   HOLD only batch slots left unfilled after the returned candidates or a
   portfolio-wide gate are exhausted. Clean slots above the per-tick cap remain
   available for a later tick; do not label them rejected or blocked.
6. Resolve Gateway scope once and reuse it until a confirmed mutation changes
   balances or a response contradicts it. Do not independently retrieve the
   `lp_executor` schema: `lp_create_guard` retrieves and validates it immediately
   before create. Do not relist routines, reread skills, or repeat
   current-controller searches in the same preflight.
7. Choose a half-width and allocation for each selected pool inside the configured
   bounds and remaining portfolio budget. Call `clmm_position_plan` directly for
   each with the scan's exact pool address, current price, tick spacing, token
   mint/symbol/decimals, allocation, range bounds, `rebalance_threshold_pct`, and
   `max_slippage_pct`; preserve every planner report.
8. In loop mode, before the first token registration or wallet mutation, journal
   one DEPLOY intent containing the ordered selected pools, their exact token
   identities, a distinct operation ID and exact Jupiter BUY/create transition
   for each, and the rejected candidates considered. In run-once, put the same
   batch intent in the response snapshot.
9. For each selected pool, serially:
   1. Use the batch's exact account, network, default wallet, balances, execution
      mode, and config. Ignore unrelated existing base inventory. A confirmed
      current-session preparation receipt is related attributed inventory and
      must be replanned or exactly restored as described above.
   2. Call `gateway_swap(action="ensure_token")` once with the selected base
      token's exact mint, symbol, and decimals from the current scan. Continue
      only on `ready` or `registered`, and preserve the report metadata. Never
      register an unselected scan token, accept an address/symbol/decimals
      mismatch, bulk-register tokens, or restart Gateway. A deterministic
      collision or mismatch rejects this candidate and advances to an alternate.
      An `uncertain` registration stops the tick; on the next tick, reconcile by
      calling the same exact ensure operation, which checks the registry before
      adding anything.
   3. Use `gateway_swap(action="quote", connector="jupiter",
      network="solana-mainnet-beta",
      slippage_pct=<configured max_slippage_pct>)`, require quoted USDC input
      not to exceed the planner's `max_usdc_for_preparation_swap`, then call
      `gateway_swap(action="execute")` once with the same connector, network,
      operation, wallet, pair, side, amount, configured slippage, and maximum
      quote input set to that exact planner cap, plus `reserve_token="SOL"` and
      the configured minimum reserve.
      The shared routine is amount-agnostic, so this Strategy must enforce the
      selected allocation and current portfolio budget. Preserve the scope,
      quote, and execute report metadata separately.
   4. If execute returns `confirmed`, retain its exact receipt without a redundant
      status call. If it returns `submitted` or `pending`, confirm the hash with
      `gateway_swap(action="status")` using the same identity and original amount,
      slippage, and attribution bounds. A confirmed BUY output at or above the
      configured slippage floor is valid; replan from its exact receipt rather
      than requiring the original requested output exactly. If Gateway remains
      uncertain after timeout/unknown-result recovery, call the canonical
      `solana_transaction_reconcile` fallback once. Use its exact finalized input
      decrease and output increase as the preparation receipt only when exactly
      one transaction matches every requirement.
   5. Refresh only the pool price needed for replanning, then rerun the planner
      from fresh price and exact attributed swap evidence. The create guard
      refreshes schema, capacity, wallet binding, and balances itself.
   6. If the refreshed plan is invalid and create definitely was not submitted,
      restore only the exact acquired base to USDC. After confirmed restoration,
      reject this candidate and continue to the next alternate.
   7. Run `lp_create_guard` once with the refreshed plan, exact preparation USDC
      spend/base receipt, and exact current-session evidence; preserve the
      guard's report metadata.
   8. Make one exact
      `manage_executors(action="search", executor_id=<returned ID>)` call. A
      matching `RUNNING` executor with the expected controller, pool, config, and
      on-chain position completes reconciliation. Do not follow it with
      `positions_summary`, a generic position-hold lookup, a wallet LP-position
      lookup, or a skill read. If it is still nonterminal, preserve its capacity
      and reconcile that same executor later rather than broadening the search.
      The next candidate's create guard refreshes capacity.

One DEPLOY action may fill at most `max_slot_deployments_per_tick` clean slots in
the same tick, with an effective limit of the smaller clean-slot count. Each
executor chain remains serialized. Finish the first candidate before starting the
second and avoid repeated preflight calls so the batch can fit the tick. Stop
immediately on an uncertain mutation; do not start another candidate until the
prior chain is confirmed or safely restored. A deterministic `rejected` result
with `mutation=false` is terminal for that attempt and may advance to an
alternate. The cap is not a quota: if the first chain needed a skill read, schema
correction, recovery, or extra troubleshooting outside the canonical fast path,
finish its reconciliation and defer the next clean slot rather than risk a tick
timeout.

The ordinary one-slot fast path is:

```text
scan -> scope -> plan -> journal (loop only) -> ensure selected token -> quote -> execute
-> refresh one pool price -> replan -> create guard -> exact executor search
```

Call the declared routines directly. Any extra skill, routine discovery, pool
detail, schema, status, or executor call requires a specific missing,
nonterminal, unfamiliar, or contradictory result from this path.

## CLOSE

Choose one or more exact current-controller executors from current evidence.
Order them explicitly; each executor keeps an independent transition chain.
Every executor in `close_required_executor_ids` must be included.

1. Read `lp_close_and_recovery`.
2. In loop mode, journal one CLOSE intent containing the ordered executor IDs,
   `keep_position=false`, and each conditional exact residual-restoration
   transition. In run-once, record it in the response snapshot.
3. For each selected executor, serially:
   1. Call
      `manage_executors(action="stop", executor_id=..., keep_position=false)`
      once.
   2. Reconcile that executor until terminal and prove its exact LP position
      absent.
      If the native open/close result timed out or is unknown, use
      `solana_transaction_reconcile(operation_kind="lp_open"|"lp_close")` only
      after exact executor recovery remains inconclusive. Require the official
      Orca Whirlpool program
      `whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc`, the exact pool address, the
      position address when known, and bounded expected wallet asset changes.
      A unique finalized chain match prevents another create/stop but does not by
      itself mark the executor terminal or release its capacity.
   3. Inspect the executor's close receipt and native close-out swap.
   4. If native close-out succeeded, perform no fallback.
   5. If it provably failed and the receipt identifies an exact material
      unswapped base remainder, use `gateway_swap` to quote and execute one
      Jupiter SELL for that exact attributed amount only.
   6. Reconcile the fallback hash through `gateway_swap(action="status")` with
      the same controller/account/network/wallet/operation/pair/side identity,
      and refresh portfolio evidence before treating capacity as reusable or
      continuing to the next selected executor. Preserve every Gateway report
      metadata pair in order.

Only after stop/close may `positions_summary` be used to inspect executor-held
residual inventory. An empty summary during an active LP lifecycle says nothing
about the active on-chain LP position.

Do not stop again after uncertainty. Do not sell total wallet base. Leave ambiguous
inventory for manual review.

All selected close chains remain in the same tick while every transition confirms.

## Session Wind-Down

`lp_portfolio_limits.session` is the authority for the global latch. It computes
loop-session age from the frozen current-session config written when Condor
created that session and sums exact current-controller executor net PnL. It
compares those values with `session_max_age_minutes`,
`session_take_profit_net_pnl_ratio`, and
`session_stop_loss_net_pnl_ratio`. Treat `session.stop_latched=true` as
`GLOBAL_SESSION_STOP_LATCHED`; never estimate, clear, or override it in prose.

After latching:

- do not scan or deploy;
- reconcile uncertainty first;
- close current-controller executors through independent serialized chains in
  the same tick while every transition confirms;
- restore exact attributed residual inventory;
- keep calling the limit routine on later ticks and remain flat after all
  current-controller executors settle.

## Mandatory HOLD

HOLD on incomplete scan coverage, stale or genuinely contradictory exact
executor/receipt evidence, invalid schema, foreign ownership, overlapping live
wallet scope, failed Gateway scope, insufficient reserve/capital, same-pool use,
unresolved mutation, ambiguous attribution, or a requested action outside this
Strategy. Never HOLD merely because an active LP executor has no corresponding
held-position summary.

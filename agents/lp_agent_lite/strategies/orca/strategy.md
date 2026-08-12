---
name: orca
description: Session-isolated truth-first Orca Whirlpool strategy with MCDA selection, bounded native execution, receive-difference blacklisting, two-level exits, and quote restoration.
agent_key: null
skills:
- orca_pool_selection
- orca_lp_operations
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
  position_time_limit_minutes: 1440
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

Operate only `lp_agent_lite.orca` on Orca Whirlpools. Read the exact account,
wallet, network, provider, quote-token identity, capital policy, and risk limits
from the current config. Each tick reconstructs truth, chooses one decision
category, performs only its bounded work, refreshes affected evidence, and
discards working estimates.

## Config Authority

`default_config` in this file is fallback metadata, not a set of literals to
copy into calls. The tracked `config.example.yml` documents the same defaults.
The editable, ignored strategy-local `config.yml` overlays those defaults when a
new run starts; Condor then freezes the resolved values into a
loop session's own `config.yml`. During a tick, `[CURRENT CONFIG]` is the only
visible config authority; infer the deliberately hidden `execution_mode` only
from Condor's prompt markers. Never read the strategy-root file for a running
session, reuse another session's values, or replace a missing value with a
documented default.

Refer to configurable values by key. Use `<config.account_name>` and
`<config.network>` for native filters and top-level executor authority; use
`<config.lp_provider>` and `<config.swap_provider>` inside executor config; use
the exact configured `quote_token_symbol`, `quote_token_mint`, and
`quote_token_decimals` as QUOTE identity. Build the pair as
`<BASE symbol>-<config.quote_token_symbol>`, using the committed scanner symbol
unless confirmed registration returns a case-only Gateway canonical symbol.
Every numeric policy and threshold also comes from the current config, never
from the nominal values in frontmatter or an example file.

Account, wallet, capital, limits, exits, and ranking policy are genuinely
operator-configurable. Network, provider, and quote-token fields are also read
from config, but Lite v1 supports only values compatible with its Orca-mainnet
scanner, Orca Stats index, registry routine, and native executor schemas. Reject
an unsupported configured identity with `HOLD`; do not silently substitute the
documented default.

## Canonical Terms And Transition Budget

- `QUOTE` is the exact configured quote-token tuple; `BASE` is the selected
  pool's other token.
- `trading_pair` is
  `<BASE symbol>-<config.quote_token_symbol>`, using the symbol rule above, and
  every price is QUOTE per one BASE.
  A reversed or uncertain orientation blocks the affected swap or LP action.
- A read, schema lookup, skill read, pure routine, or metrics preview is evidence,
  not a lifecycle transition.
- A tick may fold at most two adjacent lifecycle phases and at most one external
  mutation phase. Fresh `SELECT` commits one deployment chain and ends without
  mutation. Allowed folds are direct predecessor reconciliation into `OPEN` or
  a corrected `OPEN`/`CLOSE`, `SUPERVISE -> CLOSE`, and wind-down evaluation
  into `CLOSE`. Required post-submit reconciliation belongs to that mutation
  phase.
- Never fold three phases or two dependent mutations. In particular, prohibit
  selection into any mutation, registration mutation into preparation/open,
  `PREPARE -> OPEN`, `CLOSE -> CLEANUP`, `CLEANUP -> SELECT/PREPARE/OPEN`, and
  `CLEANUP -> STOP`. After a mutation, perform only its required exact
  reconciliation and end the tick. Successful registration performs its own
  exact read-back; successful `PREPARE`, `OPEN`, and `CLEANUP` create receipts
  end their ticks and defer reconciliation to the next canonical tick.
- `max_lp_deployments_per_tick` counts LP create calls only. Registration,
  preparation BUY, cleanup SELL, and Agent stop remain separate external
  transitions; phase folding never combines any two of them.
- `CLOSE` is the bounded loop-mode exception: several independently triggered
  exact current-session LP stops may run sequentially in one tick, up to
  `risk_limits.max_open_executors`. Journal the exact target set once, refresh
  after each stop, and stop the batch on uncertainty. Never run those stops
  concurrently or combine the batch with registration, preparation, open,
  cleanup, or Agent stop.
- `WIND_DOWN` is a no-new-risk posture, not a tool action. It may close the
  bounded exact set above; quote cleanup and final `STOP` occur only after fresh
  later evidence.

## Admit The Tick

Infer dry-run, run-once, or loop behavior from the injected prompt using
`AGENT.md`. Do not expect `execution_mode` in `[CURRENT CONFIG]`; Condor removes
it before prompt construction. Require the exact current identity:

- loop: `lp_agent_lite.orca_<positive integer>`;
- dry run or run once: `lp_agent_lite.orca_e<positive integer>`.

Identity form must agree with the inferred mode but never determines the mode.
Conflicts require read-only `HOLD`.

Validate the complete current config before live mutation. All numeric values
must be finite and nonnegative where applicable. Require:

- nonempty `account_name`, `network`, `lp_provider`, and `swap_provider`;
- the selected pool's quote side exactly matches the configured quote symbol,
  mint, and decimals;
- the configured network and quote identity are supported by the mainnet Orca
  scanner and Stats evidence; unsupported identity config produces `HOLD`
  rather than a fallback;
- `wallet_address` is an explicit valid Solana address before live LP mutation;
  never infer it from an older session or another account;
- `0 < max_amount_quote_per_lp_position <= total_amount_quote`;
- `0 <= capital_headroom_pct < 100`;
- `0 <= lp_open_balance_buffer_pct <= capital_headroom_pct`;
- positive `target_active_lp_positions`, `max_lp_deployments_per_tick`,
  `candidate_scan_limit`, and `risk_limits.max_open_executors`;
- `orca_stats_indexing_lag_seconds >= 30`;
- `0 < preparation_receive_difference_blacklist_pct <= 100`;
- `0 < minimum_range_half_width_pct <= maximum_range_half_width_pct`;
- `lp_pnl_grace_period_minutes >= 0`; zero disables only the PnL grace;
- positive position/session exit ratios and time limits;
- the five nonnegative `mcda_weights` sum exactly to `1`;
- `risk_limits.max_position_size_quote >= total_amount_quote` and
  `target_active_lp_positions <= risk_limits.max_open_executors`. The risk limit
  is the sole hard simultaneous-executor ceiling and counts every current-session
  LP, preparation, and cleanup executor while it is open. Apply the tighter
  native risk state when its live remaining capacity is lower.

`max_ticks: 0` is intentional: generic tick stopping does not perform quote
restoration. Session time limits trigger `WIND_DOWN`, followed by a gated native
`stop_agent`.

## Evidence Authority And Freshness

At the beginning of every tick, collect each broad surface once and reuse it
until a mutation invalidates that surface. For a normal loop-mode `HOLD`, the
canonical path is exactly one wallet portfolio read, one current-controller
executor search, one exact current-session performance report, one metrics
snapshot, and one journal write. Do not repeat a broad read or add unrelated
detail, liveness, skill, pool, or account-wide searches merely to confirm
`HOLD`.

The canonical start-of-tick wallet read remains fresh through pool reads,
calculations, metrics, journaling, and the immediately pre-submit liveness check.
When no wallet-affecting mutation occurred in that tick, reuse that same wallet
for sizing, admission, and the exact journal baseline; do not make a second
precautionary wallet read. Only a wallet-affecting mutation invalidates it.

1. `get_portfolio_overview` with
   `account_names=[<config.account_name>]` and
   `connector_names=[<config.network>]`, balances enabled, LP positions, active
   orders, and perps disabled, and `refresh=true`. This call is balance-only;
   never query or use the HAPI LP portfolio.
2. `manage_executors(action="search", controller_ids=[<exact current>], ...)`
   for current-controller executors. Consume its raw `executors` rows for current
   status and metrics fields. Every returned full `id` is current-session
   attribution from the server-side controller filter. Do not fan out
   exact-detail reads across every active executor. Fetch one exact executor
   only when its broad row lacks or contradicts a field
   required for metrics or the current decision, indicates an exit or lifecycle
   transition, is the proposed mutation target, or is affected by a mutation
   being reconciled. Before mutating a target, retain the exact-ID proof below.
3. `manage_executors(action="performance_report",
   controller_id=<exact current>)` for exact current-session performance. Keep
   this call mandatory: visible injected CORE DATA is rounded and cannot replace
   the exact metrics value or a session-exit comparison.
4. In loop mode, call `snapshot_lp_metrics` once with the injected tick and the
   compact facts already observed. It supplies the exact current-session clock
   and records pre-decision observations; evaluate exits only after it returns.
   It may write only this session's one tick snapshot. In experiment modes the
   routine infers preview behavior from Condor context; do not pass a nonexistent
   `preview` Config field, and it must not write custom state.

Conditional reads follow the decision; they are not part of a quiet tick:

- After selecting an otherwise admissible external transition, call
  `manage_trading_agent(action="list_agents")` immediately before submit. Require
  a complete result with no other running `lp_agent_lite.orca_*` instance on the
  same server. Never call it for a read-only `HOLD`. The native listing does not
  expose enough account/network/wallet detail to prove that two Lite instances
  are independent, so another running Lite instance is a real exclusivity
  conflict. An old or foreign executor without a running instance is not.
- Only for an unresolved mutation or genuine shared-inventory conflict, search with
   `account_names=[<config.account_name>]`,
   `connector_names=[<config.network>]`,
   no controller/status filter, and a bounded page size; walk returned cursors to
   completion and classify exact controller/status fields. Incomplete coverage
   blocks new deployment, but it does not turn an old executor into a live
   controller or suppress an independently proven exact risk-reducing close or
   the operator-authorized post-close wallet normalization below.
- Read `orca_pool_selection` or `orca_lp_operations` only when entering a flow
  covered by that skill's `when_to_use`; never read a skill as a quiet-tick
  ritual. Use the declared routines first, then request native Orca pool detail
  only for an active selection, sizing, cleanup-valuation, or pool-dependent
  decision. Never call GeckoTerminal or another external market-data fallback;
  when routine plus native evidence is insufficient, choose `HOLD`.

The controller is a session authority/filter, not the executor identifier. For
every create, retain the full returned `executor_id`. Fetch detail with that full
value and require raw detail `id` equality when the current phase requires
same-tick reconciliation; ordinary loop-mode LP `OPEN` defers that read through
the latency-bounded path below. On later ticks, an exact current-controller
filtered search establishes current-session scope and each returned full `id` is
the executor identity; fetch exact detail by that ID and require raw detail `id`
equality before lifecycle action. Never require the earlier create response on a
later tick.
Ignore `controller_id` embedded inside raw executor/config detail, including the
backend default `main`: it is neither ownership evidence nor a contradiction.
UI-style eight-character prefixes are display-only and never valid tool,
journal, metrics, retry, or stop identities.

Use current-session journal context only to locate an intent needing
reconciliation. A full-ID current-session executor satisfying the identity
contract above, with a coherent position identity and nonterminal lifecycle, is
sufficient for normal supervision; never require an HAPI LP row or routine
agreement. Use `inspect_orca_positions` only after a failed or uncertain close,
when its exact position address is already
known. Pass the configured wallet, exact position, required exact pool, mutation
start timestamp, and configured Stats lag. The routine is fixed to close
reconciliation and accepts no action selector. It compares Orca Stats exact
`summary` with position-filtered `history`. Consume only its deterministic
`close_outcome`: `closed` forbids another close; `still_active` permits a
corrected close no earlier than a later tick; and `pending_index`, `uncertain`,
or `unavailable` require `HOLD`. It is not an LP-open discovery routine; empty
results, one-endpoint evidence, or disagreement are never position-absence
proof.

For a submitted, uncertain, or ambiguous mutation, re-fetch only its affected
exact executor, finalized transaction when exposed, and wallet before deciding
that mutation's outcome. Fetch Orca Stats only for the missing/uncertain close
effect described above. Exact native executor or finalized transaction evidence
overrides the eventually consistent index; all external evidence overrides prose
and metrics.

After mutation, invalidate every affected balance, executor, position,
capacity, price, range-composition, and quote fact. Refresh only those affected
surfaces before reporting the result, except that a successful ordinary
loop-mode LP `OPEN` receipt ends its tick and refreshes those surfaces through
the next tick's canonical path. Never chain an LP open from estimated preparation
output; read the actual post-swap wallet balance on a later tick.

## Routine Invocation Contract

Call each routine as
`manage_routines(action="run", name=<exact name>, agent="lp_agent_lite",
config={...})`. The outer tool result is transport only. Parse the returned JSON
and act from its inner schema and status.

Call a normal-path routine directly from the compact contracts below. Never use
`manage_routines` `list` or `describe`, and never read a skill merely to discover
a Config. Skills remain optional judgment or exceptional-operation playbooks.

Every routine Config is an exact API contract. Use only the exact declared field
names and enum literals; never paraphrase, pluralize, change case, or substitute
a synonym. An outer error beginning `Invalid config:` is the only result proving
the request failed local validation before `/routines/run`, so no routine
instance, report, artifact, or external effect started. Correct only the fields
identified by the declared contract or validator message and make at most one
corrected call that tick. Any other outer error, or any completed invocation
regardless of its inner status, forbids repeating that routine. Config
correction does not authorize another journal intent or trading transition.

Each completed invocation best-effort saves one Condor built-in diagnostic
report after the routine result is final. `report_id` and `report_error` are
diagnostic metadata only: never use either as market truth, position truth,
mutation evidence, or retry permission. A report save failure never authorizes
rerunning the routine or any native operation. Reports are permitted in all
three execution modes and do not count as Agent state, journal, metrics, wallet,
registry, or executor mutation.

- `scan_orca_pools`: pass exactly `min_pool_tvl_usd`,
  `candidate_scan_limit`, and `mcda_weights` containing exactly
  `fee_productivity`, `recent_activity`, `price_stability`, `liquidity_depth`,
  and `execution_simplicity`. Omit routine-owned `request_size` and
  `timeout_seconds`. Its rank is comparison evidence, not action authority.
- `calculate_lp_requirements`: pass exactly `selected_allocation_quote`,
  `max_amount_quote_per_lp_position`, `remaining_session_quote`,
  `capital_headroom_pct`, `lp_open_balance_buffer_pct`,
  `allow_base_preparation`, `current_price`, `lower_price`, `upper_price`,
  `tick_spacing`, `available_base_display`, `available_quote_display`,
  `base_decimals`, and `quote_decimals`. Preserve native wallet display strings
  and use exact `"0"` only when that mint is absent from the native wallet
  response; the routine assigns that absent-token value a conservative `0.0001`
  observation quantum. Use the returned aligned, floored, buffered result
  without reconstructing it.
- `inspect_orca_positions`: use its exact skill table only after a failed or
  uncertain close of one already-known position; consume `close_outcome` exactly.
- `snapshot_lp_metrics`: pass exactly `controller_id`, `tick`,
  `session_pnl_quote`, `quote_balance`, `sol_balance`, and optional `positions`,
  `residuals`, and `last`. A position uses `executor_id`, `position_address`,
  `pool_address`, `state`, `age_minutes`, `base_amount`, `quote_amount`,
  `fees_quote`, `pnl_quote`, and `pnl_ratio`; a residual uses `mint`, `amount`,
  `value_quote`, and `status`; `last` uses `kind`, `identity`, `status`, and
  optional `transaction`. Pass only already-observed facts. Valid `last.kind`
  values are exactly `register`, `prepare`, `open`, `close`, `cleanup`, and
  `stop`; omit `last` rather than inventing a synonym.
  Position `state` accepts case/separator-insensitive native aliases:
  `RUNNING`, `ACTIVE`, `IN_RANGE`, `BELOW_RANGE`, and `ABOVE_RANGE` normalize to
  `active`; `SHUTTING_DOWN` and `CLOSING` normalize to `closing`; `TERMINATED`,
  `COMPLETED`, and `CLOSED` normalize to `closed`. Unknown states still reject.
  `last.status` is not normalized from native lifecycle text: classify the
  mutation as one of the declared outcome values, or omit `last` until proven.
- `register_gateway_token`: pass exactly `network=<config.network>`, selected
  BASE `mint`, scanner `symbol`, scanner `decimals`, and explicit `preview`;
  omit routine-owned `timeout_seconds`. Use `preview=false` directly for the
  normal registration transition; `preview=true` is only read-only
  reconciliation after an uncertain add.

## Tick Priority

Apply this order:

1. reconcile any submitted, uncertain, ambiguous, or unavailable current-session
   mutation;
2. evaluate current-session per-position exits and whole-session exit;
3. when wind-down is active, sequentially `CLOSE` its bounded independently
   verified exact LP set, or choose one `CLEANUP` residual, or `STOP` when already
   clean; never combine those categories;
4. otherwise reconcile a pending close and clean one post-close BASE balance;
5. when the current-session deployment count is below
   `target_active_lp_positions` and deployment remains eligible, resume the exact
   committed deployment chain; only when none exists, run the bounded fresh
   selection path. Choose at most one registration, `PREPARE`, or `OPEN`
   transition;
6. otherwise `HOLD` with the specific blocker or the result that no eligible
   candidate survived selection.

An uncertainty blocks only its shared authority chain. It must not suppress an
independently proven exit or safe read-only supervision for another position.
No deployment is allowed while a current-session close, restoration, cleanup,
or preparation outcome is unresolved, except that a classified failed-close
quarantine is isolated to its exact executor, position, pool, and attributable
capital. It occupies one target position and blocks that pool, but it does not
block unrelated eligible work.

`target_active_lp_positions` is an operating objective, not a safety waiver. A
healthy existing LP is not by itself a reason to `HOLD`. When the target is not
yet met, no committed deployment chain exists, and the gates above are clear,
the Agent must scan and evaluate another candidate in the same tick; it must not
defer the scan merely because no scan was performed on the previous tick. It may
still `HOLD` when fresh evidence shows a concrete
risk/capacity/config/uncertainty blocker or no valid candidate.

## Ownership, Capacity, And Capital

Use the injected current-session risk state plus full-ID current-session
executor evidence for executor capacity. Every current-session nonterminal LP,
preparation, cleanup, submitted, or uncertain executor consumes
`risk_limits.max_open_executors`; a create is ineligible when the live count has
reached that ceiling. Reconcile disagreement between the prompt risk count and
the exact current-controller filtered search before creating. Do not create a
separate LP position cap. A terminal failed executor in failed-close quarantine
does not consume `risk_limits.max_open_executors`, although its exact active
on-chain position still occupies one `target_active_lp_positions` unit.

An executor is current-session owned when its full ID came directly from this
session's create response or from an exact current-controller filtered search,
and exact detail `id` equals that full ID before lifecycle action. Only such
executors may be supervised, stopped, or assigned PnL. The controller supplies
session scope while the returned full ID supplies identity; embedded config
`controller_id` is ignored. A pool/range similarity, ID prefix, position alone,
or one wallet balance is not
ownership. Older, foreign, and otherwise untracked positions are observation-only
and do not count toward the current-session deployment target or executor risk
count; their inventory still reduces available wallet balances. A new session
never adopts, resumes, closes, or cleans an old session.

Current-session capital exposure includes exact active LP exposure, confirmed
preparation inventory awaiting use or restoration, and submitted or uncertain
amounts that may consume capital. It also includes attributable capital in an
exact active position isolated by failed-close quarantine. Terminal quote-clean
executors do not count.
Never exceed `total_amount_quote` in aggregate or
`max_amount_quote_per_lp_position` for one LP, and never compound the session
authorization after profit.

For one proposed position set:

```text
position_authorization = min(
  selected_allocation_quote,
  max_amount_quote_per_lp_position,
  remaining_session_quote
)
usable_planning_budget = position_authorization * (1 - capital_headroom_pct/100)
```

The headroom is deliberately unspent; it is not extra capital or slippage.
`lp_open_balance_buffer_pct` is funded inside that headroom and reserves each
LP leg against the provider's maximum debit. Fresh wallet balances and
`min_sol_reserve` remain hard constraints.

## Two-Level Exit Policy

Newly opened LP valuation can temporarily report the deposited principal as a
loss. Compute each LP's age from its authoritative executor creation timestamp.
While `age_minutes < lp_pnl_grace_period_minutes`, treat that LP's PnL and the
whole-session aggregate PnL as provisional. Do not trigger either position-level
PnL exit or session PnL wind-down during this grace. Zero disables the grace.
The grace suppresses only PnL-based exits: operator wind-down, position/session
time limits, exact reconciliation, and otherwise eligible deployment remain
active. On the first tick at or after the configured boundary, require fresh PnL
evidence from a fresh performance report and current executor evidence; never
reuse a breach observed during grace.

Outside that grace, evaluate every full-ID active current-session LP
independently. `CLOSE` that one executor when any verified condition is true:

- `net_pnl_ratio >= position_take_profit_net_pnl_ratio`;
- `net_pnl_ratio <= -position_stop_loss_net_pnl_ratio`;
- `age_minutes >= position_time_limit_minutes`.

Do not guess whether a native percent field is a ratio or a percentage. Require
documented native semantics or derive a ratio from exact native quote PnL and
that executor's attributable deployed quote value. Missing exit evidence blocks
new deployment but not reconciliation of an already proven close condition.

A per-position exit leaves healthy siblings running. Stop the exact LP executor
once with `keep_position=false`, then complete its quote-clean gate before
reusing its capital or capacity.

Enter `WIND_DOWN` and prohibit every new registration, preparation, and open
when any condition is true:

- current-session net PnL quote is at least
  `total_amount_quote * session_take_profit_net_pnl_ratio`;
- it is at most
  `-(total_amount_quote * session_stop_loss_net_pnl_ratio)`;
- exact current-session age reaches `session_time_limit_minutes`;
- the operator asks for a graceful end.

Session PnL is the native aggregate of full-ID current-session active and
terminal executors after costs. Its fixed denominator is
`total_amount_quote`. Wind-down serializes closes for all exact current-session
LPs. In loop mode it may submit the bounded exact close set in one tick, one stop
call at a time, refreshing after each; the first uncertain result ends the batch.
Cleanup remains a later separately admitted transition. One uncertain close
blocks conflicting work on that position but does not erase confirmed facts for
a sibling.

## Deployment Path

Deployment is loop-only. Count full-ID active current-session LPs plus any
submitted or uncertain current-session open that may have landed toward
`target_active_lp_positions`, plus each exact active on-chain position isolated
by failed-close quarantine. While that count is below the target, deployment
is eligible only when exits and cleanup are clear, the injected live open-executor
count is below `risk_limits.max_open_executors`, the same Agent/Strategy wallet
scope has no freshly observed conflicting mutation, remaining session capital
and native executor risk capacity are positive, session exit facts are complete,
and no unresolved mutation can consume the same authority. Older or foreign
positions alone are balance facts, not proof of a concurrent mutation.

At most one active or possibly landed LP per exact pool is mandatory. Exclude a
pool while a current-session LP there is nonterminal or its outcome is
submitted, uncertain, ambiguous, or unavailable. The pool becomes eligible
again after a terminal close and its cleanup are resolved, or after an open is
proven `rejected_before_submit`; that failed open may use only the existing
corrected retry path. There is no config waiver. A possibly landed create is
never retried. A failed-close quarantined pool remains excluded while its exact
on-chain position is active.

When deployment is eligible and the target is unmet, run `Select` only when no
committed deployment chain exists. A commitment consists of the exact pool,
canonical pair, BASE symbol, BASE mint, BASE decimals, allocation, range thesis,
and next phase, persisted in the current-session action entry and recovered only
from injected current-session continuity. This journal evidence is not a new
state system. A selected chain remains binding through any required `REGISTER`,
`PREPARE`, and `OPEN` ticks.
Serialize it concisely as `deployment_chain=committed`, `pool`, `pair`,
`base_symbol`, `base_mint`, `base_decimals`, `allocation_quote`, `range_thesis`,
and `next_phase`. A selection-only tick writes those fields in its final `HOLD`;
a `PREPARE` intent
uses `next_phase=OPEN`; confirmed `OPEN` completes the chain.

While a commitment exists, do not run `scan_orca_pools`, compare alternatives,
reread `orca_pool_selection`, fetch unrelated pool details, or repeat a confirmed
registration. Refresh the committed pool's exact mechanics and recalculate
aligned range and feasible amounts. Ranking changes, ordinary price movement, or
a small preparation receipt difference may rerange or downsize the same pool but
do not release it.

Release the commitment only for a concrete identity, mint, pair, quote
orientation, registry, hard pool-validity, blacklist, meaningful-size,
capital/reserve/capacity, exclusivity, unresolved-mutation, exit, or wind-down
gate. If prepared inventory exists, complete its required restoration before
selecting another pool. The target requires evaluation, not a forced trade:
preserve `HOLD` when no candidate passes current technical, portfolio, and risk
judgment.

### Select

Before ranking, parse only exact injected execution-learning records of the form
`BLACKLIST_POOL=<pool> BLACKLIST_TOKEN=<base_mint> DIFF_PCT=<value>
LIMIT_PCT=<limit>`. Ignore malformed/general learning prose. Exclude the exact
pool and every candidate with the exact BASE mint for all later sessions until
an operator removes that learning. A blacklist only excludes; it never supplies
market facts or mutation authority.

1. Run `scan_orca_pools` once with current `min_pool_tvl_usd`,
   `candidate_scan_limit`, and the exact nested `mcda_weights`.
2. Fewer than two successful discovery lenses is `unavailable`; two or three is
   honest degraded evidence; four is complete. Degradation is not fabricated
   completeness and does not automatically freeze unrelated work.
3. Verify the bounded returned candidates with
   `explore_dex_pools(action="get_pool_info", connector="orca",
   network=<config.network>, pool_address=<exact address>)`. Do not call
   GeckoTerminal. If routine evidence plus native verification cannot support
   token, comparison, or range judgment, reject only that candidate or `HOLD`.
4. Apply the exact injected token/pool blacklist and mandatory exact-pool
   exclusion against returned candidate identities before selecting or sizing a
   candidate.
5. Compare fee productivity, recent activity, price stability, liquidity depth,
   execution simplicity, source coverage, live pool mechanics, token risk,
   portfolio overlap, and range opportunity. Select one valid candidate, explain
   a rank override with current facts, or `HOLD`.

Fresh selection never mutates. After choosing and natively verifying one
eligible pool, journal `HOLD` with the committed pool and exact next phase, then
end the tick. The next tick resumes that committed pool without another scan or
alternative comparison. Copy `base_symbol`, `base_mint`, and `base_decimals`
directly from the selected scanner row. Set `next_phase=PREPARE` for the exact
wrapped-SOL or configured quote mint and `next_phase=REGISTER` for every other
BASE mint. A registration mutation always ends its tick.

Read `orca_pool_selection` only for a genuinely close, unusual, contradictory,
or rank-override decision. The skill cannot waive a scanner technical gate.

### Register And Size

The committed scanner tuple supplies `base_symbol`, `base_mint`, and
`base_decimals`; do not call a token-information or registry-presence tool to
rediscover them. A registration-only tick does not refresh pool detail. On the
later sizing tick, make one native `get_pool_info` call and use it to verify the
committed mint orientation, price, and tick spacing.

For each newly selected chain, compare exact mints. If BASE is the wrapped-SOL
mint `So11111111111111111111111111111111111111112` or the configured
`quote_token_mint`, skip registration and continue sizing. Otherwise, in live
loop mode journal one `REGISTER` intent for the committed tuple, perform the
liveness check, and call `register_gateway_token` directly with `preview=false`.
Do not preview or check whether it is already registered: repeated registration
is allowed. Exact `status="confirmed"` with the committed mint and decimals
advances this unchanged chain to sizing on the next tick and must not be
repeated. The routine's add plus exact registry read-back is the registration
reconciliation; on the next tick go directly to the committed pool refresh and
sizing without `preview=true` or another registration call. Use returned
`canonical_symbol` for its executor pair when it differs only by case. A
confirmed registration ends the tick.

A different mint, decimals, or non-case-only symbol after the add is ambiguous
and blocks only this chain. An uncertain add is never blindly repeated; on a
later tick one `preview=true` read-only reconciliation may establish the exact
tuple. In dry run, do not call the registration routine because no registration
mutation is permitted and registry presence is not needed to evaluate the pool.

Use that single sizing-tick selected-pool result for sizing. It must come from
`explore_dex_pools(action="get_pool_info", connector="orca",
network=<config.network>, pool_address=<exact selected pool>)`. Require its exact
BASE/QUOTE orientation, finite positive current price, and tick spacing. The
scanner is the primary comparison evidence; only this native selected-pool
refresh supplies execution mechanics. Choose a range within the configured
half-width bounds from the routine evidence, current pool price, tick spacing,
fee opportunity, and inventory exposure. Then run
`calculate_lp_requirements` for that already-selected pool/range using the exact
keys `selected_allocation_quote`, `remaining_session_quote`, and
`max_amount_quote_per_lp_position`, plus fresh
`available_base_display`/`available_quote_display` strings, token decimals,
`capital_headroom_pct`, `lp_open_balance_buffer_pct`,
`allow_base_preparation`, and those current pool mechanics.

Ordinary shortages are feasibility facts. Accept a smaller meaningful result,
choose another valid range for the same committed pool, or `HOLD`; do not require
an old estimate to match. If no meaningful size remains, release the commitment,
end the tick, and defer any other candidate to later fresh selection. Invalid
identities, non-finite values, impossible bounds, negative balances, capital
breach, and SOL-reserve breach reject the affected action.

### Prepare

If the calculator returns a material exact base shortfall, choose `PREPARE`:

Use this latency-bounded fast path: canonical wallet/search/performance/metrics
evidence, the one selected-pool refresh already used for sizing, one calculation,
one intent write, one liveness check, and one create. Do not read
`orca_lp_operations`, make a separate `order_executor` schema-only call, repeat
the pool or wallet read, or perform post-create reconciliation in this tick.

1. Require the sizing call used `allow_base_preparation=true`, then journal exact
   loop-mode intent before submit, including pool, BASE mint, canonical pair,
   allocation, range thesis, `deployment_chain=committed`, `next_phase=OPEN`,
   `base_symbol`, `base_mint`, `base_decimals`,
   requested BASE shortfall, configured receive threshold, and the exact native
   `pre_base_balance_display` used by sizing so a later reconciliation tick
   retains both baselines. If the shortfall is at or below the calculator's
   derived `base_balance_observation_quantum`, do not swap; use a meaningful
   smaller result for the same pool when feasible, otherwise release the
   commitment, choose `HOLD`, and defer another candidate to a later tick;
2. Immediately before submit, perform the one liveness check, then create one
   current-controller market `order_executor` BUY: top-level exact
   `controller_id`, `account_name=<config.account_name>`, and
   `executor_type="order_executor"`; inside config use
   `type="order_executor"`, `connector_name=<config.network>`, the canonical
   pair, `execution_strategy="MARKET"`, `side=1`, and `amount` equal to the
   calculated BASE shortfall in BASE units. The native create path performs its
   own live schema validation; a configuration rejection is
   `rejected_before_submit` and ends the tick without another create. This is
   the exclusive preparation swap path. Trust the
   Gateway/Jupiter path used internally by the executor for slippage protection;
   do not request a separate quote and never call `manage_gateway_swaps`;
3. Retain the full returned executor ID plus any transaction identity and end
   the tick without more tool calls. A successful create receipt establishes
   `submitted`, not received inventory or terminal success. At the beginning of
   the next tick, reconcile that exact preparation through the canonical
   controller search, fetch its full-ID detail only when the broad row is
   insufficient, and use the canonical fresh wallet read for the actual
   post-swap BASE display. In loop mode preserve the committed pool with
   `next_phase=OPEN`.
   Never fold `PREPARE` into `OPEN`.

For a confirmed Gateway BUY in the checked-out Solana/Jupiter connector,
`executed_amount_base` repeats the requested BASE amount; it is not received
inventory and never enters receipt-difference or blacklist math. Refresh the
wallet and preserve the exact post-swap BASE display. Expand the journaled pre
and refreshed post displays using the calculator's same display rule, obtaining
`pre_value`, `post_value`, `pre_quantum`, and `post_quantum`. Bound the received
amount conservatively:

```text
observed_delta = post_value - pre_value
error_bound = pre_quantum + post_quantum
received_lower = max(observed_delta - error_bound, 0)
received_upper = observed_delta + error_bound
minimum_difference =
  0 if received_lower <= requested_base_amount <= received_upper
  else min(abs(received_lower - requested_base_amount),
           abs(received_upper - requested_base_amount))
minimum_difference_pct =
  minimum_difference / requested_base_amount * 100
```

When the requested amount lies inside the interval, the minimum difference is
zero. Do not require equality. If the interval touches or crosses the configured
threshold, it is ambiguous in the safe direction: never blacklist and never
declare preparation failed. Recalculate from the refreshed post-swap wallet
display, fit a meaningful smaller LP with `allow_base_preparation=false`, and
never submit a dust top-up swap. Blacklist only when
`minimum_difference_pct` is strictly above
`preparation_receive_difference_blacklist_pct` and exact controller, executor,
pool, pair, BASE mint, requested amount, and wallet exclusivity are all proven.
Then make the pool and BASE mint immediately ineligible, choose `HOLD`, write the
normal action entry, and write one execution learning with exact text
`BLACKLIST_POOL=<pool> BLACKLIST_TOKEN=<base_mint> DIFF_PCT=<value>
LIMIT_PCT=<configured_limit>`. This persists the exclusion into later sessions.
Equality does not blacklist. A nonpositive or contradictory wallet delta after
a submitted preparation is unresolved: `HOLD`, do not blacklist, and never
repeat the preparation. Unavailable, nonterminal, ambiguous, or insufficiently
precise evidence likewise never blacklists.

A rejected pre-submit preparation may be corrected only after fresh facts; a
submitted or uncertain one is never duplicated. A returned configuration
rejection releases only that committed candidate before mutation; choose
`HOLD`, end the tick, and defer alternatives. The documented requested-amount
meaning of `executed_amount_base` is not a schema conflict when native create
accepts the required fields.

### Open

Choose `OPEN` when fresh calculation requires no preparation. After a confirmed
preparation, fold its direct predecessor reconciliation into `OPEN` for the same
committed pool without selection or alternative comparison. If reconciliation
is unresolved, requires blacklist/restoration, or invalidates the commitment,
choose `HOLD` and end instead of opening or selecting another pool.

For an ordinary committed loop-mode `OPEN`, use this latency-bounded fast path:
one canonical wallet read plus the canonical search/performance/metrics evidence,
at most one exact
predecessor detail when its broad row is insufficient, one selected-pool
refresh, one calculation, one intent write, one liveness check, and one create.
Do not read `orca_lp_operations`, repeat an
exact predecessor detail, make an intermediate wallet/executor refresh, or make
a separate `lp_executor` schema-only call. Pure reads and calculations do not
invalidate the canonical evidence.

1. require the calculation used `allow_base_preparation=false` after any
   confirmed preparation and that its `base_balance_required` and
   `quote_balance_required` fit the wallet used by that calculation. Reuse the
   canonical current-controller search for capacity and require its open count
   below `risk_limits.max_open_executors` with no unresolved open;
2. construct the exact documented LP request below. The native
   `manage_executors(action="create")` path performs live schema validation; do
   not spend a separate tool call requesting the schema. A returned
   configuration rejection is `rejected_before_submit`, consumes this tick's LP
   deployment quota, and ends the tick without another create;
3. immediately before submit, require that no same-tick wallet mutation has
   invalidated the canonical wallet used by the calculation, verify the buffered
   BASE/QUOTE requirements still fit it, then journal the observed position
   identities, pool, range, calculated base/quote amounts, controller, and reason.
   Do not make a second wallet call. The action text must persist that canonical
   pre-submit baseline as exact
   key/value fields: `base_mint`, `pre_base_balance`, `quote_mint`, and
   `pre_quote_balance`. Persist each spendable balance as the plain decimal
   exactly reported by that native wallet surface and preserve its reported
   decimal scale; never pad or invent digits to reach mint precision. Fewer
   reported decimals than mint precision do not block the initial OPEN; planned
   LP deposit amounts are not baseline substitutes;
4. create at most one current-controller `lp_executor`: top-level exact
   `controller_id` and `account_name=<config.account_name>`; inside
   `executor_config`, `connector_name=<config.network>`,
   `lp_provider=<config.lp_provider>`,
   `swap_provider=<config.swap_provider>`,
   `trading_pair="<canonical Gateway BASE symbol>-<config.quote_token_symbol>"`,
   exact pool, aligned bounds,
   `side=3`, the calculator's floored BASE/QUOTE amounts, and
   `keep_position=false`;
5. retain the full returned `executor_id` plus any returned transaction or
   position identities and end the tick without more tool calls. The successful
   create receipt establishes `submitted`, not confirmed lifecycle state. At the
   beginning of the next tick, use the canonical exact-controller search, fetch
   that LP by full ID only when the broad row is insufficient, require detail
   `id` equality when fetched, and refresh the wallet through the normal tick
   path. A coherent position and lifecycle under that full ID then confirms
   ownership and begins normal supervision. An embedded raw/config
   `controller_id=main` is expected non-authoritative metadata, not a conflict.
   Do not call `inspect_orca_positions` for an LP open.

Every LP create call consumes `max_lp_deployments_per_tick`, including schema or
Gateway simulation rejection. Never open another LP in that tick.

## Reconciliation-Gated Retry

On the first tick after a failed LP create, use one exact executor-detail read
and one fresh wallet read. Classify the result `rejected_before_submit` only when
the recorded full executor ID equals detail `id`, is terminal `FAILED`, has no
position address, reports zero native actual LP base/quote amounts at token precision,
and the fresh balances for journaled `base_mint` and `quote_mint` equal
`pre_base_balance` and `pre_quote_balance` at the same decimal scale reported by
the same native wallet surface. Compare the wallet values exactly as reported;
do not normalize or demand mint precision. At least one failed-request planned
BASE or QUOTE debit must be no smaller than the least observable unit implied by
its matching baseline scale, so a fully landed debit would have changed that
wallet reading. Planned amounts prove only observability; requested/configured
amounts and initial-amount fallback fields are not actual LP amounts. If neither
planned debit is observable, the retry evidence is `unavailable` and requires
`HOLD`; this observability rule never blocks an initial OPEN.

When all gates pass, do not spend a separate `HOLD` tick recording the
classification. Refresh the remaining admission facts, recalculate with
`allow_base_preparation=false`, and continue directly to one corrected create
in that same reconciliation tick. Its sole action intent must name the failed
executor, state `rejected_before_submit`, include the corrected range/amounts,
and record a new four-field pre-submit wallet baseline. A corrected request
means at least one of `base_amount` or `quote_amount` is lower than the failed
request at token precision and both buffered requirements fit the refreshed
wallet; no arbitrary percentage reduction is required.

If any position address exists, a native actual LP amount is nonzero, or either
exact-mint balance changed, classify the open `uncertain` and do not retry.
Missing any of the four journal fields or an actual-amount observation is
`unavailable`, not retry permission. Metrics never supply or repair this proof.
Do not call Orca Stats, require transaction evidence, promise manual
exact-position recovery, or search the same executor logs again. The failed
create consumed its original tick's deployment quota; the corrected create is
allowed only on this or another later tick and consumes that later tick's quota.
Never repeat an unchanged request.

## Close, Cleanup, And Stop

Use this section directly for ordinary close, cleanup, and graceful wind-down;
do not load `orca_lp_operations` for those normal flows. Read that skill only
for a failed/uncertain close, close quarantine, or a genuinely exceptional
recovery question.

For `CLOSE`, build the bounded exact target set described above. In loop mode,
journal all target executor/position IDs once, then call
`manage_executors(action="stop", controller_id=<exact current>,
executor_id=<one full current-session LP ID>, keep_position=false)` sequentially
for each target. Each call contains one full executor ID; never concatenate IDs.
Exclude already quarantined failed-close targets from executable close batches.
Refresh after every call and stop that batch on uncertainty. Run once permits
only one target. On a later tick, independently proven sibling exits continue.
When a close is terminal `FAILED` or its effect is uncertain, reconcile that
known exact position with post-lag Orca Stats summary/history. A `close_outcome`
of `closed` prevents another close; `still_active` permits at most one corrected
close no earlier than a later tick; and `pending_index`, `uncertain`, or
`unavailable` remains `HOLD`. If the corrected stop returns `404` / `executor
not found` or fails again while the exact position is still active, quarantine
the exact executor, position, pool, and attributable capital for manual Gateway
or Orca-UI recovery and never retry that close again. After an ordinary
successful close, refresh wallet and affected-token balances to determine the
cleanup amount. `keep_position=false` neutralizes the LP executor's net token
change relative to its opening deposits; it does not prove that prepared BASE
inventory was sold to configured QUOTE. Never direct-close an orphan missing an
exact native executor; report its exact position for manual Gateway or Orca-UI
recovery.

Fresh evidence that the exact on-chain position is closed ends failed-close
quarantine. Refresh the wallet before reuse. Release its pool, target occupancy,
and attributable capital only when the fresh wallet can fund a new LP through
normal sizing, reserve, capital, and risk gates. Inventory attribution, QUOTE
restoration, and cleanup completion are not quarantine-release requirements;
remaining inventory follows the ordinary inventory policy separately and cannot
keep this quarantine active. If the wallet is insufficient, deployment waits on
an ordinary balance-feasibility `HOLD` rather than unresolved-close quarantine. Do not
create a quarantine slot object, persistent state ledger, config field, or
learning blacklist.

After an LP close, abandoned confirmed preparation, or wind-down, block new
deployment until the affected chain is quote-clean. After an exact
current-session LP executor is terminal, refresh its exact BASE-mint wallet
balance. For non-SOL BASE whose fresh value exceeds
`residual_base_dust_quote`, choose `CLEANUP` and create one current-controller
market `order_executor` SELL whose `amount` is the entire refreshed available
balance in `<canonical Gateway BASE symbol>-<config.quote_token_symbol>`.
Retain the full create-returned executor ID and end the tick without an exact
search or wallet refresh. The successful create receipt is `submitted`, not
confirmed cleanup. On the next tick, reconcile that exact cleanup through the
canonical controller search and wallet read. A balance at or below dust is
already clean.

This full-balance cleanup is the operator-authorized exception to inventory
attribution. It is scoped only by the exact BASE mint of the terminal
current-session LP; it may sell pre-existing same-mint wallet inventory but does
not adopt or close any foreign executor.

When the terminal LP's BASE is SOL, normally retain SOL for later SOL LPs; those
LPs may use only SOL above `min_sol_reserve`. A SOL cleanup SELL is allowed only
after that SOL-base close and only when fresh QUOTE balance is below
`total_amount_quote`. Using a fresh finite positive QUOTE-per-SOL price, compute:

```text
protected_sol = min_sol_reserve * (1 + capital_headroom_pct / 100)
excess_sol = max(fresh_sol_balance - protected_sol, 0)
quote_shortfall = max(total_amount_quote - fresh_quote_balance, 0)
sol_to_sell = min(excess_sol, quote_shortfall / sol_price_quote)
```

Floor `sol_to_sell` to accepted token precision. Never sell more SOL than this
bound. If QUOTE is already at target or no protected excess exists, leave SOL
untouched and treat the close chain as clean even when the configured quote
budget cannot be fully restored.

This is the exclusive cleanup swap path: trust the executor's internal
Gateway/Jupiter slippage protection, request no separate quote, and never call
`manage_gateway_swaps`.
Never apply full-balance normalization to an arbitrary mint, a nonterminal LP,
or SOL outside the bounded SOL-close rule. An uncertain cleanup submission still
requires quarantine and reconciliation.

Choose `STOP` only after wind-down has proven every exact current-session LP
terminal, every close and cleanup mutation reconciled, every touched base mint
at or below dust under the rules above, no unresolved capital, and the protected
SOL reserve intact. Then call only
`manage_trading_agent(action="stop_agent", agent_id=<exact current>)`.
External kills and manual hard stops bypass this workflow and require manual
inspection and cleanup; a later session never adopts the remains.

## Journal And Response

In loop mode write exactly one concise action entry per tick. A mutation entry
is its pre-submit intent; after execution do not add a second action entry. A
`HOLD` entry states the smallest blocking fact and next read-only evidence
needed. The sole cross-session-learning exception is one exact execution
blacklist after the preparation receive-difference rule above proves a strict
threshold breach; no other learning is authorized.

Use the full native call every time:
`trading_agent_journal_write(agent_id=<exact injected Agent ID>,
entry_type="action", tick=<exact injected tick>, text=<concise action>,
reasoning=<one sentence>, risk_note=<one sentence>)`. Never use a partial call.
Require `written=true` before mutation. A caller-validation rejection may be
corrected once because it wrote nothing; never create a second successful action
entry. For an authorized blacklist, separately call
`trading_agent_journal_write(agent_id=<exact injected Agent ID>,
entry_type="learning", tick=<exact injected tick>, category="execution",
text=<exact BLACKLIST record>)` and require `written=true`.

End with the chosen decision, exact affected identities, whether mutation was
attempted, its precise outcome class, quarantined capacity/capital, and the next
permitted transition. In run once put all intent and reconciliation in the
experiment response. Dry run reports the conditional decision and required
registration/preparation without mutating.

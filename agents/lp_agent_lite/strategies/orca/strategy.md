---
name: orca
description: Session-isolated truth-first Orca Whirlpool strategy with MCDA selection, bounded native execution, persistent receipt blacklisting, two-level exits, and quote restoration.
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
  allow_multiple_lp_positions_per_pool: true
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
`BASE-<config.quote_token_symbol>`. Every numeric policy and threshold also comes
from the current config, never from the nominal values in frontmatter or an
example file.

Account, wallet, capital, limits, exits, and ranking policy are genuinely
operator-configurable. Network, provider, and quote-token fields are also read
from config, but Lite v1 supports only values compatible with its Orca-mainnet
scanner, Orca Stats index, registry routine, and native executor schemas. Reject
an unsupported configured identity with `HOLD`; do not silently substitute the
documented default.

## Canonical Terms And Transition Budget

- `QUOTE` is the exact configured quote-token tuple; `BASE` is the selected
  pool's other token.
- `trading_pair` is `BASE-<config.quote_token_symbol>`, and every price is QUOTE
  per one BASE.
  A reversed or uncertain orientation blocks the affected swap or LP action.
- A read, schema lookup, skill read, pure routine, or metrics preview is evidence,
  not a lifecycle transition.
- `max_lp_deployments_per_tick` counts LP create calls only. Registration,
  preparation BUY, cleanup SELL, and Agent stop remain separate transitions and
  are never combined with each other or an LP create in one tick.
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
- boolean `allow_multiple_lp_positions_per_pool`;
- positive `target_active_lp_positions`, `max_lp_deployments_per_tick`,
  `candidate_scan_limit`, and `risk_limits.max_open_executors`;
- `orca_stats_indexing_lag_seconds >= 30`;
- `0 < preparation_receive_difference_blacklist_pct <= 100`;
- `0 < minimum_range_half_width_pct <= maximum_range_half_width_pct`;
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

At the beginning of every tick, collect each broad surface once:

1. `get_portfolio_overview` with
   `account_names=[<config.account_name>]` and
   `connector_names=[<config.network>]`, balances enabled, LP positions, active
   orders, and perps disabled, and `refresh=true`. This call is balance-only;
   never query or use the HAPI LP portfolio.
2. `manage_executors(action="search", controller_ids=[<exact current>], ...)`
   for current-controller executors, followed by exact detail only where needed.
3. `manage_executors(action="performance_report",
   controller_id=<exact current>)` for current-session performance.
4. Before any external transition, call
   `manage_trading_agent(action="list_agents")`. Require a complete result with no
   other running `lp_agent_lite.orca_*` instance on the same server. The native
   listing does not expose enough account/network/wallet detail to prove that two
   Lite instances are independent, so another running Lite instance is a real
   exclusivity conflict. An old or foreign executor without a running instance
   is not.
5. For unresolved mutation and shared-inventory facts, search with
   `account_names=[<config.account_name>]`,
   `connector_names=[<config.network>]`,
   no controller/status filter, and a bounded page size; walk returned cursors to
   completion and classify exact controller/status fields. Incomplete coverage
   blocks new deployment and any cleanup attribution that depends on aggregate
   balance exclusivity, but it does not turn an old executor into a live
   controller or suppress an independently proven exact risk-reducing close.
6. In loop mode, call `snapshot_lp_metrics` once with the injected tick and the
   compact facts already observed. It supplies the exact current-session clock
   and records pre-decision observations; evaluate exits only after it returns.
   It may write only this session's one tick snapshot. In experiment modes the
   routine infers preview behavior from Condor context; do not pass a nonexistent
   `preview` Config field, and it must not write custom state.

Use current-session journal context only to locate an intent needing
reconciliation. An exact current-controller executor with a coherent position
identity and nonterminal lifecycle is sufficient for normal supervision; never
require an HAPI LP row or routine agreement. Use `inspect_orca_positions` only
after a failed or uncertain close, when its exact position address is already
known. Pass the configured wallet, exact position, required exact pool, mutation
start timestamp, and configured Stats lag. The routine is fixed to close
reconciliation and accepts no action selector. It compares Orca Stats exact
`summary` with position-filtered `history`. Consume only its deterministic
`close_outcome`: `closed` forbids another close; `still_active` permits a
corrected close no earlier than a later tick; and `pending_index`, `uncertain`,
or `unavailable` require `HOLD`. It is not an LP-open discovery routine; empty
results, one-endpoint evidence, or disagreement are never position-absence
proof.

Re-fetch the exact executor, finalized transaction when exposed, and wallet
before deciding. Fetch Orca Stats only for the missing/uncertain effect described
above. Exact native executor or finalized transaction evidence overrides the
eventually consistent index; all external evidence overrides prose and metrics.

After mutation, invalidate every affected balance, executor, position,
capacity, price, range-composition, and quote fact. Refresh only those affected
surfaces before reporting the result. Never chain an LP open from estimated
preparation output; read the actual post-swap wallet balance on a later tick.

## Routine Invocation Contract

Call each routine as
`manage_routines(action="run", name=<exact name>, agent="lp_agent_lite",
config={...})`. The outer tool result is transport only. Parse the returned JSON
and act from its inner schema and status.

Each completed invocation best-effort saves one Condor built-in diagnostic
report after the routine result is final. `report_id` and `report_error` are
diagnostic metadata only: never use either as market truth, position truth,
mutation evidence, or retry permission. A report save failure never authorizes
rerunning the routine or any native operation. Reports are permitted in all
three execution modes and do not count as Agent state, journal, metrics, wallet,
registry, or executor mutation.

- `scan_orca_pools`: pass `min_pool_tvl_usd`, `candidate_scan_limit`, and the
  exact nested `mcda_weights`; leave `request_size` and `timeout_seconds` at
  routine defaults unless current config explicitly supplies them. `complete`
  means four bounded discovery requests returned, `degraded` means two or three,
  and `unavailable` returns no deployable shortlist. Rank is comparison evidence.
- `calculate_lp_requirements`: pass exactly `selected_allocation_quote`,
  `max_amount_quote_per_lp_position`, `remaining_session_quote`,
  `capital_headroom_pct`, `lp_open_balance_buffer_pct`,
  `allow_base_preparation`, `current_price`, `lower_price`, `upper_price`,
  `tick_spacing`, `available_base`, `available_quote`, `base_decimals`, and
  `quote_decimals`. Set `allow_base_preparation=true` before the selected action
  chain has a confirmed preparation; the returned `base_shortfall` then includes
  the configured LP-open balance buffer. Set it `false` after confirmed
  preparation or for a corrected LP-open retry so the routine sizes down to
  actual balances and cannot request a dust top-up. `feasible` means both
  floored LP legs are nonzero and their reported buffered balance requirements
  fit the permitted balance path; it is not execution authorization.
  `preparation_required` chooses a separate later-tick `PREPARE`; `infeasible`
  means resize/rerange/reselect or `HOLD`.
- `inspect_orca_positions`: pass required `wallet_address`, `position_address`,
  `expected_pool_address`, and `mutation_started_at`; the timestamp is an
  integer Unix epoch in seconds, not ISO text or milliseconds. The routine is
  close-only and has no `expected_action_type` input. Set
  `indexing_lag_seconds` from Strategy config and call only for a failed or
  uncertain close with a known exact position. Use its `close_outcome` exactly:
  `closed` forbids another close; `still_active` permits a corrected close no
  earlier than a later tick; `pending_index`, `uncertain`, and `unavailable`
  require `HOLD`. `not_indexed`, one endpoint, disagreement, or unavailable
  evidence never proves on-chain absence.
- `snapshot_lp_metrics`: pass only already observed `controller_id`, injected
  `tick`, `session_pnl_quote`, `quote_balance`, `sol_balance`,
  de-duplicated `positions`, `residuals`, and optional `last`. For each position,
  pass only `executor_id`, `position_address`, `pool_address`, `state`,
  `age_minutes`, `base_amount`, `quote_amount`, `fees_quote`, `pnl_quote`, and
  `pnl_ratio`. `position_address` is the position identity; never pass
  `position_mint`, an empty placeholder, or a substituted address. Map a coherent
  exact nonterminal native executor, including `RUNNING` plus `IN_RANGE`, to
  compact `state="active"`; map a submitted stop awaiting terminal proof to
  `closing`, and use `closed` only with terminal evidence. Native fields such as
  `status`, `range_state`, range prices, token symbols, and position value are
  source evidence, not metrics input keys. Pass `residuals` as a list of exact
  objects—not a symbol-to-amount map—with exact Solana-address `mint`, `amount`,
  optional `value_quote`, and `status` in
  `prepared|clean|cleanup|unattributed`. Use `prepared` only for exact
  current-session BASE awaiting LP open or restoration.
  Pass optional `last` as exactly `kind`, `identity`, mutation-outcome `status`,
  and optional `transaction`. Do not call routine discovery to
  rediscover this declared schema, and do not make a second metrics attempt in
  the same tick after caller-side validation rejection. Never fabricate a
  missing metric or pass an exit decision the routine cannot yet know. Read
  compact tuples by their returned `p_cols`/`r_cols`, not a remembered index.
  Loop success writes the bounded authoritative metrics payload once, excluding
  response-only `report_id`/`report_error`; experiment success is an unwritten
  metrics preview even though its Condor diagnostic report may be saved. Require
  `artifact_write=true` only for loop
  `status="complete"`; `mutation=false` means no trading/external transition.
  An unavailable metrics routine blocks only facts that depend on that artifact
  or its session clock. It never suppresses an independently verified native
  risk-reducing exit or safe read-only supervision.
- `register_gateway_token`: pass the configured `network` and selected token's
  exact `mint`, `symbol`, and `decimals`. `preview=true` is read-only; absent
  returns `preview`, not registration. In loop, `preview=false` permits at most
  one add and read-back. Continue only on inner `status="confirmed"` with the
  exact tuple; `rejected_before_submit`, `ambiguous`, `uncertain`, or
  `unavailable` blocks that token. A live registration is the tick's transition.

## Tick Priority

Apply this order:

1. reconcile any submitted, uncertain, ambiguous, or unavailable current-session
   mutation;
2. evaluate current-session per-position exits and whole-session exit;
3. when wind-down is active, sequentially `CLOSE` its bounded independently
   verified exact LP set, or choose one `CLEANUP` residual, or `STOP` when already
   clean; never combine those categories;
4. otherwise reconcile a pending close and clean one attributable residual;
5. when the current-session deployment count is below
   `target_active_lp_positions` and deployment remains eligible, run the fresh
   selection path in this tick and choose at most one registration, `PREPARE`,
   or `OPEN` transition;
6. otherwise `HOLD` with the specific blocker or the result that no eligible
   candidate survived selection.

An uncertainty blocks only its shared authority chain. It must not suppress an
independently proven exit or safe read-only supervision for another position.
No deployment is allowed while a current-session close, restoration, cleanup,
or preparation outcome is unresolved.

`target_active_lp_positions` is an operating objective, not a safety waiver. A
healthy existing LP is not by itself a reason to `HOLD`. When the target is not
yet met and the gates above are clear, the Agent must scan and evaluate another
candidate in the same tick; it must not defer the scan merely because no scan
was performed on the previous tick. It may still `HOLD` when fresh evidence
shows a concrete risk/capacity/config/uncertainty blocker or no valid candidate.

## Ownership, Capacity, And Capital

Use the injected current-session risk state plus exact current-controller
executor evidence for executor capacity. Every current-session nonterminal LP,
preparation, cleanup, submitted, or uncertain executor consumes
`risk_limits.max_open_executors`; a create is ineligible when the live count has
reached that ceiling. Reconcile disagreement between the prompt risk count and
exact current-controller search before creating. Do not create a separate LP
position cap.

Only exact executors created by the current controller may be supervised,
stopped, or assigned PnL. A pool/range similarity or one wallet balance is not
ownership. Older, foreign, and otherwise untracked positions are observation-only
and do not count toward the current-session deployment target or executor risk
count; their inventory still reduces available wallet balances. A new session
never adopts, resumes, closes, or cleans an old session.

Current-session capital exposure includes exact active LP exposure, confirmed
preparation inventory awaiting use or restoration, and submitted or uncertain
amounts that may consume capital. Terminal quote-clean executors do not count.
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

Evaluate every exact active current-controller LP independently. `CLOSE` that
one executor when any verified condition is true:

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

Session PnL is the native aggregate of exact current-controller active and
terminal executors after costs. Its fixed denominator is
`total_amount_quote`. Wind-down serializes closes for all exact current-session
LPs. In loop mode it may submit the bounded exact close set in one tick, one stop
call at a time, refreshing after each; the first uncertain result ends the batch.
Cleanup remains a later separately admitted transition. One uncertain close
blocks conflicting work on that position but does not erase confirmed facts for
a sibling.

## Deployment Path

Deployment is loop-only. Count exact active current-controller LPs plus any
submitted or uncertain current-session open that may have landed toward
`target_active_lp_positions`. While that count is below the target, deployment
is eligible only when exits and cleanup are clear, the injected live open-executor
count is below `risk_limits.max_open_executors`, the same Agent/Strategy wallet
scope has no freshly observed conflicting mutation, remaining session capital
and native executor risk capacity are positive, session exit facts are complete,
and no unresolved mutation can consume the same authority. Older or foreign
positions alone are balance facts, not proof of a concurrent mutation.

Apply `allow_multiple_lp_positions_per_pool` before sizing. When `false`, an
exact active, submitted, uncertain, ambiguous, or unavailable current-session
open already using or possibly using a pool makes that pool ineligible for a new
LP; evaluate another candidate or `HOLD`. When `true`, a healthy exact active LP
does not exclude its pool, and the Agent may create another independently sized
and ranged LP there. This permission never weakens mutation reconciliation:
possibly-landed creates are never retried under either value.

When deployment is eligible and the target is unmet, run `Select` every tick.
The target requires evaluation, not a forced trade: open at most one LP per tick
and preserve `HOLD` when no candidate passes current technical, portfolio, and
risk judgment.

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
   network=<config.network>, pool_address=<exact address>)`. Use bounded
   GeckoTerminal token or OHLCV context only when close scores, a contradiction,
   or range judgment needs it.
4. Apply the exact injected token/pool blacklist, then
   `allow_multiple_lp_positions_per_pool`, against returned candidate identities
   before selecting or sizing a candidate.
5. Compare fee productivity, recent activity, price stability, liquidity depth,
   execution simplicity, source coverage, live pool mechanics, token risk,
   portfolio overlap, and range opportunity. Select one valid candidate, explain
   a rank override with current facts, or `HOLD`.

Read `orca_pool_selection` only for a genuinely close, unusual, contradictory,
or rank-override decision. The skill cannot waive a scanner technical gate.

### Register And Size

For one selected pool, reconcile the exact base mint, symbol, and decimals.
When the token is absent in live loop mode, run `register_gateway_token` once;
set `preview=false` for that single add-capable invocation. A confirmed exact
read-back completes `REGISTER`; record it and end the tick. A metadata conflict
rejects that candidate. An uncertain add result blocks another add; later ticks
may use only `preview=true` read-only registry reconciliation until truth
resolves. In dry run, only state that registration would be required; do not
call the mutating routine.

Immediately before sizing, refresh the exact selected pool with
`explore_dex_pools(action="get_pool_info", connector="orca",
network=<config.network>, pool_address=<exact selected pool>)`. Require its exact
BASE/QUOTE orientation, finite positive current price, and tick spacing. The
scanner price is discovery evidence and GeckoTerminal is contextual evidence;
neither substitutes for this selected-pool refresh. Choose a range within the
configured half-width bounds from that current pool price, tick spacing,
volatility, fee opportunity, and inventory exposure. Then run
`calculate_lp_requirements` for that already-selected pool/range using the exact
keys `selected_allocation_quote`, `remaining_session_quote`, and
`max_amount_quote_per_lp_position`, plus fresh balances, precision,
`capital_headroom_pct`, `lp_open_balance_buffer_pct`,
`allow_base_preparation`, and those current pool mechanics.

Ordinary shortages are feasibility facts. Accept a smaller meaningful result,
choose another valid range/candidate, or `HOLD`; do not require an old estimate
to match. Invalid identities, non-finite values, impossible bounds, negative
balances, capital breach, and SOL-reserve breach reject the affected action.

### Prepare

If the calculator returns a material exact base shortfall, choose `PREPARE`:

1. read the current `order_executor` schema;
2. require the sizing call used `allow_base_preparation=true`, then journal exact
   loop-mode intent before submit, including pool, BASE mint,
   pair, requested BASE shortfall, and configured receive threshold so a later
   reconciliation tick retains the exact requested-amount baseline;
3. require the live schema to support the preparation request described in
   `orca_lp_operations`, then create one current-controller market
   `order_executor` BUY whose `amount` is exactly the calculated BASE shortfall
   in BASE units. This is the exclusive preparation swap path. Trust the
   Gateway/Jupiter path used internally by the executor for slippage protection;
   do not request a separate quote and never call `manage_gateway_swaps`;
4. retain its exact executor ID and reconcile it;
5. refresh wallet and executor evidence, then end the tick.

For a confirmed Gateway BUY in the checked-out connector, the exact terminal
executor's positive `executed_amount_base` is the realized received BASE; still
refresh the wallet for spendable LP inventory. Compare it with the same
preparation's positive requested BASE amount from the journaled order-executor
intent using exact decimal arithmetic:

```text
receive_difference_pct =
  abs(executed_amount_base - requested_base_amount) / requested_base_amount * 100
```

Do not require equality. When the result is at or below
`preparation_receive_difference_blacklist_pct`, recalculate from the true
available wallet balance and fit a meaningful smaller LP when necessary while
passing `allow_base_preparation=false` and preserving both headroom values; never
submit a dust top-up swap. When it is strictly above
the threshold, require an exact confirmed current-controller executor, matching
pool/pair/BASE mint, the same positive requested amount, and no contradictory
wallet activity.
Then make the pool and BASE mint immediately ineligible, choose `HOLD`, write the
normal action entry, and write one execution learning with exact text
`BLACKLIST_POOL=<pool> BLACKLIST_TOKEN=<base_mint> DIFF_PCT=<value>
LIMIT_PCT=<configured_limit>`. This persists the exclusion into later sessions.
Equality does not blacklist. Uncertain, unavailable, nonterminal, zero, or
contradictory amounts cause reconciliation/HOLD but never a blacklist.

A rejected pre-submit preparation may be corrected only after fresh facts; a
submitted or uncertain one is never duplicated. Unavailable or invalid
order-executor schema evidence rejects only that candidate before mutation, so
continue through the bounded alternatives before `HOLD`.

### Open

When fresh calculation requires no preparation:

1. require the calculation used `allow_base_preparation=false` after any
   confirmed preparation and that its `base_balance_required` and
   `quote_balance_required` fit the refreshed wallet; immediately refresh native
   wallet, executor, and known exact-position facts;
   require the exact current-session open-executor count below
   `risk_limits.max_open_executors` and no unresolved open;
2. read the current `lp_executor` schema and require successful validation;
3. in loop mode, after validation and immediately before submit, journal the
   observed position identities, pool, range, calculated base/quote amounts,
   controller, and reason. Refresh the native wallet again after validation; the
   action text must persist that immediately pre-submit baseline as exact
   key/value fields: `base_mint`, `pre_base_balance`, `quote_mint`, and
   `pre_quote_balance`. Balances are plain decimal spendable wallet amounts at
   their current native token precision; planned LP deposit amounts are not
   baseline substitutes;
4. create at most one current-controller `lp_executor`: top-level exact
   `controller_id` and `account_name=<config.account_name>`; inside
   `executor_config`, `connector_name=<config.network>`,
   `lp_provider=<config.lp_provider>`,
   `swap_provider=<config.swap_provider>`,
   `trading_pair="BASE-<config.quote_token_symbol>"`, exact pool, aligned bounds,
   `side=3`, the calculator's floored BASE/QUOTE amounts, and
   `keep_position=false`;
5. retain the exact returned executor, transaction, and position identities.
   Refresh the executor and wallet immediately. A coherent exact
   current-controller executor is sufficient to confirm ownership and begin
   normal supervision. Do not call `inspect_orca_positions` for an LP open.

Every LP create call consumes `max_lp_deployments_per_tick`, including schema or
Gateway simulation rejection. Never open another LP in that tick.

## Reconciliation-Gated Retry

On the first tick after a failed LP create, use one exact executor-detail read
and one fresh wallet read. Classify the result `rejected_before_submit` only when
the exact current-controller executor is terminal `FAILED`, has no position
address, reports zero native actual LP base/quote amounts at token precision,
and the fresh balances for journaled `base_mint` and `quote_mint` equal
`pre_base_balance` and `pre_quote_balance`. Compare exact decimals after
normalizing both sides to each mint's freshly verified native precision.
Requested/configured amounts and initial-amount fallback fields are not actual
LP amounts.

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

Read `orca_lp_operations` before a close, cleanup, uncertain mutation, orphan,
or graceful stop.

For `CLOSE`, build the bounded exact target set described above. In loop mode,
journal all target executor/position IDs once, then call
`manage_executors(action="stop", controller_id=<exact current>,
executor_id=<one exact current-controller LP>, keep_position=false)` sequentially
for each target. Refresh after every call and stop the batch on uncertainty. Run
once permits only one target. When a close is terminal `FAILED` or its effect is
uncertain, reconcile that known exact position with post-lag Orca Stats
summary/history. A `close_outcome` of `closed` prevents another close;
`still_active` permits a corrected close no earlier than a later tick; and
`pending_index`, `uncertain`, or `unavailable` remains `HOLD`. Wallet and
affected token balances must still prove restoration. `keep_position=false`
neutralizes the LP executor's net token change
relative to its opening deposits; it does not prove that prepared BASE inventory
was sold to configured QUOTE. Never direct-close an orphan missing an exact
native executor; report its exact position for manual Gateway or Orca-UI
recovery.

After an LP close, abandoned confirmed preparation, or wind-down, block new
deployment until the affected chain is quote-clean. If one material residual
above `residual_base_dust_quote` is attributed by exact executor or finalized
transaction evidence, choose `CLEANUP`: after the LP executor's own close-out
swap is terminal, create one current-controller market `order_executor` SELL
whose `amount` is exactly that BASE amount in
`BASE-<config.quote_token_symbol>`, reconcile its exact ID, and refresh balances.
This is the exclusive cleanup swap path: trust the executor's internal
Gateway/Jupiter slippage protection, request no separate quote, and never call
`manage_gateway_swaps`.
Never sell total wallet balance, a broad delta,
another executor's inventory, old-session inventory, unattributed inventory, or
the configured SOL reserve. Ambiguity requires local quarantine/manual review.

Choose `STOP` only after wind-down has proven every exact current-session LP
terminal, every close and cleanup mutation reconciled, every touched base mint
at or below attributable dust, no unresolved capital, and the SOL reserve
intact. Then call only
`manage_trading_agent(action="stop_agent", agent_id=<exact current>)`.
External kills and manual hard stops bypass this workflow and require manual
inspection and cleanup; a later session never adopts the remains.

## Journal And Response

In loop mode write exactly one concise action entry per tick. A mutation entry
is its pre-submit intent; after execution do not add a second action entry. A
`HOLD` entry states the smallest blocking fact and next read-only evidence
needed. The sole cross-session-learning exception is one exact execution
blacklist after the preparation receipt rule above proves a strict threshold
breach; no other learning is authorized.

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

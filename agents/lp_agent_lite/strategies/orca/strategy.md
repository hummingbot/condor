---
name: orca
description: Stateless truth-first Orca Whirlpool strategy with MCDA selection, bounded native execution, two-level exits, and quote restoration.
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
  max_active_lp_positions: 3
  max_lp_deployments_per_tick: 1
  orca_stats_indexing_lag_seconds: 90
  max_slippage_pct: 1
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
  `max_active_lp_positions`. Journal the exact target set once, refresh after
  each stop, and stop the batch on uncertainty. Never run those stops
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
- positive LP/deployment caps and `candidate_scan_limit`;
- `orca_stats_indexing_lag_seconds >= 30`;
- `0 < minimum_range_half_width_pct <= maximum_range_half_width_pct`;
- positive position/session exit ratios and time limits;
- the five nonnegative `mcda_weights` sum exactly to `1`;
- `risk_limits.max_position_size_quote >= total_amount_quote` and
  `risk_limits.max_open_executors >= max_active_lp_positions + 1` so one
  serialized preparation or cleanup executor can coexist with the configured LP
  cap. Apply the tighter native risk ceiling if runtime risk state is lower.

`max_ticks: 0` is intentional: generic tick stopping does not perform quote
restoration. Session time limits trigger `WIND_DOWN`, followed by a gated native
`stop_agent`.

## Evidence Authority And Freshness

At the beginning of every tick, collect each broad surface once:

1. `get_portfolio_overview` with
   `account_names=[<config.account_name>]` and
   `connector_names=[<config.network>]`, fresh balances, LP positions and active
   orders enabled, perps disabled, and `refresh=true`.
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
5. For capacity, unresolved mutation, and shared-inventory facts, search with
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
reconciliation. When one exact position address is known, run
`inspect_orca_positions` with the configured wallet, exact position, expected
pool, intended `open_position` or `close_position`, mutation start timestamp,
and configured Stats lag. It compares Orca Stats exact `summary` with
position-filtered `history`. Require matching action, timestamp, position, and
pool after the lag window before treating the index as caught up. Empty results,
one-endpoint evidence, or disagreement are never position-absence proof.

Re-fetch the exact executor, finalized transaction when exposed, wallet, and
Orca Stats facts before deciding. Native finalized transaction evidence
overrides the eventually consistent index; both override prose and metrics.

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
  `capital_headroom_pct`, `current_price`, `lower_price`, `upper_price`,
  `tick_spacing`, `available_base`, `available_quote`, `base_decimals`, and
  `quote_decimals`. `feasible` means both floored LP legs are nonzero; it is not
  execution authorization. A positive `base_shortfall` means choose a separate
  later-tick `PREPARE`. `infeasible` means resize/rerange/reselect or `HOLD`.
- `inspect_orca_positions`: pass `wallet_address`, `position_address`, optional
  `expected_pool_address`, and the paired `expected_action_type` plus
  `mutation_started_at`; the timestamp is an integer Unix epoch in seconds, not
  ISO text or milliseconds. Set `indexing_lag_seconds` from Strategy config. Use
  `active`/`closed` only with matching event consensus. `not_indexed`, one
  endpoint, disagreement, or `unavailable` never proves on-chain absence.
- `snapshot_lp_metrics`: pass only already observed `controller_id`, injected
  `tick`, `session_pnl_quote`, `quote_balance`, `sol_balance`,
  de-duplicated `positions`, `residuals`, and optional `last`. Never fabricate a
  missing metric or pass an exit decision the routine cannot yet know. Read
  compact tuples by their returned `p_cols`/`r_cols`, not a remembered index.
  Loop success writes the bounded authoritative metrics payload once, excluding
  response-only `report_id`/`report_error`; experiment success is an unwritten
  metrics preview even though its Condor diagnostic report may be saved. Require
  `artifact_write=true` only for loop
  `status="complete"`; `mutation=false` means no trading/external transition.
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
5. only then consider one registration, `PREPARE`, or `OPEN` transition;
6. otherwise `HOLD`.

An uncertainty blocks only its shared authority chain. It must not suppress an
independently proven exit or safe read-only supervision for another position.
No deployment is allowed while a current-session close, restoration, cleanup,
or preparation outcome is unresolved.

## Ownership, Capacity, And Capital

Build the best current wallet-wide count from de-duplicated HAPI-visible active
positions, exact current-controller executors, any exact Orca Stats position
indexed active, and every unresolved open that may have landed. Every known
nonterminal Orca position, including older, foreign, untracked, submitted, or
uncertain positions, consumes `max_active_lp_positions`. Orca Stats exact mode
does not claim a complete wallet scan, so never subtract capacity merely because
an address was not indexed.

Only exact executors created by the current controller may be supervised,
stopped, or assigned PnL. A pool/range similarity or one wallet balance is not
ownership. Older and foreign positions are observation-only even though they
reduce capacity and available wallet balances. A new session never adopts,
resumes, closes, or cleans an old session.

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
Fresh wallet balances and `min_sol_reserve` remain hard constraints.

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

Deployment is loop-only. It is eligible only when exits and cleanup are clear,
wallet-wide LP count is below the configured cap, the same Agent/Strategy wallet
scope has no freshly observed conflicting mutation, remaining session capital
and native executor risk capacity are positive, session exit facts are complete,
and no unresolved mutation can consume the same authority. Older or foreign
positions alone are capacity facts, not proof of a concurrent mutation.

### Select

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
4. Compare fee productivity, recent activity, price stability, liquidity depth,
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

Choose a range within the configured half-width bounds from current pool price,
tick spacing, volatility, fee opportunity, and inventory exposure. Then run
`calculate_lp_requirements` for that already-selected pool/range using the exact
keys `selected_allocation_quote`, `remaining_session_quote`, and
`max_amount_quote_per_lp_position`, plus fresh balances, precision,
`capital_headroom_pct`, and current pool mechanics.

Ordinary shortages are feasibility facts. Accept a smaller meaningful result,
choose another valid range/candidate, or `HOLD`; do not require an old estimate
to match. Invalid identities, non-finite values, impossible bounds, negative
balances, capital breach, and SOL-reserve breach reject the affected action.

### Prepare

If the calculator returns a material exact base shortfall, choose `PREPARE`:

1. read the current `order_executor` schema;
2. require a schema/provider field or authoritative current connector setting
   that proves the submitted swap's slippage ceiling is at most
   `max_slippage_pct`; the checked-out `order_executor` schema has no such field,
   so without additional live evidence classify native swap execution
   `unsupported` and `HOLD` rather than assuming its default;
3. journal exact loop-mode intent before submit;
4. require the live schema to support the preparation request described in
   `orca_lp_operations`, then create one current-controller market BUY whose
   `amount` is exactly the calculated BASE shortfall in BASE units;
5. retain its exact executor ID and reconcile it;
6. refresh wallet and executor evidence, then end the tick.

Requested or `executed_amount_base` is not authoritative received amount from
the configured swap provider. The later tick recalculates from the true
available balance. A rejected pre-submit preparation may be corrected only after
fresh facts; a submitted or uncertain one is never duplicated.

### Open

When fresh calculation requires no preparation:

1. immediately refresh native wallet, executor, and known exact-position facts;
   require the de-duplicated capacity count below its cap and no unresolved open;
2. in loop mode journal the observed position identities, pool, range, calculated
   base/quote amounts, controller, and reason before submit;
3. read the current `lp_executor` schema and require successful validation;
4. create at most one current-controller `lp_executor`: top-level exact
   `controller_id` and `account_name=<config.account_name>`; inside
   `executor_config`, `connector_name=<config.network>`,
   `lp_provider=<config.lp_provider>`,
   `swap_provider=<config.swap_provider>`,
   `trading_pair="BASE-<config.quote_token_symbol>"`, exact pool, aligned bounds,
   `side=3`, the calculator's floored BASE/QUOTE amounts, and
   `keep_position=false`;
5. retain the exact returned executor, transaction, and position identities.
   Refresh the executor and wallet immediately. Once an exact position address
   is known, use `inspect_orca_positions` after the configured Stats indexing
   window to reconcile the wallet/position/pool and intended open action.

Every LP create call consumes `max_lp_deployments_per_tick`, including schema or
Gateway simulation rejection. Never open another LP in that tick.

Matching Orca Stats summary/history after the mutation time confirms that the
wallet/position/pool open was indexed. Combine it with the exact returned
executor or transaction identity before current-session attribution. Multiple
candidate identities, Stats disagreement, external wallet activity, or
contradictions are ambiguous.

## Reconciliation-Gated Retry

Before any corrected LP create, combine exact executor evidence, exact finalized
transaction or Gateway outcome when available, wallet balances, and post-lag
Orca Stats evidence for every known candidate position identity.

- Matching indexed open after the mutation time: never retry. Supervise only if
  current-session attribution and HAPI lifecycle support are exact; otherwise
  count and quarantine the untracked position for manual close.
- Terminal no-effect: require explicit pre-submit/schema/simulation rejection or
  an exact finalized failed atomic transaction, no active/submitted matching
  executor, fully refreshed admission facts, and a material correction. Permit
  one new create no earlier than a later tick. Stats `not_indexed` is not part of
  no-effect proof.
- Submitted, still-landable, uncertain, ambiguous, unavailable, contradictory,
  or incomplete: no retry. Continue read-only reconciliation.

An executor label of `failed`, an absent HAPI row, or an empty position response
is never sufficient alone. An insufficient-funds Gateway simulation is terminal
no-effect only when no Solana transaction was submitted; refresh balances,
recalculate/downsize, correct the shortfall, and wait until the next tick.

## Close, Cleanup, And Stop

Read `orca_lp_operations` before a close, cleanup, uncertain mutation, orphan,
or graceful stop.

For `CLOSE`, build the bounded exact target set described above. In loop mode,
journal all target executor/position IDs once, then call
`manage_executors(action="stop", controller_id=<exact current>,
executor_id=<one exact current-controller LP>, keep_position=false)` sequentially
for each target. Refresh after every call and stop the batch on uncertainty. Run
once permits only one target. Reconcile every exact executor independently with
matching post-lag Orca Stats summary/history. Indexed `close_position` prevents
another close, but wallet and affected token balances must still prove
restoration. `keep_position=false` neutralizes the LP executor's net token change
relative to its opening deposits; it does not prove that prepared BASE inventory
was sold to configured QUOTE. Never direct-close an orphan missing from HAPI;
report its exact position for manual Gateway or Orca-UI recovery.

After an LP close, abandoned confirmed preparation, or wind-down, block new
deployment until the affected chain is quote-clean. If one material residual
above `residual_base_dust_quote` is attributed by exact executor or finalized
transaction evidence, choose `CLEANUP`: after the LP executor's own close-out
swap is terminal, require the same explicit slippage-ceiling evidence as
`PREPARE`, then create one current-controller market `order_executor` SELL
whose `amount` is exactly that BASE amount in
`BASE-<config.quote_token_symbol>`, reconcile its exact ID, and refresh balances.
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
is its pre-submit intent; after execution do not add a second entry. A `HOLD`
entry states the smallest blocking fact and next read-only evidence needed.
Never write cross-session learning.

End with the chosen decision, exact affected identities, whether mutation was
attempted, its precise outcome class, quarantined capacity/capital, and the next
permitted transition. In run once put all intent and reconciliation in the
experiment response. Dry run reports the conditional decision and required
registration/preparation without mutating.

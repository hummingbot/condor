---
name: orca_lp_operations
description: Operate one exact Orca LP lifecycle through native executors, failed-close Orca Stats reconciliation, quote restoration, and progress-gated retry without a local controller.
when_to_use: Read before preparation, LP open or close, residual cleanup, graceful wind-down, or reconciliation of a failed LP operation.
source: agent:lp_agent_lite
---

# Orca LP Operations

This is a reactive playbook, not a state machine. Begin every use from the exact
current controller, frozen config, and fresh executor and wallet evidence. Use
Orca Stats only after a failed or uncertain close: compare exact `summary` with
exact-filtered `history` for the already-known position after the configured
indexing delay. Do not use it to discover or reconcile an LP open. An exact
coherent current-controller executor is sufficient for normal supervision.
Earlier intent narrows what to reconcile but never replaces current evidence.

Each Lite routine attempts a Condor diagnostic report only after its result is
final. Read the compact JSON result itself for operational truth. Its
`report_id`/`report_error` fields are for human review only; a missing report or
report failure never changes an LP, swap, cleanup, registration, retry, or stop
decision and never justifies repeating an operation.

## Mode And Ownership Gate

- Dry run is operationally read-only; propose the operation without
  registration, executor, journal, metrics-file, wallet, or Agent mutation. A
  best-effort Condor diagnostic report is allowed because it has no trading
  authority and is not Agent state.
- Run once never starts preparation or a new LP. It may perform one exactly
  attributable risk-reducing close or cleanup only when same-tick reconciliation
  is possible; unresolved restoration becomes explicit manual follow-up.
- Loop may progress one bounded lifecycle decision per tick under the exact
  `lp_agent_lite.orca_N` controller.

Mutate only an exact current-controller executor. Older, foreign, and untracked
positions are observation-only; they do not consume the current-session target
or executor risk count, but their inventory reduces available wallet balances.
Shared-wallet balances are aggregate feasibility facts, never per-position
ownership.

The configured deployment quota counts LP create calls only. Registration,
preparation BUY, LP create, cleanup SELL, and Agent stop remain separate and are
not combined in one tick. In loop mode only, one `CLOSE` decision may stop a
bounded set of independently triggered exact current-session LPs sequentially,
up to `risk_limits.max_open_executors`. Journal the exact target set once,
refresh after each stop, and end the batch on uncertainty. Never run stops
concurrently or combine that close batch with another transition category.
`WIND_DOWN` is a posture, not a tool action.

## Token Orientation And Order Semantics

For every pool handled by this Strategy, bind the following from the current
config before constructing any native request:

- `QUOTE` is the exact configured quote symbol, mint, and decimals; `BASE` is the
  pool's other token;
- `trading_pair` is exactly `BASE-<config.quote_token_symbol>`;
- price is QUOTE per BASE;
- `order_executor.amount` is always BASE-token units, for both BUY and SELL;
- preparation is `side=1` (`BUY` BASE using configured QUOTE);
- cleanup is `side=2` (`SELL` BASE for configured QUOTE);
- an order executor's `connector_name` is `<config.network>`, not the provider
  identifier; its swap provider is `<config.swap_provider>`;
- every executor's top-level account is `<config.account_name>`.

Reject a reversed/uncertain pair or amount unit. Read the current executor
schema before create and use only fields it accepts. Preparation BUYs and cleanup
SELLs use a native current-controller `order_executor` exclusively. Trust the
configured Gateway/Jupiter execution path's internal slippage protection. Do not
invent or pass a `slippage` field, do not request a separate swap quote, and
never call `manage_gateway_swaps` for any action.

## Preparation

1. Require one selected and natively verified Orca pool, exact token registry
   truth, available current-session executor risk capacity, and current
   capital/reserve evidence.
2. Immediately refresh the exact selected Orca pool through native
   `explore_dex_pools(action="get_pool_info")`; require its exact orientation,
   finite positive current price, and tick spacing. Run
   `calculate_lp_requirements` for the selected pool/range with those mechanics,
   configured `lp_open_balance_buffer_pct`, and
   `allow_base_preparation=true`. Use its floored amounts and buffered exact
   base shortfall; do not reconstruct them from prose or substitute
   scanner/GeckoTerminal prices.
3. If a material shortfall exists, record one loop intent containing the exact
   pool, BASE mint, pair, requested BASE shortfall, and configured receive
   threshold, then submit one native current-controller market
   `order_executor`. Pass exact `controller_id` and
   `account_name=<config.account_name>` at tool top level. Its config uses
   `type="order_executor"`, `connector_name=<config.network>`,
   `trading_pair="BASE-<config.quote_token_symbol>"`, `side=1`,
   `amount=<base_shortfall in BASE units>`, and
   `execution_strategy="MARKET"`, subject to the live schema.
4. Retain its exact executor ID and fetch that exact executor. If it is
   nonterminal, submitted, or uncertain, end the tick and reconcile later. If
   confirmed terminal, require positive `executed_amount_base`. For the
   checked-out Gateway connector this is the realized received BASE derived from
   settled token changes. Refresh the wallet for spendable LP inventory; do not
   require the realized amount to equal the request.
5. End the tick. On a later tick, refresh wallet balances, pool price, and range
   composition and run the calculator again with
   `allow_base_preparation=false`. Fit the LP to the actual balances while
   preserving capital headroom and the configured per-token open buffer. A small
   receive difference is normal: downsize to a meaningful feasible LP instead
   of declaring preparation failed or submitting a dust top-up swap. Never swap
   and open in one tick.

Compare the journaled preparation order's positive requested BASE amount with
the confirmed executor's positive realized `executed_amount_base`:

```text
receive_difference_pct =
  abs(executed_amount_base - requested_base_amount) / requested_base_amount * 100
```

At or below `preparation_receive_difference_blacklist_pct`, use the realized
amount and continue with fresh sizing. Strictly above it, require exact matching
controller, executor, pool, pair, BASE mint, the same positive requested amount,
and non-contradictory wallet evidence; then `HOLD`, exclude that pool and BASE
mint immediately, and write one
`category="execution"` learning exactly as
`BLACKLIST_POOL=<pool> BLACKLIST_TOKEN=<base_mint> DIFF_PCT=<value>
LIMIT_PCT=<configured_limit>`. Equality never blacklists. Missing, zero,
nonterminal, uncertain, ambiguous, or contradictory evidence never blacklists;
reconcile instead.

If preparation is confirmed but LP creation becomes authoritatively impossible,
restore only the exact attributable prepared base amount to configured QUOTE.
Never infer restoration amount from total wallet balance.

## LP Open

1. Immediately before submit, require the latest calculator result used
   `allow_base_preparation=false` after any confirmed preparation and that its
   `base_balance_required` and `quote_balance_required` fit the refreshed wallet.
   Refresh native wallet and executor facts; require the current-session
   open-executor count below `risk_limits.max_open_executors` and no unresolved
   open.
2. Read the live `lp_executor` schema. If schema retrieval or validation is
   unavailable, `HOLD`; never rely on skipped validation.
3. After validation and immediately before submit, record loop intent with the
   exact pool/range, calculated amounts, controller, and reason. Refresh the
   native wallet again after validation and persist that baseline in the action
   text as exact `base_mint`, `pre_base_balance`, `quote_mint`, and
   `pre_quote_balance` key/value fields. Use plain decimal spendable balances at
   their current native token precision; planned LP deposit amounts are not
   baseline substitutes.
4. Submit at most one create with the exact current controller and
   `account_name=<config.account_name>` at tool top level, plus
   `executor_type="lp_executor"`. Inside `executor_config`, use
   `type="lp_executor"`, `connector_name=<config.network>`,
   `lp_provider=<config.lp_provider>`,
   `swap_provider=<config.swap_provider>`,
   `trading_pair="BASE-<config.quote_token_symbol>"`, exact pool, aligned
   `lower_price`/`upper_price`, double-sided `side=3`, calculator
   `base_amount`/`quote_amount`, and `keep_position=false`. Do not substitute a
   DEX/provider name for `connector_name` or put top-level authority only inside
   config.
5. Record returned executor, transaction, and position identities immediately.
   Refresh executor and wallet evidence. If the exact current-controller
   executor coherently reports the position and lifecycle, supervise it directly.
   Do not call `inspect_orca_positions` for an LP open.

One create call consumes the per-tick deployment quota even when rejected by
schema or Gateway simulation. Do not submit a second pool that tick.

## Progress-Gated Retry

Classify the result:

- `rejected_before_submit`: no mutation was submitted;
- `submitted` or `confirmed`: an external identity/effect exists;
- `uncertain`: an effect may exist but is not exact;
- `ambiguous`: several or contradictory matches exist;
- `unavailable`: required truth is incomplete or unreachable.

For LP open only, reconcile once on the first later tick with one exact
executor-detail read and one fresh wallet read. A result is
`rejected_before_submit` when the exact current-controller executor is terminal
`FAILED`, has no position address, reports zero native actual LP base/quote
amounts at token precision, and fresh balances for journaled `base_mint` and
`quote_mint` equal `pre_base_balance` and `pre_quote_balance`. Normalize exact
decimals to each mint's freshly verified native precision. Do not treat
requested/configured amounts or initial-amount fallback fields as actual LP
amounts.

When those gates pass, continue in that same reconciliation tick: refresh all
remaining admission facts, rerun sizing with `allow_base_preparation=false`,
and submit one corrected OPEN after its sole action intent is written. Do not
spend a separate `HOLD` tick merely recording the classification. That intent
names the failed executor, states `rejected_before_submit`, records the corrected
range/amounts, and persists a new four-field wallet baseline. At least one of
the corrected `base_amount` or `quote_amount` must be lower than the failed
request at token precision, both buffered requirements must fit, and no
arbitrary percentage reduction is required.

Any position address, nonzero native actual LP amount, or exact-mint balance
change is `uncertain` and forbids retry. Missing any of the four journal fields
or an actual-amount observation is `unavailable` and remains `HOLD`; metrics are
never a baseline substitute. Do not call Orca Stats, require transaction
evidence, search the same logs again, or promise exact-position recovery without
an address. The failed create consumed its original tick's quota, so the one
corrected create occurs only on this or another later tick and consumes that
tick's quota. Never repeat an identical request.

## Per-Position Close

1. Build a bounded target set containing only exact current-controller LP
   executors with a verified configured take-profit, stop-loss, time-limit, or
   explicit risk-reducing reason. In run once the set may contain only one target.
2. Record one loop intent listing every exact executor and position in the set.
3. Submit `manage_executors(action="stop", keep_position=false)` sequentially for
   each exact controller/executor pair. Refresh the affected exact executor and
   wallet after each result; stop the batch immediately if any result is not a
   proven non-ambiguous submission/confirmation. Each native stop may internally
   perform remove-liquidity and its close-out swap.
4. If a close is terminal `FAILED` or its effect remains uncertain, run
   close-only `inspect_orca_positions` for that exact known position. Pass the
   required exact wallet, position, pool, mutation-start Unix timestamp, and
   configured lag; there is no action selector. Consume `close_outcome`
   deterministically: `closed` forbids another stop; `still_active` permits a
   corrected close no earlier than a later tick; and `pending_index`,
   `uncertain`, or `unavailable` remains `HOLD`. Never repeat a possibly
   submitted close without this classification.
5. Indexed close consensus proves the indexed lifecycle effect but not token
   delivery or quote restoration. Refresh wallet balances and native results.

If Orca Stats shows an active position without an exact native executor that can
own and close it, do not adopt or hide a direct Gateway mutation inside a
routine. Count it, quarantine conflicting deployment, and request manual
exact-position recovery through Gateway or Orca UI.

## Quote Restoration

Native LP close with `keep_position=false` closes the on-chain position and
then attempts to neutralize the LP round trip's net BASE difference relative to
the amounts deposited. It does **not** sell the original prepared BASE deposit
and does not by itself make the session all-QUOTE. Verify executor `COMPLETE`,
wallet balances, and attribution rather than assuming quote restoration.

- While close-out is pending, reconcile read-only and do not race it with a
  cleanup order.
- If no exactly attributable base value exceeds
  `residual_base_dust_quote`, mark only that action chain quote-clean.
- If one material amount is attributed by exact executor or finalized
  transaction evidence, record intent and submit one native current-controller
  market `order_executor` SELL. Pass exact top-level controller and
  `account_name=<config.account_name>`. Its config uses
  `connector_name=<config.network>`,
  `trading_pair="BASE-<config.quote_token_symbol>"`, `side=2`,
  `amount=<attributable BASE units>`, and `execution_strategy="MARKET"`, subject
  to the live schema. Reconcile its exact identity and actual wallet result.
- If attribution or cleanup submission is uncertain, quarantine that chain.

An attributable amount must come from a finalized current-session transaction's
token changes or a native executor field whose documented meaning is actual,
not requested. A serialized exact-mint before/after balance change is usable only
when it is tied to that executor/transaction and the shared-wallet exclusivity
check proves no other balance-changing controller was live. Cap any SELL by the
fresh available BASE balance. If only an aggregate balance, requested amount, or
unproven delta exists, classify it `unattributed` and do not sell it.

Never sell the wallet's whole balance, a broad delta, pre-existing holdings,
another executor's inventory, an old session's assets, an unattributed token, or
the protected SOL reserve. No new preparation/open and no clean stop are allowed
while any current-session residual chain remains material or unresolved.

## Wind-Down And Stop

Once a whole-session TP, SL, time limit, or graceful-end request is proven,
disable registration, preparation, and open. Close exact current-session LPs,
using the bounded sequential close exception where safe, reconcile each
independently, restore every attributable base residual in later separately
admitted cleanup work, and verify all affected facts. Only after every LP is
terminal and every inventory chain is quote-clean may the Strategy call
exact-current `stop_agent`.

An external kill or manual hard stop bypasses this path and therefore requires
manual inspection and cleanup. A later session may observe the remains for
balance and attribution safety but never adopt, close, or clean them.

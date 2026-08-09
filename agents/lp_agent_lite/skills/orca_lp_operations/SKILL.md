---
name: orca_lp_operations
description: Operate one exact Orca LP lifecycle through native executors, post-lag Orca Stats reconciliation, quote restoration, and progress-gated retry without a local controller.
when_to_use: Read before preparation, LP open or close, residual cleanup, graceful wind-down, or reconciliation of any submitted, uncertain, failed, or HAPI-missing LP operation.
source: agent:lp_agent_lite
---

# Orca LP Operations

This is a reactive playbook, not a state machine. Begin every use from the exact
current controller, frozen config, and fresh executor and wallet evidence. For
one known position, compare Orca Stats exact `summary` with exact-filtered
`history` after the configured indexing delay. Earlier intent narrows what to
reconcile but never replaces current evidence.

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
positions are observation-only and still consume wallet capacity. Shared-wallet
balances are aggregate feasibility facts, never per-position ownership.

The configured deployment quota counts LP create calls only. Registration,
preparation BUY, LP create, cleanup SELL, and Agent stop remain separate and are
not combined in one tick. In loop mode only, one `CLOSE` decision may stop a
bounded set of independently triggered exact current-session LPs sequentially,
up to `max_active_lp_positions`. Journal the exact target set once, refresh after
each stop, and end the batch on uncertainty. Never run stops concurrently or
combine that close batch with another transition category. `WIND_DOWN` is a
posture, not a tool action.

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
schema before create and use only fields it accepts. Do not invent a `slippage`
field. The checked-out native `order_executor` schema has no per-call slippage
field, so its existence or a familiar connector default is not proof. Submit a
preparation or cleanup swap only when the live schema exposes a supported bound
or authoritative current provider/connector evidence proves the applied ceiling
is no greater than `max_slippage_pct`. Otherwise classify native swap execution
as `unsupported`, choose `HOLD`, and name the missing evidence. Never weaken the
configured cap to make the swap possible.

## Preparation

1. Require one selected and natively verified Orca pool, exact token registry
   truth, clean de-duplicated lifecycle capacity, and current capital/reserve
   evidence.
2. Run `calculate_lp_requirements` for the selected pool/range. Use its floored
   amounts and exact base shortfall; do not reconstruct them from prose.
3. If a material shortfall exists, record one loop intent and submit one native
   current-controller market `order_executor`. Pass exact `controller_id` and
   `account_name=<config.account_name>` at tool top level. Its config uses
   `type="order_executor"`, `connector_name=<config.network>`,
   `trading_pair="BASE-<config.quote_token_symbol>"`, `side=1`,
   `amount=<base_shortfall in BASE units>`, and
   `execution_strategy="MARKET"`, subject to the live schema.
4. Retain its exact executor ID and fetch that exact executor. If it is
   nonterminal, submitted, or uncertain, end the tick and reconcile later. If
   terminal, refresh the wallet; a requested or reported `executed_amount_base`
   is not authoritative received inventory.
5. End the tick. On a later tick, refresh wallet balances, pool price, and range
   composition and run the calculator again. Never swap and open in one tick.

If preparation is confirmed but LP creation becomes authoritatively impossible,
restore only the exact attributable prepared base amount to configured QUOTE.
Never infer restoration amount from total wallet balance.

## LP Open

1. Immediately before submit, refresh native wallet, executor, and every known
   exact-position fact; require capacity below the configured cap and no
   unresolved open.
2. Record loop intent with that observed baseline, exact pool/range, calculated
   amounts, controller, and reason.
3. Read the live `lp_executor` schema. If schema retrieval or validation is
   unavailable, `HOLD`; never rely on skipped validation.
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
   Refresh executor and wallet evidence. Once the exact position is known, run
   `inspect_orca_positions` after the configured Stats indexing window with the
   wallet, position, pool, intended action, and pre-submit mutation timestamp as
   integer Unix epoch seconds.

One create call consumes the per-tick deployment quota even when rejected by
schema or Gateway simulation. Do not submit a second pool that tick.

## Reconcile Open Evidence

Use the three independent surfaces together:

| Executor/HAPI | Orca Stats after lag | Interpretation |
| --- | --- | --- |
| Exact current executor | Summary/history agree on matching indexed open | Supervise after all identities agree |
| Missing row/executor | Matching indexed open | Open effect exists; never retry; count and quarantine if native lifecycle support is absent |
| Authoritative pre-submit rejection or finalized failed transaction | Empty, old, or not indexed | Terminal no-effect may permit a corrected later-tick attempt; Stats absence contributes no proof |
| Partial, contradictory, still indexing, or several identities | Disagreement or uncertain | No retry; read-only reconciliation/manual review |

Matching Stats evidence proves that Orca's event index associates the exact
wallet, position, pool, and action; the history row also supplies the
transaction signature. Combine it with the exact
executor or finalized transaction before current-session attribution. Empty
results are `not_indexed`, never authoritative absence.

## Progress-Gated Retry

Never treat an executor `failed` label as retry permission. Classify the result:

- `rejected_before_submit`: no mutation was submitted;
- `submitted` or `confirmed`: an external identity/effect exists;
- `uncertain`: an effect may exist but is not exact;
- `ambiguous`: several or contradictory matches exist;
- `unavailable`: required truth is incomplete or unreachable.

Only a terminal no-effect result may be retried, and no earlier than a later
tick. It must come from authoritative pre-submit rejection or a finalized failed
transaction—not an empty or lagging Stats response. Require no active/submitted
matching executor, refreshed balances, registry, pool/range, capital, capacity,
reserve, and exit facts, plus a material correction or proven transient
recovery. Never repeat an identical request against unchanged facts and never
use a global retry counter.

An insufficient-funds `TransferChecked` Gateway simulation is safe no-effect
only when no Solana transaction was submitted. Refresh actual balances, retain
headroom, recalculate, correct the shortfall, and wait for the next tick. If the
position instead exists on Orca, reconciliation replaces retry.

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
4. Reconcile each executor independently and require matching post-lag Stats
   `close_position` summary/history. Never repeat a possibly submitted stop.
5. Indexed close consensus proves the indexed lifecycle effect but not token
   delivery or quote restoration. Refresh wallet balances and native results.

If Orca Stats shows an active position without the HAPI row required by native
close, do not adopt or hide a direct Gateway mutation inside a routine. Count
it, quarantine conflicting deployment, and request manual exact-position
recovery through Gateway or Orca UI.

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
capacity but never adopt, close, or clean them.

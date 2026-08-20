# Gateway issues

Found against gateway `fdad604aa`, Solana mainnet-beta, wallet `82Sgg…yHx5`.
Now merged with the route-unification work on `feat/unified-trading-routes`, which
changed every path these issues live on.

**Status at a glance**

| | Issue | State |
|---|---|---|
| GW-1 | Meteora `quote-liquidity` priced the range midpoint | fixed (re-implemented) |
| GW-2 | Raydium AMM `feePct` reported as a fraction | fixed |
| GW-3 | OKX router has no credentials | **needs a decision** — error made legible |
| GW-4 | Native-SOL amounts inflated by the transaction fee | fixed, plus 3 follow-on sites |
| GW-5 | Execute response carried no `poolAddress` | fixed |
| GW-6 | AMM add never returned the DAMM v2 position it created | fixed |
| GW-7 | CLMM/AMM double-counted the fee after GW-4 | fixed (found by GW-4) |
| GW-0 | `poolAddress` pin survived the refactor | no action |
| GW-8 | Nested `data` schemas named but never registered | fixed |
| GW-9 | Request bodies declared inline, so ungeneratable | fixed |
| GW-10 | The reads published a stale shape under the right name | fixed |
| GW-11 | The chain half of `chainNetwork` was decorative on the liquidity routes | fixed |
| GW-12 | Three ways a caller's intent is dropped without an error | **open** |
| GW-13 | The spec guard passes on regressions it claims to catch | fixed |
| GW-14 | No `operationId` and no error responses in the spec | fixed |
| GW-15 | Response component names were never unified | fixed |
| GW-16 | Leftovers the unification did not sweep | fixed (partly) |
| GW-17 | `baseTokenAmountAdded` is signed on some connectors, a magnitude on others | fixed |
| GW-18 | pancakeswap-sol's close reports fees and rent as a hardcoded 0 | **open** |
| GW-19 | A hyphen in a token symbol makes its pair unquotable (hummingbot-api) | fixed |
| GW-20 | A DAMM v2 open records the position rent as deposited liquidity | fixed |
| GW-21 | Nothing downstream can close an AMM position, so rent is stranded | fixed (routes collapsed) |
| GW-22 | `position_info` ignored the position it was given (condor) | fixed |
| GW-23 | Money is typed as JSON `number`, so exact decimals do not survive | **open** |
| GW-24 | The committed spec carried a real wallet address and a local port | fixed |
| GW-25 | A narrow in-range CLMM close fails on slippage, and no layer widens it | **open** |
| GW-26 | A transaction that lands and reverts is never recorded, though it costs gas | **open** |
| GW-27 | `/trading/router/execute-quote` is reachable from nothing | **open** |
| GW-28 | pancakeswap-sol's open spends the slippage bound, then fails by one unit | **open** |

Ten are open and are written out in full below; the eighteen that are fixed are
summarised under **Fixed — the record**, with the verification each still needs collected
under **Outstanding verification**.

---

## Route changes that affect every issue below

The trading type is now a path segment and the connector a parameter. The spec went
from 182 paths to 54, and `openapi.json` is generated from the route table by
`pnpm generate:openapi` without a running server.

| Was | Is |
|---|---|
| `/connectors/{dex}/{type}/*` (128 paths) | removed |
| `/trading/swap/{quote,execute}` | `/trading/router/{quote-swap,execute-quote,execute-swap}` |
| `/connectors/{dex}/{amm,clmm}/quote-swap` | `/trading/{amm,clmm}/quote-swap` |
| `/connectors/{meteora,orca}/clmm/fetch-pools` | `/trading/clmm/fetch-pools` |
| `/trading/clmm/quote-position` | `/trading/clmm/quote-liquidity` |
| `/trading/amm/{add,remove}-liquidity` | `/trading/amm/{add,remove}` |
| — | `/trading/amm/{open,close}` (new) |
| `/chains/{solana,ethereum}/*` (14 paths) | `/chains/{chain}/*` (6) + 2 EVM-only |

`connector` is now a bare, enum-constrained name (`meteora`, not `meteora/clmm`).
Both callers have since been updated: hummingbot-api vendors this spec, generates its
models from it, and builds every `/trading` request from them; the hummingbot wheel does
the same through `gateway_http_client`. Neither still speaks the old routes.

---

## Outstanding verification

Carried over from the fixed entries below, none of which has been re-run since it landed.
Everything here needs a container built from current `main`.

- **GW-1** — quote a Meteora range that does *not* straddle spot; the paired amount should
  follow the active bin, not the midpoint.
- **GW-2** — read `pool-info` for a Raydium v4 pool and a CPMM pool; both should report a
  percent.
- **GW-4** — repeat three 0.01 SOL swaps; `input_amount` should be 0.01 exactly, with the
  fee reported separately.
- **GW-6** — open a DAMM v2 position via `/trading/amm/add` with no `positionAddress` and
  confirm the response names the position it created.
- **GW-17** — repeat the Raydium add/remove round trip and read
  `POST /gateway/amm/events/search`; both rows should be positive and net to `remove − add`.
  Rows written before the fix keep their old signs.
- **GW-22** — the position opened for GW-20, `F1YcTMd6…`, is still open and still holds its
  rent; `position_info` on it should answer about it alone.

**Blocked until the hummingbot-api container is rebuilt.** As of 2026-08-20 the running
container predates `55cf09e`, so the live `gateway_amm_positions` table has neither rent
column and `POST /gateway/amm/positions/search` answers 500 with
`'GatewayAMMPosition' object has no attribute 'position_rent'`. This is a stale image, not
a defect: the model carries both columns and `database/connection.py` carries the ALTER for
both tables in its migration list. Nothing on the AMM side can be verified until the
container is built from current `main`. An earlier note in this file called it a live bug —
it is not.

---

## Still open

In full, worst first. GW-18 is a wrong number and GW-26 is a missing row; the rest are ways
a wrong request is accepted quietly, or a right one is described badly, and GW-3 is a
decision rather than a fix. GW-12 is being fixed in a parallel session.

---

## GW-28 — pancakeswap-sol's open spends the slippage bound, then fails by one unit
**Status: open.** One connector, one route. Found 2026-08-20 on the first attempt to open a
pancakeswap-sol position, which was being opened to unblock GW-18. It failed at simulation,
so it cost nothing — the wallet was unchanged at 2.573201337 SOL afterwards.

### What happened

```
Quote: baseLimited=false, base=0.009856058065771249, quote=0.8742539984898908
Quote Max: base=0.010053179227086673, quote=0.8917390784596887
Amounts with slippage (2%):
  amount0Max: 10053179 (SOL)
  amount1Max: 891739 (USDC)
Base Flag: false (amount1 is base)
```

and on chain:

```
AnchorError thrown in programs/amm/src/instructions/open_position.rs:575.
Error Code: PriceSlippageCheck. Error Number: 6021.
Left: 891739
Right: 891740
```

One unit of USDC — 0.000001 — against a 2% tolerance that should have allowed 17,000 of
them.

### Why the tolerance did not absorb it

`baseLimited=false` sets `base_flag=false`, which tells the program to derive the
position's liquidity **from `amount1Max`**. That is the slippage-inflated *maximum*, and
using it as the defining amount is what breaks the check:

1. `amount1Max` = 891739 is handed in as the amount to size liquidity from.
2. The program computes liquidity `L` from 891739.
3. It then computes the deposit `L` actually requires, rounding **up** in the pool's
   favour, and gets 891740.
4. It asserts `required <= max` — 891740 <= 891739 — and fails.

The maximum and the amount are the same number, so the comparison has no headroom by
construction. Any upward rounding in that round trip fails it. This is GW-24's defect on a
second connector: the bound is spent as the deposit rather than reserved above it, so the
2% buys nothing. **Raising `slippagePct` does not help** — a larger bound is simply a larger
deposit, and step 4 still compares a number against itself.

### Why it is intermittent

Which way it lands is decided by a float:

```ts
const amount1Max = new BN((quote.quoteTokenAmountMax * 10 ** quoteToken.decimals).toFixed(0));
```

`0.8917390784596887 × 1e6` is 891739.078…, and `.toFixed(0)` rounds it **down** to 891739,
discarding the 0.078 that would have covered the program's round-up. Had the fraction been
≥ 0.5 it would have rounded up, left a spare unit, and the open would have succeeded. So
the connector fails or succeeds on the fractional part of a float — roughly a coin flip per
attempt, which is why it has appeared to work before.

That makes this the concrete consequence of GW-23. GW-23 is filed as a fidelity problem —
exact decimals not surviving the wire — and this is what it costs when the number reaching
the chain is one unit short of a strict inequality.

### The fix

Size liquidity from the *requested* amount and pass the inflated figure only as the
maximum, which is what a maximum is for: derive `L` from `quote.quoteTokenAmount`
(0.874254) and send `amount1Max` = 891739. The program's round-up then lands well inside
the bound instead of one unit outside it. Rounding the max **up** rather than to nearest
would stop this particular failure, but it treats the symptom — the tolerance would still
be fully consumed, and the position would still deposit the maximum every time.

### Consequence for GW-18

GW-18 is still blocked and now blocked on this: no pancakeswap-sol position can be opened
reliably, so its close cannot be observed. Retrying with slightly different amounts re-rolls
the rounding and should eventually land one.

---

## GW-26 — a transaction that lands and reverts is never recorded, though it costs gas
**Status: open.** hummingbot-api, both position routers. Found 2026-08-20 by counting rows
rather than by hitting an error: the database says every operation ever attempted
succeeded, and that is not what happened.

### What the tables say

Every row in both event tables is `CONFIRMED`, and `error_message` is NULL in all of them:

```
gateway_clmm_events   25 rows   25 CONFIRMED   0 with error_message
gateway_amm_events     3 rows    3 CONFIRMED   0 with error_message
```

GW-25 documents two close failures against position `2PWGc9j7…` on 2026-08-20. Neither
appears. The second is the one that matters: it was not rejected at simulation, it **landed
on-chain at slot 440494812 and reverted**, paying 0.000011772 SOL for the privilege. It has
a signature. It cost money. There is no row for it.

### Why the FAILED branch does not catch it

The recording code is present and looks complete. `routers/gateway_clmm.py` writes the
status it was handed rather than assuming success, and a comment there explains the care
taken over exactly this case:

```python
"status": tx_status
...
# Position bookkeeping happens exactly once, when the tx is known
# good: CONFIRMED here (the event is created CONFIRMED, so the
# poller never touches it), or in the poller's confirm path for
# SUBMITTED events. A FAILED tx mutates nothing — the old
# unconditional booking permanently inflated *_fee_collected on
# failed closes.
```

That branch is only reachable when Gateway **returns** — when there is a response object
with a `tx_status` in it. A landed-and-reverted transaction does not return. Gateway's
`throwIfLandedWithError` raises, the client turns it into a `GatewayError`, and control
skips the whole recording block to land here:

```python
except GatewayError as e:
    raise HTTPException(status_code=e.status, detail=f"Gateway error closing CLMM position: {e}")
```

which persists nothing. So the FAILED path covers the case where Gateway reports a failure,
and misses the case where Gateway raises one. The signature is in the error string, on its
way to a log line and nowhere else.

The same shape is in the AMM router and in the other CLMM routes: every one of them ends in
the same four handlers, and none writes a row before re-raising.

### Why it matters more than one missing row

The cost is real and it compounds. `lp_executor` retries a failed close up to
`max_retries=10`, and GW-25 recommends widening slippage across those attempts — which
raises the share of attempts that reach the chain before failing. Ten paid reverts is a
plausible outcome of one stuck position, and the database would show an unchanged open
position and no explanation. Anything reading these tables for PnL understates gas by
whatever the failures cost, and the position history cannot answer "why is this still
open?" — the evidence is only in the logs, correlated by hand.

It also removes the data GW-25's fourth recommendation needs. Counting simulation failures
separately from paid reverts requires both to be recorded; right now neither is.

### The fix

Write the event before re-raising, in the `except GatewayError` handler, with the signature
parsed out of the error and `status="FAILED"`. The columns already exist and
`error_message` is already in `event_to_dict`. The distinction worth preserving is the one
GW-25 draws: a simulation failure never got a signature and cost nothing, a landed revert
has both, and a row that records the signature and the fee is what tells them apart later.

---

## GW-27 — `/trading/router/execute-quote` is reachable from nothing
**Status: open.** Gateway route, no caller. Found 2026-08-20 while mapping which routes the
test scripts reach.

Gateway registers three router routes (`trading.routes.ts:37-39`):

```
quoteSwapRoute        /trading/router/quote-swap
executeQuoteRoute     /trading/router/execute-quote
executeSwapRoute      /trading/router/execute-swap
```

The middle one takes a `quoteId` from the first and is deliberately router-only — its own
comment explains that a quote id refers to cached route calldata, which pool-scoped amm and
clmm swaps have no equivalent of because they price against a pool at execution time.

Nothing downstream exposes it:

| layer | what exists |
|---|---|
| hummingbot-api | `POST /swap/quote`, `POST /swap/execute` — no execute-quote |
| hummingbot-api-client | no method |
| condor `manage_gateway_swaps` | `quote`, `execute`, `get_status`, `search` |

So the two-step flow — get a firm quote, decide, then execute *that* quote — cannot be run
end to end, and cannot be tested. Every swap on record went through the one-step
`execute-swap`, which re-prices at execution and discards the quote the caller saw.

This matters most for the connectors the route exists for. dflow, titan and 0x return
signed or firm quotes whose value is that the price is held; routing them through
`execute-swap` throws that away and quotes again. It also matters for any strategy that
wants to quote several venues and commit to one, which is the case the route was built for.

Whether to fix it is a decision, not a defect: either add the pass-through and gain a
testable route, or delete the route and stop implying a flow the stack does not have.

---

## GW-18 — pancakeswap-sol's close reports fees and rent as a hardcoded 0
**Status: open.** One connector, one route. Found 2026-08-19 while confirming that a
`collect_fees` result of 0/0 was a real zero rather than a missing field.

`pancakeswap-sol/clmm-routes/closePosition.ts:127`:

```ts
baseTokenAmountRemoved: Math.abs(baseTokenChange),   // the whole wallet delta
quoteTokenAmountRemoved: Math.abs(quoteTokenChange),
baseFeeAmountCollected: 0,   // Included in balance changes
quoteFeeAmountCollected: 0,  // Included in balance changes
positionRentRefunded: 0,     // Position rent refund (simplified)
```

The comment is accurate about the mechanism and that is the problem: the fees really are
inside the balance change, so closing a position with pending fees reports fees of 0 and a
principal inflated by exactly those fees, plus rent of 0. Every other connector separates
them:

| Connector | fees on close | rent on close |
|---|---|---|
| meteora, orca, raydium | real amounts | real amount |
| uniswap, pancakeswap | real amounts | n/a (no rent on EVM) |
| **pancakeswap-sol** | **always 0** | **always 0** |

hummingbot-api stores the two fields separately in its CLOSE event and position
accounting, so on this connector fee income is recorded as zero forever and principal
returned reads high — with no error anywhere.

**A fix was attempted and abandoned deliberately.** Copying `orca/closePosition.ts` does
not work here. `extractInnerTransferAmounts` groups transfers by *top-level instruction*,
which separates Orca's `collectFees` from its `decreaseLiquidity` because they are two
instructions. Pancakeswap-sol builds raw instructions itself, and Raydium-style
`decrease_liquidity_v2` moves principal and fees inside one instruction, so the grouping
returns them already summed. Nor does the position account help: `parsePositionData` reads
only `liquidity`, and `token_fees_owed_*` alone understates the total — the accrued-since-
checkpoint part needs fee-growth math against the pool and the tick range.

So this needs a live pancakeswap-sol close to fix correctly, and the test wallet holds no
position on that connector. Writing the arithmetic blind would produce a plausible non-zero,
which is harder to notice as wrong than the visible zero that is there now.

**Same connector, same shape:** `pancakeswap-sol/clmm-routes/openPosition.ts` reports
`positionRent: 0` beside `Math.abs(change)`, so its rent is counted as deposited liquidity
*and* reported as zero — GW-20's defect, uncorrected here.

**Not yet observed live** — read from the source rather than from a response. The related
`collect_fees` route on the same connector does report real amounts; only `closePosition`
flattens them.

---

## GW-23 — money is typed as JSON `number`, so exact decimals do not survive the wire
**Status: open.** Seen repeatedly across 2026-08-19/20 mainnet runs. Cosmetic at the
amounts tested; the reason to fix it is that the failure grows with the number, and one
instance of it was a real bug already.

Every monetary field is `Type.Number({ format: 'decimal' })` — **181 of them across
`src/`** — which the spec emits as `"type": "number"`. That is an IEEE 754 double, so a
value that is an exact decimal on-chain (an atomic integer over `10^decimals`) arrives as
the nearest representable double instead:

```
collect fees   base: 4.2900000000000047E-7      (4.29e-7)
               quote: 0.00003700000000250725    (0.000037)
swap record    input_amount: 0.010000000000000002   (0.01)
```

The `format: 'decimal'` annotation is a hint no JSON parser acts on — it does not change
what is on the wire.

**It defeats the consumer's Decimal.** hummingbot-api models these as `Decimal`, which
would be exact, but the value has already lost precision before pydantic sees it:

```python
Decimal(str(0.00003700000000250725))  # 0.00003700000000250725  — noise preserved
Decimal('0.000037')                   # 0.000037                — if it were a string
```

**One instance of this was not cosmetic.** `page=2` came back from a numeric field as the
float `2.0` and went out on a query string as `"2.0"`, which is a different request from
`"2"`. Fixed in the clients with `_wire_str`, but that is a workaround at the edge — the
values were already floats by then.

**Why it will not stay cosmetic.** Error is relative, so it scales with magnitude. At
0.01 SOL it is 1e-17 and invisible. On a token with 18 decimals, or a position worth
millions of base units, doubles run out of integer precision at 2^53 — a wei-denominated
amount above ~9e15 cannot round-trip at all. Fee and rent figures also get summed across
events, and the noise accumulates in whichever direction the rounding fell.

**Fix:** carry money as strings — `Type.String({ format: 'decimal' })` — so the exact
decimal survives and every consumer's Decimal is exact. That is a breaking spec change
across 181 fields, so it wants doing in one pass with the generated models regenerated on
both sides; hummingbot-api already parses these into `Decimal`, which would then be
faithful rather than approximate. Keeping `number` and rounding at the edges is the
cheaper option, but it puts the correctness in every consumer instead of in the wire.

---

## GW-12 — three ways a caller's intent is dropped without an error
**Status: open.** None of these returns a wrong answer; each accepts a request it could
have rejected, and acts on something other than what was asked.

- **No component sets `additionalProperties: false`** — 0 of 80. `slippagePc: 5` on an
  execute-swap is dropped and the trade goes out at the connector's configured slippage.
  This is the exact failure hummingbot-api wrote `test_every_kwarg_is_a_field_of_the_model_it_names`
  to catch on its own side, because pydantic drops unknown keywords the same way.
- **`x-connectors` is documentation, not validation.** `AmmCreatePoolRequest` carries both
  `configAddress` (meteora) and `ammConfigIndex` (raydium); passing the wrong one creates a
  pool with connector defaults rather than erroring. 21 fields across the spec are marked
  this way and none is enforced.
- **Writes default their connector.** `POST /trading/clmm/add` with only `positionAddress`
  gets `meteora`, `solana-mainnet-beta` and the config wallet injected by AJV `useDefaults`
  and runs. The first connector in the registry is a strange venue to pick for a position
  whose venue the caller did not name.

**Fix, if wanted:** `additionalProperties: false` on the trading request schemas is the
one with real blast radius — it turns today's silently-ignored field into a 400, which is
the point, but any caller sending an extra key starts failing. Enforcing `x-connectors` is
contained: one check against the field's own extension in `resolveChainNetwork`'s
neighbourhood. Dropping the `connector` default from the write routes costs the Swagger
prefill, which is what it was for.

---

## GW-25 — a narrow in-range CLMM close fails on slippage, and no retry layer widens it
**Status: open.** Found live 2026-08-20 closing Orca positions. The mechanism is
established; the **cause is not confirmed** — see "What is not proven" below before acting
on it.

### The case

Three Orca SOL-USDC positions, opened minutes apart in the same pool, differing only in
where the range sat relative to spot. Closing them:

| position | range vs spot | in range at close | slippage | result |
|---|---|---|---|---|
| `CzUtGJG8…` | below | no | 1% (default) | closed |
| `sWXmj6vg…` | above | no | 1% (default) | closed |
| `2PWGc9j7…` attempt 1 | across | **yes** | 1% | **failed in simulation** |
| `2PWGc9j7…` attempt 2 | across | **yes** | 1% | **landed on-chain, reverted** |
| `2PWGc9j7…` attempt 3 | across | no (drifted out) | 5% | closed |

The failing position was 1% wide (85.6850–86.5808) and sitting 0.13% below its upper bound
when the closes were attempted. The error both times:

```
custom program error: 0x1782        // 6018 TokenMinSubceeded
Did not meet the minimum token amount for the liquidity withdrawal.
```

Gateway recognises it — `solana-error-parser.ts:97` is where that message comes from.

### Two failure stages, one of which costs money

The two attempts failed at **different points**, which matters more than the failure itself:

```
attempt 1   Transaction simulation failed          — never submitted, no gas
attempt 2   landed on-chain but failed             — slot 440494812, fee 0.000011772 SOL
            InstructionError [1, {Custom: 6018}]
```

A close that clears simulation can still revert by the time it lands. So **retrying is not
free**: every attempt that gets past simulation before the price moves again pays a
transaction fee for a reverted transaction.

### Why an in-range narrow position is the sensitive case

A CLMM position's composition is fixed while it is out of range — it holds one token and
price movement cannot change the amount. The withdrawal minimums are then exact and cannot
be subceeded, which is why both one-sided closes above succeeded first time at the default
tolerance.

In range, composition varies continuously with price, and the closer spot is to a bound the
faster it varies. This position went from `0.009533 SOL / 0.903469 USDC` at open to
`0.002486 / 1.511704` in nine minutes — the pool selling its base as spot rose through a
range only 1% wide. Between computing the withdrawal minimums and the transaction landing,
the amounts had moved past a 1% tolerance.

### What is not proven

**The tolerance hypothesis is untested.** Attempt 3 used 5% *and* the position had drifted
out of range by then, so its success has two candidate explanations and the experiment
cannot separate them. Every close that has ever succeeded here was out of range; both
failures were in range. Tolerance was never isolated.

The discriminating experiment is a **narrow, in-range** position closed at a high
tolerance, which needs a fresh position and has to run before the position drifts out:

```
test_orca_clmm.py open-across          # 1% wide, straddling spot
# then immediately, with slippage_pct=5, while `info` still reports [in range]
```

If that closes, tolerance is the cause and the recommendation below applies. If it fails
at 5% too, the cause is elsewhere and the retry logic should not be changed on this
evidence.

### The retry layers, and what they do about it

§3.1 of `docs/retry-architecture.md` specifies retry in two places. One exists:

- **Connector fast path — not implemented.** `closePosition.ts` has no re-quote-and-resubmit;
  its only retry is the confirmation re-fetch at line 54. The route returns a 400. Retry
  logic exists in the router connectors (jupiter, okx, dflow) but not on the LP close path.
  For a caller that is not an executor — `manage_clmm`, the orphan-recovery path — this
  means the caller retries by hand. A convenience gap rather than a dead end: calling again
  is what a human does, and it is what produced attempts 2 and 3 above.

- **Executor paced re-entry — implemented, and correct for the failure mode it was built
  for.** `lp_executor.py` counts attempts, arms an exponential backoff capped at 30s,
  stays in `CLOSING`, and rebuilds with fresh state each time (`:194`, `:804`, `:808`).
  `max_retries` defaults to 10, after which `_max_retries_reached` requires intervention.

**The gap is that neither layer widens the tolerance.** `lp_executor.py:938` is explicit:

```python
# No slippage_pct: omitted, the connector-configured slippagePct applies
```

Every one of the ten attempts re-quotes at the same tolerance. That is the right design for
a *stale quote* — get a fresh one and the problem is gone. It does nothing for a tolerance
that is too tight for the position's sensitivity: the fresh quote is just as tight as the
last one. If the hypothesis above holds, an executor-managed position in this state burns
its whole retry budget failing identically, pays a fee on each attempt that clears
simulation, and lands in "requires intervention" for a position one wider close would have
shut.

### Recommendation for lp_executor

Widen progressively across the existing retry budget rather than repeating the same
request. The backoff loop is already the right place; only the request changes.

- **Pass `slippage_pct` explicitly on close, escalating with the attempt count** — the
  connector-configured value on attempt 0, then a bounded ramp (e.g. ×1.5 per attempt,
  capped at something the operator sets). The parameter already exists on the request and
  is currently omitted, so this is a value to supply rather than a mechanism to build.
- **Cap it, and make the cap configuration rather than a constant.** Tolerance on a close
  is not the same risk as on a swap: the intent is "remove whatever is in this position",
  and the position is being exited regardless, so the exposure is to the withdrawal split
  rather than to a price. It still bounds how much the closer will accept losing, so it
  belongs in the executor config beside `max_retries`.
- **Distinguish the two failure stages in the retry accounting.** A simulation failure
  costs nothing and can be retried freely; a landed-and-reverted one costs a fee. Counting
  them the same way either spends the budget too fast on free failures or pays too many
  fees on expensive ones. `throwIfLandedWithError` already separates them at the connector.
- **Consider out-of-range as a terminal-ish state for close purposes.** A position that has
  drifted out of range will close at any tolerance, because its composition is frozen. If
  the retry loop knows the position is out of range it can stop widening — and conversely,
  a position still in range after several failures is the case that needs the widest
  attempt.

Whether the connector fast path is also worth adding is a separate question. It would save
the executor a tick, but the executor loop already covers the managed path and a widening
ramp there addresses the same failure with one implementation instead of two.


---

## GW-3 — OKX router has no API credentials
**Status: needs a decision. The error is now legible; the credentials are not populated.**

`conf/connectors/okx.yml` still has `apiKey`, `secretKey` and `passphrase` empty, so every
quote through OKX fails. It was the one failure in an otherwise clean 36/37 read sweep;
jupiter, dflow and titan all quoted fine.

**Changed:** the guard in `okx.ts:56` threw a plain `Error`, which reached callers as a
**500** — "Gateway broke, try again" for a condition no retry can fix. It now throws
`httpErrors.badRequest` with the same message. OKX is advertised in `/config/connectors`,
so anything enumerating providers hits this.

**Still open, and yours to decide:** populate the three keys, or drop OKX from the
advertised connector list. Leaving it advertised-but-unconfigured is the one state that
misleads a caller enumerating providers.

### What creating a key involves (researched 2026-08-20)

Self-serve and free. Create an OKX account, verify email and phone, create a project (max 3
per account), then create an API key inside it (max 3 per project) choosing a passphrase.
The secret key is shown only at creation and the passphrase is unrecoverable. Full steps
are in `src/connectors/okx/README.md`.

Three things make it less attractive than "free and self-serve" suggests:

- **The trial ceiling is 1 request/second**, raisable to 5 on review, for 60 days. That is
  the binding constraint, not the expiry — a bot sweeping quotes across connectors exceeds
  1 RPS by itself.
- **Continuing past 60 days requires KYC** in the developer portal, which upgrades the
  account to the Start-up tier. No per-call charge there, but a partner taking a fee on
  swaps enters a revenue share where OKX keeps 20% of it.
- **A fifth header may be missing.** OKX's own client library sends `OK-ACCESS-PROJECT`
  with the project ID; `okx.ts:107` sends only the four signed headers and `okx.config.ts`
  has no field for a project ID. That library targets v5 while this connector calls v6,
  whose quote reference lists only four — so this may be fine, but it cannot be tested
  without credentials. If signed requests 401 once keys are populated, add the project ID
  before investigating anything else.

**Recommendation:** drop OKX from the advertised list unless its routing is specifically
wanted. Jupiter, dflow and titan all quoted cleanly in the read sweep, and a connector
capped at 1 RPS for 60 days and gated on KYC after that is not something to build on.
Creating a key to try it costs little; depending on it does.

---

## Fixed — the record

One line each. The full write-ups are in git history; what is kept here is what the issue
was, what closed it, and where.

- **GW-0 — the `poolAddress` pin survived the refactor.** No action. It briefly looked lost
  when `src/trading/swap/{quote,execute}.ts` were deleted; the rejection had moved to
  `src/trading/common.ts` and the test to `test/trading/pool-swap/pool-address-pin.test.ts`,
  extended to cover the router surface having no `poolAddress` parameter at all.
- **GW-1 — Meteora `quote-liquidity` priced the range midpoint.** The paired amount came
  from the arithmetic mean of the requested range with no reference to the pool's active
  bin. Re-implemented against the active bin after the first fix was lost in the refactor.
- **GW-2 — Raydium's AMM reported `feePct` as a fraction.** `0.0025` for a pool charging
  0.25%, where every other connector reports a percent. Scaled at `raydium.ts:326`.
- **GW-4 — native-SOL amounts were inflated by the transaction fee.** The wallet delta on a
  native-SOL trade is the amount *plus* the fee, and `amountIn` reported the whole thing, so
  the recorded price was wrong. `extractBalanceChangesAndFee` now nets the fee out of the
  native side and reports it separately.
- **GW-5 — the execute response carried no `poolAddress`.** A settled fill could not be
  attributed to a venue without refetching the transaction. The pool-scoped routes stamp it
  on the confirmed `data`; the router leaves it unset, having no single pool.
- **GW-6 — an AMM add never returned the position it created.** Gateway generated the
  position NFT, logged the address, and discarded it, so the caller who had just paid to
  open a position could only recover it by re-listing and diffing. Now returned.
- **GW-7 — three sites double-counted the fee once GW-4 landed.** GW-4 changed the contract
  of `extractBalanceChangesAndFee`; three callers kept applying their own correction. All
  three dropped it.
- **GW-8 — nested `data` schemas were named but never registered.** The 16 confirmed-
  transaction `data` objects were rewritten to `$ref`s pointing at components nobody
  defined, so the spec resolved nowhere for exactly the fields that describe a settled trade.
  `$id` collection now recurses through an already-collected schema.
- **GW-9 — request bodies were declared inline, so they could not be generated.** 24 of 28
  were anonymous objects in the route file, and the `$id`'d shapes in `src/schemas` were
  pre-refactor bases carrying `network` and neither `connector` nor `chainNetwork` — wrong
  the same way for every route. Each route's request const now carries the `$id`.
- **GW-10 — the reads published the wrong shape under the right name.** The GETs had no
  component of their own, so names a client reaches for were held by shapes no route serves.
  The `$id`s moved off the stale bases onto each route's querystring; all 12 trading GETs
  publish a component matching their query exactly.
- **GW-11 — the chain half of `chainNetwork` was decorative on the liquidity routes.**
  Fifteen routes read only the network half and dispatched on connector alone, so
  `ethereum-mainnet` ran a Solana connector and a chain that exists nowhere ran anyway — on
  a write, submitting a transaction. `chainNetworkField` gained an enum of the configured
  chain-networks, and `resolveChainNetwork` checks the connector against the chain.
  gateway `3d9e7d8e9`
- **GW-13 — the spec guard passed on regressions it claimed to catch.** Its shape matching
  could not tell twins apart, and reading the committed spec never caught drift despite
  saying so. CI regenerates and diffs; each `/trading` GET is pinned by component name and
  then by shape. gateway `db6da4d75`
- **GW-14 — the spec named its types but not its operations or its errors.** No operation
  carried an `operationId`, so every generator invented one from the path and a caller's
  method was renamed by any path change; and three of 56 declared any non-2xx response, so
  a client had no error model while `code` is the field it should branch on. Names are now
  chosen in `operation-ids.ts` rather than derived, and one published `ErrorResponse` is
  attached to every operation. Three pool routes had hand-written `{message}` error shapes,
  which is not what Gateway sends. gateway `a40e654e2`
- **GW-15 — response component names were never unified.** Requests were prefixed and
  responses were not, so the unprefixed name was the CLMM one: `PoolInfo` against
  `AmmPoolInfo`. `QuotePositionResponse` was doubly stale — its route had been renamed to
  quote-liquidity. Only the `$id`s moved; the component namespace is global and the
  TypeScript one is not. gateway `a40e654e2`
- **GW-16 — leftovers the unification did not sweep**, in its concrete half. 36 dead schema
  exports (~1300 lines, three files exporting nothing at all) and the 22 tests that asserted
  they were supersets of a base — a contract test for an API that no longer exists. The
  three disagreeing `parseChainNetwork` implementations became one, with trading adding
  only the 400. **Not done, and deliberately:** the three addressing conventions
  (`/pools/` takes chain+network, `/pools/find` takes chainNetwork) and the 20 route files
  that still carry their own `switch (connector)` for every liquidity operation. Both are
  refactors of live trading paths rather than cleanups, and want their own change.
  gateway `a40e654e2`
- **GW-17 — `baseTokenAmountAdded` was a signed wallet delta on three Raydium sites.** A
  deposit was recorded negative, so summing the event table netted a round trip on one
  connector and double-counted it on every other. The CLMM open also counted position rent
  as liquidity — GW-20 on a second connector. gateway `d6e444f74`
- **GW-19 — a hyphen in a token symbol made its pair unquotable.** `split("-")` assumed no
  symbol contains one, which chain-learned symbols do (`DOGE-1-SOL`). One helper splits from
  the right and names the pair it rejects; eight call sites. hummingbot-api `979ff40`
- **GW-20 — a DAMM v2 open recorded the position rent as deposited liquidity.** The stored
  position read 2.86× its real size and fabricated a ~186% loss on a round trip.
  gateway `e3eb7b14f`, hummingbot-api `ad7c516`. **That second commit was itself wrong**: it
  added the two rent columns to `GatewayCLMMPosition` while using them from both
  repositories, which took `POST /gateway/amm/positions/search` down with a 500 and silently
  dropped every AMM position booking. Fixed in hummingbot-api `55cf09e`, with the columns
  added to the AMM model, migrations for both tables, and a structural test that every
  attribute those methods touch is a real column.
- **GW-21 — nothing downstream could close an AMM position, so the rent was stranded.**
  Fixed by collapsing the routes rather than wiring a second one. gateway `74a1ee512`
- **GW-22 — `position_info` answered about every position but the one it was given.**
  `position_address` was declared, accepted by pydantic, and dropped by the handler.
  condor `51300d7`
- **GW-24 — the committed spec carried a real wallet address and a local port.** The
  `walletAddress` default came from the generating machine's config, 21 times over, and was
  vendored into hummingbot-api and its generated models; `servers[0].url` carried a local
  port. The generator substitutes the template placeholders, which is also what made GW-13's
  drift check possible. gateway `db6da4d75`

---

## What is not done

- **GW-3's credentials** — the only item needing a decision.
- **Mainnet coverage as of 2026-08-20.** Run and confirmed: all three swap types
  (router/clmm/amm), the full CLMM lifecycle on Meteora (open → add → remove → close), the
  Raydium AMM round trip (add → remove) which surfaced GW-17, a Meteora DAMM v2 open which
  surfaced GW-20, and the Orca CLMM lifecycle across all three range shapes — above spot,
  below spot and straddling — which surfaced GW-25. Fee collection against a position that
  had fees is also covered: event `42VzMyKR…` collected 0.000195752 base and 0.016661
  quote on Orca. Still unrun: the rest of the DAMM v2 lifecycle
  (`condor/scripts_lp_test/test_meteora_amm.py` steps `close`, `add-more`, `swap-out`),
  blocked on the container above; pool creation on either type, including
  `manage_amm(create_pool)`, which no test step exercises at all; and every connector
  outside meteora/orca/raydium/jupiter — 0x, dflow, okx, titan, uniswap, pancakeswap and
  pancakeswap-sol have never been called. The "verify" notes on the fixed issues above
  remain outstanding except where a section says otherwise.
- **Only one side of the book has been swapped.** Nine of the ten recorded swaps are SELL;
  the single BUY is the DOGE-1 purchase that funded the GW-20 position. BUY inverts which
  side of the pair the amount refers to, which is where a GW-4-style amount confusion would
  hide, and no BUY has been run against SOL-USDC on any connector.
- **No failure path has ever been exercised deliberately**, and per GW-26 the two that did
  occur were not recorded.
- **The seven open issues.** Six of them — GW-12, GW-14, GW-15, GW-16, GW-23 and GW-3 —
  are not wrong answers: they are ways a wrong request is accepted quietly, or a right one
  is described badly, or a decision nobody has taken. **GW-18 is the exception** and the one
  to take first: it is a stored number that is wrong, in the same category as GW-17 and
  GW-20. It is blocked on having a pancakeswap-sol position to close, and as of 2026-08-20
  that is blocked in turn by GW-28, which makes opening one a coin flip.
- **Connector-specific response fields are gone from HTTP** — orca's `sqrtPrice`/`tvlUsdc`,
  0x's `gasEstimate`, jupiter's `quoteResponse`. Deleting `/connectors/*` removed the only
  surface that exposed them. If any are wanted back, the fix is extending the unified
  response schemas, not restoring the routes.

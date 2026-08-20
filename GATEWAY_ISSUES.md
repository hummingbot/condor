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
| GW-14 | No `operationId` and no error responses in the spec | **open** |
| GW-15 | Response component names were never unified | **open** |
| GW-16 | Leftovers the unification did not sweep | **open** |
| GW-17 | `baseTokenAmountAdded` is signed on some connectors, a magnitude on others | fixed |
| GW-18 | pancakeswap-sol's close reports fees and rent as a hardcoded 0 | **open** |
| GW-19 | A hyphen in a token symbol makes its pair unquotable (hummingbot-api) | fixed |
| GW-20 | A DAMM v2 open records the position rent as deposited liquidity | fixed |
| GW-21 | Nothing downstream can close an AMM position, so rent is stranded | fixed (routes collapsed) |
| GW-22 | `position_info` ignored the position it was given (condor) | fixed |
| GW-23 | Money is typed as JSON `number`, so exact decimals do not survive | **open** |
| GW-24 | The committed spec carried a real wallet address and a local port | fixed |

Seven are open and are written out in full below; the eighteen that are fixed are
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

---

## Still open

In full, worst first. Only GW-18 is a wrong number; the rest are ways a wrong request is
accepted quietly or a right one is described badly, and GW-3 is a decision rather than a
fix. GW-12 is being fixed in a parallel session.

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

## GW-14 — the spec names its types but not its operations or its errors
**Status: open.** The half of the generated-client contract GW-8/9/10 did not reach.

**No operation has an `operationId`** — 0 of 58. That is the method name in a generated
client, so every generator synthesizes one from path and method, and every path change
renames the method. It is the same instability GW-9 fixed for models, still live for calls.

**Three of 58 operations declare any non-2xx response** (`POST /pools/`,
`DELETE /pools/{address}`, `GET /pools/{tradingPair}`). A generated client therefore has no
error model at all, though `{statusCode, error, message, code?}` is the envelope every
route actually returns and the `code` is what callers branch on.

Also undocumented: `GET /` and `POST /restart` are registered routes that `hideUntagged`
keeps out of the spec, and the ethereum-only routes are tagged `/chain/ethereum` where
everything else is `/chains`, which puts them in a separate class in a generated client.

---

## GW-15 — response component names were never unified
**Status: open.**

The request side got `Amm`/`Clmm` prefixes uniformly. The response side did not, so the
unprefixed name is the CLMM one and a reader has to know that:

| CLMM route answers with | its AMM twin answers with |
|---|---|
| `PoolInfo` | `AmmPoolInfo` |
| `PositionInfo` | `AmmPositionInfo` |
| `AddLiquidityResponse` | `AmmAddLiquidityResponse` |
| `OpenPositionResponse` | `AmmOpenPositionResponse` |
| `QuotePositionResponse` | `QuoteLiquidityResponse` |

The last row is also a stale name: the route was renamed `quote-position` → `quote-liquidity`
in the refactor and its response component kept the old word. `/chains/ethereum/allowances`
and `/approve` answer with inline objects where every other chain route has a component.

---

## GW-16 — leftovers the unification did not sweep
**Status: open.** Cosmetic individually; together they are the difference between a
unified API and one that mostly looks unified.

- **36 dead schema exports** survive the deleted per-connector routes —
  `MeteoraClmmQuoteSwapRequest`, `PancakeswapSolClmm*` (11 of them), the jupiter/okx/dflow/
  titan request trio each. None is referenced by any route. `24795ffbb` swept some of these;
  these are what it missed. **30 tests across four files assert their shape**, which is a
  contract test for an API that no longer exists.
- **Three addressing conventions**, two of them inside one router: `/pools/` takes
  `chain`+`network`, `/pools/find` takes `chainNetwork`, `/chains/{chain}/*` takes a path
  `chain` plus a query `network`. Trading is uniformly `chainNetwork`.
- **Three `parseChainNetwork` implementations** with three validation levels: the one in
  `src/trading/common.ts` rejects a value with no hyphen, `ConfigManagerV2`'s accepts
  anything, and `src/pools/routes/findPools.ts:110` hand-rolls the split inline.
- **The registry covers swaps only.** Its header says a connector is one entry and the
  routes are pure dispatch; that holds for `quoteSwap`/`executeSwap`/`fetchPools`, while 20
  route files still carry their own `switch (connector)` for every liquidity operation.
  Adding a connector means editing all of them.
- **`/trading/router/execute-quote` has no caller.** `RouterExecuteQuoteRequest` is the one
  generated request model hummingbot-api never constructs, so the quote-then-execute flow
  that `quoteId` exists for is unused and every execute re-quotes.

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
- **Mainnet coverage as of 2026-08-19.** Run and confirmed: all three swap types
  (router/clmm/amm), the full CLMM lifecycle on Meteora (open → add → remove → close), the
  Raydium AMM round trip (add → remove) which surfaced GW-17, and a Meteora DAMM v2 open
  which surfaced GW-20. Orca CLMM and the rest of the DAMM v2 lifecycle have scripts
  (`condor/scripts_lp_test/test_orca_clmm.py`, `test_meteora_amm.py`) but are unrun. Also
  unrun: fee collection against a position that has any, pool creation, and
  `manage_amm(create_pool)`, which no test step exercises at all. The "verify" notes on the fixed issues above remain outstanding except
  where a section says otherwise.
- **The seven open issues.** Six of them — GW-12, GW-14, GW-15, GW-16, GW-23 and GW-3 —
  are not wrong answers: they are ways a wrong request is accepted quietly, or a right one
  is described badly, or a decision nobody has taken. **GW-18 is the exception** and the one
  to take first: it is a stored number that is wrong, in the same category as GW-17 and
  GW-20, and it is blocked only on having a pancakeswap-sol position to close.
- **Connector-specific response fields are gone from HTTP** — orca's `sqrtPrice`/`tvlUsdc`,
  0x's `gasEstimate`, jupiter's `quoteResponse`. Deleting `/connectors/*` removed the only
  surface that exposed them. If any are wanted back, the fix is extending the unified
  response schemas, not restoring the routes.

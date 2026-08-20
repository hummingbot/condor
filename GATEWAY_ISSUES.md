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
| GW-13 | The spec guard passes on regressions it claims to catch | **open** |
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

GW-1 through GW-11, GW-17 and GW-19 are code-complete and green: 1153 tests, 143 suites,
clean typecheck,
lint 0 errors, and `openapi.json` regenerates identical to the committed copy.

Mainnet verification began 2026-08-19 against a container built from `c0bb10253`: the
three swap types and the full Meteora CLMM lifecycle all behave, and the Raydium AMM add
surfaced GW-17. See "What is not done" for what remains unrun.

GW-12 through GW-16 came out of an adversarial audit of the unification and of GW-8/9/10
themselves, run on 2026-08-19. Each is reproduced below rather than asserted; none is
fixed. GW-13 matters most for the others, because it is the reason a regression in any of
them can land green.

---

## Route changes that affect every issue below

The trading type is now a path segment and the connector a parameter. The spec went
from 182 paths to 56, and `openapi.json` is generated from the route table by
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

## GW-1 — Meteora `quote-liquidity` priced the range midpoint, not the active bin
**Status: fixed. Re-implemented after the first fix was lost.**

The paired amount came from the arithmetic midpoint of the requested range, with no
reference to where the pool's active bin sits:

```ts
const avgPrice = (lowerPrice + upperPrice) / 2;
quoteAmount = baseAmount * avgPrice;
```

For a range of [150, 250] with spot at 82.29 — entirely above the current price, so the
position is 100% base — Meteora quoted **2 USDC**. Orca returned 0 on the identical
request. Meteora was the only connector doing this.

Worse than a bad estimate: `openPosition.ts` *rejects* a nonzero quote amount when spot
sits below the range, so following the quote produced a 400 from the open it fed.

**Note on history:** this was fixed once in an uncommitted working tree, and that fix was
destroyed by a `git checkout -- src/connectors` during the route refactor's recovery from
a bad codemod. It was not recoverable from stashes or dangling blobs. What follows is a
re-implementation, so review it as new code rather than as a re-application.

**Fix** in `src/connectors/meteora/clmm-routes/quotePosition.ts`:

- Uses the DLMM SDK's `autoFillYByStrategy` / `autoFillXByStrategy`, which take the active
  bin, bin step and bin range and return what the strategy actually requires on the other
  side (0 for the unused side on a one-sided range).
- Bin IDs now derive exactly as `openPosition` does — `toPricePerLamport` first, and the
  rounding flags that way round. The old code passed raw prices with the flags swapped, so
  the quote described a different bin range than the open would occupy.
- The both-amounts branch asks the strategy what the offered base needs on the quote side
  and picks the binding side from that, instead of comparing a ratio to spot.
- Dropped the `liquidity` field — a geometric mean that collapses to 0 for any one-sided
  position. It is optional in the schema and Orca already omits it.

**Verify after the next build:** quote a range that does *not* straddle spot, confirm the
unused side is 0, then confirm that same request opens without a 400.

---

## GW-2 — Raydium's AMM reported `feePct` as a fraction; every other connector reports a percent
**Status: fixed.**

`raydium.ts:326` divided and stopped: 25/10000 = `0.0025` — a fraction — for a pool
charging 0.25%.

| Connector | Pool | feePct returned | Actual fee |
|---|---|---|---|
| **raydium/amm** | `58oQ…LYQo2` | **0.0025** | **0.25%** |
| meteora/amm | `Bv65…tunM` | 0.25 | 0.25% |
| raydium/clmm | `3ucN…sUxv` | 0.04 | 0.04% |
| orca/clmm | `Czfq…44zE` | 0.04 | 0.04% |
| meteora/clmm | `2sf5…nQT3` | 0.04 | 0.04% |
| pancakeswap-sol/clmm | `4QU2…D9qN` | 0.03 | 0.03% |

One outlier out of six, off by 100×, rendered literally downstream — hummingbot-api showed
`Fee: 0.0025%` for a pool charging 0.25%.

**Fix:** ×100 on the v4 branch. The CPMM branch was a *third* unit in the same file —
`configInfo.tradeFeeRate` in millionths, which would have emitted `2500` for the same
0.25% — now /10000. Two test expectations that asserted the old values were corrected.

**Verify:** read `pool-info` for a Raydium v4 pool and a CPMM pool; both should report
percent, matching the table above. The CPMM branch was never confirmed live — no CPMM pool
was configured.

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

## GW-4 — native-SOL amounts inflated by the transaction fee
**Status: fixed, plus three follow-on sites it exposed (see GW-7).**

When the traded token was native SOL, the wallet's balance delta was the swap amount
**plus** the transaction fee, and `amountIn` reported the whole thing. The comment said so
outright, and `extractBalanceChangesAndFee` computed that exact fee ~25 lines earlier.

Measured, three swaps of 0.01 SOL each:

| Connector | Recorded `input_amount` | `gas_fee` | True input | Recorded price | True price | Error |
|---|---|---|---|---|---|---|
| jupiter | 0.010027173 | 0.000027173 | 0.01 | 82.1780 | 82.4013 | −0.27% |
| meteora/clmm | 0.010010560 | 0.000010560 | 0.01 | 82.1680 | 82.2548 | −0.11% |
| raydium/amm | 0.010009974 | 0.000009974 | 0.01 | 82.0883 | 82.1702 | −0.10% |

**Fix:** `extractBalanceChangesAndFee` nets the fee out of the native-SOL change for the
fee payer (account 0), so `amountIn`/`amountOut` describe the trade and `fee` alone
describes the cost. `extractClmmBalanceChanges` no longer subtracts `txFee` from rent — it
would have understated rent by exactly the fee — and its now-unused `txFee` parameter is
gone, along with its four call sites.

**Verify:** repeat the three swaps; `input_amount` should equal 0.01 exactly, with the fee
appearing only in `gas_fee`.

---

## GW-5 — the execute response carried no `poolAddress`
**Status: fixed.**

A CLMM or AMM fill could not be reconciled to a venue without re-fetching the transaction,
even though Gateway had resolved a specific pool to execute against. hummingbot-api stored
`pool_address: null` for every swap — correctly.

**Fix:** optional `poolAddress` on the confirmed `data` block of the execute response, set
by the pool-scoped routes, which resolve exactly one pool. The router leaves it unset,
which is right: a router has no single pool. hummingbot-api's column is waiting.

---

## GW-6 — AMM add never returned the DAMM v2 position it just created
**Status: fixed.**

Gateway generated the position NFT keypair, logged the address, and discarded it. The
caller who had just paid to open a position was never told which one, and could only
recover the address by re-listing `positions-owned` and diffing — which races any
concurrent write and cannot attribute an address to a transaction.

**Fix, in three parts:**

1. Opening moved into its own `openPosition` function, since it is its own on-chain call
   (`createPositionAndAddLiquidity` — it mints the NFT and locks rent), and is reachable
   directly as `POST /trading/amm/open`.
2. `AddLiquidityResponse.data` gained optional `positionAddress` and `positionRent`.
   `positionAddress` is **always** the position the write touched: the one just opened when
   no address was given, or the one named. `positionRent` appears only when the call
   actually opened a position.
3. Rent is read from the landed transaction — the position account's post-balance at open,
   its pre-balance at close — through a shared `accountLamports` helper that resolves the
   index against the transaction's *full* account list including lookup-table addresses.
   Indexing into the static keys alone reads a different account's lamports on a versioned
   transaction, since `preBalances`/`postBalances` span the combined list. The helper
   returns `null` when the account took no part, so "no rent moved" stays distinguishable
   from a real zero.

`/trading/amm/close` was added alongside, and is genuinely distinct from remove-at-100%:
withdrawing everything leaves the position NFT holding its rent, so close uses the SDK's
`removeAllLiquidityAndClosePosition` and reports `positionRentRefunded`.

Both open and close work on **every** AMM. The fungible-LP AMMs (raydium, uniswap,
pancakeswap) route through their existing add and remove; they have no position account, so
`positionAddress` is absent and the rent figures are 0 — a fact about the AMM, not a
placeholder for something unread.

**hummingbot-api can now delete its `positions-owned` reconciliation workaround.**

**Verify:** open a DAMM v2 position via `/trading/amm/add` with no `positionAddress`;
the response should name the position and its rent. Close it; the rent should come back.

---

## GW-7 — three sites double-counted the fee once GW-4 landed
**Status: fixed. Found while integrating GW-4.**

GW-4 changed the contract of `extractBalanceChangesAndFee`: the native-SOL change is now
already net of the transaction fee. Three call sites still applied their own correction:

| Site | Was | Effect |
|---|---|---|
| `meteora/clmm-routes/closePosition.ts:120,124` | `− rent + totalFee` | overstated SOL-side liquidity removed by one fee |
| `meteora/clmm-routes/openPosition.ts:208,213` | `− rent − txFee` | understated SOL-side liquidity added by one fee |
| `meteora/amm-routes/closePosition.ts` (new) | `− rent + fee` | same, caught before it shipped |

All three now subtract rent only. The AMM close test encodes the contract in its fixture,
so a future change to the fee convention fails loudly rather than drifting amounts.

This is the class of bug worth watching for: any site combining a balance change with a fee
is now coupled to that one function's convention.

---

## GW-0 — the `poolAddress` pin survived the route refactor (no action)

Recorded because it briefly looked lost: `src/trading/swap/quote.ts` and `execute.ts` were
deleted wholesale along with the pin test. Both landed on their feet — the rejection is now
in `src/trading/common.ts`, and the test moved to
`test/trading/pool-swap/pool-address-pin.test.ts`, extended to cover the router surface
having no `poolAddress` parameter at all.

---

## GW-8 — nested `data` schemas were named but never registered
**Status: fixed (uncommitted in `~/gateway`).**

Giving the shared schemas `$id`s put them in `components.schemas`, which is what lets both
Python clients generate their models instead of transcribing them. The collector only
walked module-level exports, though, so the 16 confirmed-transaction `data` objects — which
exist only nested inside their parent response — were rewritten to `$ref`s pointing at
components nobody defined. The spec resolved nowhere for exactly the fields that describe a
settled trade: `baseTokenBalanceChange`, `fee`, `positionRent`.

**Fix:** collect `$id`s recursively, continuing *through* an already-collected schema.
84 components, no dangling refs; the generated models go from 128 classes with 26 numbered
collisions to 86 with none.

---

## GW-9 — request bodies are declared inline, so they cannot be generated
**Status: fixed.**

24 of Gateway's 28 request bodies are anonymous objects declared in the route file
(`const UnifiedAmmAddLiquidityRequest = Type.Object({...})` in
`src/trading/trading-amm-routes/add.ts`, and its 21 siblings). The `$id`'d schemas in
`src/schemas/` are *base* types the routes extend — `AmmAddLiquidityRequest` has no
`connector` or `chainNetwork`, the two fields every call sends — so a client generated from
them would be wrong in the same way for every route.

**Fix:** each route's request const carries the `$id`, the route barrels re-export them,
and `identifiedSchemas()` in `src/app.ts` collects those barrels alongside the schema
modules. Three stale bases (`AmmExecuteSwapRequest`, `ClmmExecuteSwapRequest`,
`ClmmCreatePoolRequest`) were squatting the names the real wire bodies want while being
referenced by nothing; they keep their shape as composition sources but lose their `$id`.

23 of the 28 request bodies are components now, up from 4 — all 15 trading POSTs, the four
parameterized chain POSTs, ethereum's allowances/approve, and wallet remove/add-hardware.
The two pool-swap bodies come from a factory, so they are built once per pool type and
shared with the route, since Fastify rejects a duplicate `$id`.

This reached the POSTs only. Five POST bodies are also still inline — `/config/update`,
`/pools/`, `/tokens/`, `/wallet/add`, `/wallet/setDefault` — because they are raw object
literals in the route rather than named consts; extracting them is a separate change.

**Superseded:** this section originally recorded that the GETs could never be components,
their fields reaching the spec only as `parameters`. That turned out to be wrong, and
GW-10 below covers them.

`test/spec/request-components.test.ts` pins it: every `/trading` request body resolves to a
component, no `$ref` dangles, and each published request carries `connector` and
`chainNetwork` and not `network` — the last being what fails if a base type is ever
published in a route body's place.

---

## GW-10 — the reads published the wrong shape under the right name
**Status: fixed.**

GW-9 left 32 of 100 components referenced by nothing, read at the time as dead classes a
generator would emit and nobody would use. They were worse than dead. Every one was a
pre-refactor base, and the GET routes had no component of their own, so the obvious names
were held by shapes no route serves:

| Route | Its query | The component holding the name |
|---|---|---|
| `GET /trading/clmm/quote-swap` | `chainNetwork`, `connector`, … | `ClmmQuoteSwapRequest`: `network`, no `connector` |
| `GET /trading/clmm/fetch-pools` | 9 fields incl. `connector` | `FetchPoolsRequest`: 4, incl. `network` |
| `GET /trading/clmm/position-info` | `chainNetwork`, `connector`, `positionAddress` | `GetPositionInfoRequest`: `network`, `positionAddress`, `walletAddress` |

`walletAddress` there is not a parameter of that route at all. The same held on the
response side: `ClmmQuoteSwapResponse` and `AmmQuoteSwapResponse` were published while both
routes answer with `ChainQuoteSwapResponse`. This is exactly the trap GW-9 fixed for the
POSTs, still live for the reads — and the reads are most of this API.

**The premise that blocked it was false.** Both GW-9 and the earlier analysis here assumed
a GET's fields could not become a component because @fastify/swagger expands a querystring
into `parameters`. Registering a schema and referencing it are independent: `addSchema`
puts it in `components.schemas`, and the operation still expands its parameters normally.
Verified by adding one `$id` and regenerating — the component appeared with all 9 fields
and the route's 9 parameters were unchanged.

**Fix:** the `$id` moved off each stale base and onto the route's own querystring, the same
move GW-9 made for the bodies. All 12 trading GETs now publish a component matching their
query exactly; 28 stale bases and 3 orphaned `data` shapes lost their `$id`s, keeping their
shape as composition sources. `src/trading/clmm` gained a barrel — its four read routes
live outside `trading-clmm-routes`, so nothing was collecting them. `TokenSchema` is now
published as `Token`, the name the stale `TokensResponse` was holding.

100 components down to 80. The 14 still unreferenced are all GET request models, which
cannot be referenced by construction. Both clients regenerated; hapi 20 checks and
hummingbot 115 pass, gateway 1118 tests across 137 suites.

`test/spec/request-components.test.ts` grew two assertions: every `/trading` GET publishes
a component matching its query (`connector` and `chainNetwork`, never `network`), and no
component is both unreferenced and not some GET's query shape — which is what fails if a
stale base is ever republished. Mutation-checked both ways: removing one GET's `$id` fails
the first, and adding a stale `$id` fails the second.

---

## GW-11 — the chain half of `chainNetwork` was decorative on the liquidity routes
**Status: fixed.**

Fifteen routes — every `/trading/amm/*` and every `/trading/clmm/*` write plus the AMM
reads — did `const { network } = parseChainNetwork(chainNetwork)` and never looked at
`chain`. They then dispatched on `connector` alone, so the chain the caller named had no
effect on anything:

| sent | connector called with |
|---|---|
| `chainNetwork: "ethereum-mainnet"`, `connector: "meteora"` | meteora ← `"mainnet"` |
| `chainNetwork: "banana-mainnet-beta"`, `connector: "meteora"` | meteora ← `"mainnet-beta"`, **succeeded** |
| `chainNetwork: "solana-"` | meteora ← `""` |

On `/add` or `/open` that submits a transaction for a request Gateway could have proved
wrong before signing. The pool-scoped swap routes never had this — they fetch their ops
through the registry's `lookup`, which compares connector and chain — and the
`src/trading/clmm/*` reads switch on chain and reject an unknown one. Only the liquidity
routes, which call their connector module directly and so have no ops to fetch, were
unguarded. Three dispatch idioms, one of them with no check in it.

**Fix, in two parts, because neither implies the other.** `chainNetworkField()` now
carries an `enum` read from `ConfigManagerV2.getSupportedChainNetworks()` — the 14
configured `chain-network` strings, read at import rather than listed — which rejects rows
2 and 3 at the schema. Row 1 is a *valid* chain-network with the wrong connector, so the
enum cannot see it; `resolveChainNetwork(chainNetwork, connector, type)` replaces the bare
parse and calls the registry's `assertConnectorOnChain`, giving the same rejection the
swap routes already gave: `Connector 'meteora' runs on solana, not ethereum. Use a
ethereum clmm connector: uniswap, pancakeswap`. All three rows now 400 with the connector
never invoked.

The registry's `CLMM_SWAP_CONNECTORS`/`AMM_SWAP_CONNECTORS` became `CLMM_CONNECTORS`/
`AMM_CONNECTORS` and `common.ts` re-exports them instead of listing the same names again —
the hardcoded copies were a second roster that could drift from the table that dispatches.

`test/trading/chain-network-guard.test.ts` pins it: the three malformed selectors, the
mismatched pair on both surfaces, the matching pair still dispatching, and a structural
case asserting no route under `trading-amm-routes/` or `trading-clmm-routes/` parses its
own `chainNetwork` — that last one is what stops a sixteenth route reopening the hole.
Mutation-checked separately in both directions: removing the enum fails exactly the three
schema cases, removing the pair check fails exactly the two pairing cases.

Two test suites had been passing incoherent pairs and relying on a downstream error —
`positions-owned` asked for uniswap on `solana-mainnet-beta` — and now name each connector
with the chain it runs on.

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

## GW-13 — the spec guard passes on regressions it claims to catch
**Status: open.** Both of these were demonstrated by mutation, not inferred.

`test/spec/request-components.test.ts` identifies a GET's component by *shape* — the set of
its property names — because a GET has no `$ref` to follow. Three pairs share a shape
exactly: `Amm`/`ClmmQuoteSwapRequest`, `Amm`/`ClmmPositionsOwnedRequest`, and
`EstimateGasRequest`/`StatusRequest`. **Removing `AmmQuoteSwapRequest` from the spec
entirely leaves all 30 tests passing**, because its twin still matches the AMM route's
shape and the orphan check excuses it for the same reason. (hummingbot-api catches this
one, since `assert model is not None` fires when the class disappears — by accident, not
by design.)

Second, the file's own comment says reading the committed spec means these "also catch a
spec that was not regenerated after a route changed." **They do not.** Adding a field to
`ClmmAddRequest` without running `pnpm generate:openapi` leaves all 30 passing. Reading the
committed spec catches nothing about drift; only regenerating and comparing does, and no
CI job does that — `.github/workflows/` never runs the generator.

**Fix:** a CI step that regenerates and fails on a diff is the whole of the second half,
and it subsumes the first — a twin that lost its `$id` is a diff. Without it every other
open item here can regress green.

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

## GW-17 — `baseTokenAmountAdded` is a signed wallet delta on some connectors, a magnitude on others
**Status: fixed** in gateway `<pending>`. Found live 2026-08-19, the first time anything
wrote to the AMM event table.

`amm-add` on Raydium's CPMM deposited 0.01 SOL and 0.849 USDC. hummingbot-api recorded it as:

```json
"event_type": "ADD_LIQUIDITY",
"base_token_amount":  -0.01,
"quote_token_amount": -0.848971
```

Negative amounts on an add. The sign is Gateway's — hummingbot-api stores
`data.baseTokenAmountAdded` verbatim (`routers/gateway_amm.py:223`).

Within one connector and one route family, the two directions disagree:

```ts
// raydium/amm-routes/addLiquidity.ts:205
baseTokenAmountAdded: baseTokenBalanceChange,              // balanceChanges[0], raw → negative
// raydium/amm-routes/removeLiquidity.ts:215
baseTokenAmountRemoved: Math.abs(baseTokenBalanceChange),  // magnitude → positive
```

Across all 20 add sites the convention is split. Three pass a raw signed balance change:

| Site | Value | Sign on a deposit |
|---|---|---|
| **raydium/amm/addLiquidity.ts:205** | `baseTokenBalanceChange` | **negative** (confirmed live) |
| **raydium/clmm/addLiquidity.ts:126** | `baseTokenBalanceChange` | **negative** |
| **raydium/clmm/openPosition.ts:137** | `baseTokenChange` | **negative** |
| meteora/amm/{createPool,openPosition} | `Math.abs(balanceChanges[0])` | positive |
| orca, pancakeswap-sol (all) | `Math.abs(...)` | positive |
| uniswap, pancakeswap (all) | `quote.baseTokenAmount`, `actualBaseAmount` | positive |

The removed side has no such split: all nine sites normalize, either through `Math.abs`
or — in `meteora/amm/closePosition.ts:116` — an `adjust()` that takes the absolute value
and then nets off the position rent refund.

**The round trip, run live.** `amm-add` then `amm-remove` on the same Raydium pool:

```
event_type                  base            quote     status
REMOVE_LIQUIDITY     +0.009985521   +0.850208000   CONFIRMED
ADD_LIQUIDITY        -0.010000000   -0.848971000   CONFIRMED
```

**Why it matters.** Raydium's `Math.abs` on the remove is ambiguous — for an inflow the
magnitude and the signed delta are the same number — so Raydium ends up coherent end to
end under one convention (**signed wallet deltas**, net = `add + remove`) while every
other connector is coherent under the other (**magnitudes**, net = `remove - add`). Both
are defensible alone. What is broken is that no single arithmetic is right for both, and
nothing in a stored row says which convention produced it.

`/gateway/amm/events/search` is the AMM history, so summing `base_token_amount` over it is
the obvious way to derive a net position. On the rows above that sum is meaningful —
`-0.0000145` SOL and `+0.001237` USDC, the real result of four minutes in the pool. Run
the identical arithmetic over Meteora rows and it adds two positives, double-counting the
round trip instead of netting it. Nothing raises either way.

**Fixed** on all three raydium sites, matching what every other connector and the entire
removed side already do. A field named `…Added` reporting a negative value is wrong at the
source, so this belongs in Gateway rather than in a consumer-side sign flip.

The two adds take `Math.abs()`. The third, `clmm/openPosition.ts`, turned out to carry
**two** defects rather than one: opening a position locks rent in the position account,
and when a side is SOL that outflow sits inside the same balance change — so the raw
delta was both negative and larger than the deposit. That is GW-20 on a second connector,
unfixed there because `e3eb7b14f` reached only the DAMM v2 open. It now uses the same
`liquidityWithoutRent` helper, which takes the magnitude and backs the rent off the native
side only.

Two tests asserted `toHaveProperty('baseTokenAmountAdded')` — the key, not the value — so
they passed on every negative that reached the event table. Both now assert amounts, and
`clmm/addLiquidity.ts` gained the first test it has ever had. Mutation-checked: reverting
the sign fails them, and on the open, reverting the sign and reverting the rent each fail
independently.

**Still open in the same family:** `pancakeswap-sol/clmm-routes/openPosition.ts` reports
`positionRent: 0` beside `Math.abs(change)`, so its rent is counted as deposited liquidity
and reported as zero — the same shape as GW-18 below, on the same connector.

**Verify:** repeat the round trip above and read `POST /gateway/amm/events/search`. After
the fix both rows carry positive amounts and the net is `remove - add`. The two rows
already in the table were written under the old behavior and keep their signs, so a
migration — or a documented cutover timestamp — is part of the fix, not separate from it.

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

**Fix:** extract the fee transfers from the balance changes the way `orca/closePosition.ts`
does (it groups the transfers and takes principal from the first group, fees from the
second), and report the real rent instead of 0.

**Not yet observed live** — the test wallet holds no pancakeswap-sol positions, so this is
read from the source rather than from a response. The related `collect_fees` route on the
same connector does report real amounts; it is only `closePosition` that flattens them.

---

## GW-19 — a token whose symbol contains a hyphen makes its pair unquotable
**Status: fixed** in hummingbot-api `<pending>`. **Lives in hummingbot-api, not Gateway** — but Gateway's chain-learning
feature is what makes it reachable, so it belongs with that work. Found live 2026-08-19,
minutes after that feature first recorded a token.

`routers/gateway_swap.py:62` and `:148`:

```python
base, quote = request.trading_pair.split("-")
```

No `maxsplit`, no guard. Any symbol containing a hyphen raises `ValueError`, which escapes
as the HTTP message:

```
400  'too many values to unpack (expected 2)'
```

Two things are wrong there. The pair is unquotable at all, and the caller is handed a
Python internal instead of an explanation — there is no way to read that message and learn
that the symbol is the problem.

**Why it is newly reachable.** Gateway now reads a token's name, symbol and decimals off
the chain the first time it is used. Symbols are then whatever the mint says, not what a
curated list allows. The first pool this was exercised against recorded its base token as
`DOGE-1`, so Gateway filed the pool under `trading_pair: "DOGE-1-SOL"` — a string
hummingbot-api cannot parse back into two tokens. Gateway is right; the pair *is*
`DOGE-1` over `SOL`. The consumer's split is what assumes a symbol has no hyphen.

```
"DOGE-1-SOL"                                     → 400, cannot quote
"DpBzjtgG…vfhm-SOL"  (the mint, spelled out)     → quotes fine
```

**Fixed** with one helper rather than a `rsplit` at each site: `utils/trading_pair.py`
splits from the right and raises `InvalidTradingPair` — a `ValueError`, which the routers
already map to a 400 — with a message naming the pair. Splitting from the right is correct
rather than merely forgiving: the quote asset is the last segment, so `DOGE-1-SOL` reads
as `DOGE-1` over `SOL`, which is what it means.

Wired into all eight sites: `gateway_swap.py` ×2 (the ones that returned the 400),
`market_data.py` ×2, `orders_recorder.py` ×2 — where a broad `except` had been turning
this into a silently missing fee rather than an error — and the `len(parts) == 2` guards
in `executors.py` ×2, `executor_ws_manager.py` and `executor_service.py`, which had been
skipping unrealized PnL without saying so. Those four keep their tolerance, since one
unreadable pair should not fail a whole listing; it now applies only to pairs that really
are unreadable.

A structural test asserts no bare `split("-")` on a trading pair survives under `routers/`,
`services/`, `utils/` or `models/`. It found `executor_service.py:1072`, which none of the
reading above had listed. `bots/controllers/` is deliberately out of scope — those are
strategy templates, edited and shipped separately.

**Verified by test, not re-observed live.** The hyphenated token is no longer in the local
token list, and none of the 42 highest-TVL mints across Orca and Meteora has a hyphen in
its symbol, so the original 400 could not be reproduced; the tests assert against the exact
recorded string instead.

**Related, smaller:** `GET /gateway/networks/{id}/tokens?search=` filters on symbol and
name only, so searching a freshly-learned token by its address returns `{"tokens":[]}`
while the token is in the list. The pool search on the same router *does* match an address,
which is what makes it a trap rather than merely a limitation.

---

## GW-20 — a DAMM v2 open records the position rent as deposited liquidity
**Status: fixed** in gateway `e3eb7b14f` and hummingbot-api `ad7c516`. Found live
2026-08-19 on the first Meteora AMM add. Worse than GW-17: that is a sign, this is a
magnitude, and here it was wrong by 2.86×.

Opened a DAMM v2 position with 3000 base and the quote side the pool asked for. What the
chain did, what the position holds, and what was recorded:

```
wallet SOL delta                 -0.015241081
  less gas                       -0.015220830   <- recorded as quoteTokenAmountAdded
liquidity actually in the pool     0.005323709   <- what positions_owned reports
unaccounted                        0.009897121   <- rent for the position NFT accounts
```

Rent is **locked, not spent** — the chain returns it when the position account is closed.
Recording it as deposited liquidity overstates the position by the rent, and since the
rent here is nearly twice the liquidity, the stored position is 2.86× its real size.

The same connector already gets this right on the way out.
`meteora/amm-routes/closePosition.ts:116`:

```ts
const adjust = (change, mint) =>
  mint.toBase58() === nativeMint ? Math.max(0, Math.abs(change) - positionRentRefunded)
                                 : Math.abs(change);
quoteTokenAmountRemoved: adjust(balanceChanges[1], poolState.tokenBMint),
```

`openPosition.ts:86` computes the same quantity, returns it, and does not apply it:

```ts
positionRent,                                       // computed at :73, returned, unused
quoteTokenAmountAdded: Math.abs(balanceChanges[1]), // still carries the rent
```

**Why it compounds.** hummingbot-api stores `positionRent` on the position row and
populates it for CLMM (`routers/gateway_clmm.py:455,539`) — but the AMM path never reads
it. `grep position_rent routers/gateway_amm.py` returns nothing. So the inflated figure is
booked with nothing recorded alongside that would let a reader back it out:

```json
"initial_quote_token_amount": 0.015220830000000001,
"quote_token_amount":         0.015220830000000001,
"lp_token_amount":            null
```

against a position `positions_owned` reports as holding `0.005323709`.

**The P&L this produces.** The close nets the rent refund out, so it will record roughly
the 0.0053 that was really in the pool. Against an open of 0.0152, that is a fabricated
loss of ~0.0099 SOL on a position that never held more than 0.0053 — a ~186% loss on a
round trip whose real cost is the 4% pool fee.

**Fixed, in both halves.** `openPosition` now backs the rent out of the native side, and
both directions share one helper — `liquidityWithoutRent` in `chains/solana/solana.utils.ts`
— rather than holding a copy each of the same subtraction. Two supporting renames, both
because the units are what made this silent: `accountLamports` returns SOL (it divides the
raw balances), so it is now `accountBalanceSol` and says so in its docblock; the magic
native-mint string is now `NATIVE_MINT` from `@solana/spl-token`.

hummingbot-api records `positionRent` on the open, exposes it and the refund through
`position_to_dict` — which returned neither — and reads the position address out of the
response instead of the GW-6 diff, now deleted.

Mutation-checked: dropping the subtraction, dropping the clamp, inverting which side it
applies to, and returning lamports instead of SOL each fail the tests.

**Rows already written keep the inflated figures.** `F1YcTMd6…` is booked at
`0.015220830` against `0.005323709` held. Nothing migrates them, so a reader spanning the
fix needs `created_at` to tell which convention a row follows.

**GW-4 is related but not the same.** That one was gas inflating native amounts, and it is
fixed — gas is correctly excluded here, which is why the recorded figure matches the
delta-minus-fee exactly. Rent was never part of it.

---

## GW-21 — nothing downstream can close an AMM position, so the rent is stranded
**Status: fixed, by removing the choice rather than wiring the second route.** Found
2026-08-19 while deciding whether `/trading/amm/{open,close}` duplicate
`/trading/amm/{add,remove}`.

They are not symmetric, and only half the question has an easy answer.

**`open` is duplicative.** `add` declares `positionAddress` as "Omit to open a new
position", and Meteora's `addLiquidity` delegates to `openPosition` when it is absent.
`AmmOpenPositionResponseData` and `AmmAddLiquidityResponseData` have identical property
sets. `open.ts` only re-frames the fungible-LP case so it answers `positionRent: 0` with
no address, for a caller that does not want to know which kind of AMM it is on. Removing
it costs that normalization and nothing else.

**`close` is not.** From `meteora/amm-routes/closePosition.ts:17`:

> This is why close is not the same call as remove at 100%: removing all the liquidity
> leaves an empty position NFT behind, still holding its rent. The SDK's
> `removeAllLiquidityAndClosePosition` does both in one transaction.

The schemas carry the same distinction: `AmmClosePositionResponseData` has
`positionRentRefunded`; `AmmRemoveLiquidityResponseData` has no rent field at all.

**The gap.** `services/gateway_client.py` implements `amm_add_liquidity` and
`amm_remove_liquidity` and neither of the other two. So hummingbot-api opens through
`add` — which does return `positionRent`, and which it discards (GW-20) — and has no way
to close at all. condor's `manage_amm` inherits the gap: its actions are `add_liquidity`
and `remove_liquidity`, so the closest thing to a close is remove at 100%, which strands
the rent. On the position opened for GW-20 that is 0.0099 SOL, against 0.0053 of
liquidity.

Nothing errors. The position drains to zero, reads as empty, and quietly keeps the rent.

**Fixed** in gateway `74a1ee512` and hummingbot-api `6529688`, by collapsing the four
routes to two rather than teaching two more callers about the other pair:

- `meteora/amm-routes/removeLiquidity.ts` delegates to `closePosition` at 100%, so a full
  removal closes the account and the rent comes back without the caller knowing to ask.
- `/trading/amm/open` and `/trading/amm/close` are gone. `app.integration.test.ts` asserts
  they are *not* registered, so re-adding one is a decision rather than a drift back.
- `positionRentRefunded` joins the remove response as optional — present when the removal
  closed the account, absent on a partial removal and on fungible-LP AMMs, rather than a 0
  that would read as "closed, refunded nothing".
- hummingbot-api's `close_position` records it, as the CLMM path already did.

condor needs no change: `manage_amm(remove_liquidity, percentage_to_remove=100)` is now
the close, which is what it was already calling.

**Verify:** the position opened for GW-20, `F1YcTMd6…`, is still open and still holds its
rent. Removing 100% of it against a container built from `74a1ee512` should refund
~0.0099 SOL and record it — a check GW-20's own fix does not cover, since that one is
about the open side.


---

## GW-22 — `position_info` answered about every position but the one it was given
**Status: fixed** in condor `51300d7`. **Lives in condor, not Gateway or hummingbot-api** —
nothing to do on either of those, noted here only because it is the same failure mode as
GW-12 and turned up during the same mainnet run.

`manage_clmm(action="position_info")` always called `get_positions_owned`, which answers
with every CLMM position the wallet holds on the connector. `position_address` is declared
on `CLMMRequest`, so pydantic accepted it and the handler dropped it — no error, just a
different question answered:

```python
if action == "position_info":
    result = await gc.get_positions_owned(          # position_address never read
        connector=connector, network=net,
        wallet_address=request.wallet_address,
    )
```

It read as correct for the whole life of the tool because the test wallet held exactly one
orca position, so "that position" and "every position" were the same list. Opening a second
made it visible: asking about the new one returned both.

Nothing was missing underneath — `hummingbot_api_client.gateway_clmm.get_position_info`
existed, and hummingbot-api already served `GET /gateway/clmm/position-info` behind it.
Only the branch that chooses between them was absent.

**Also added:** `in_range` on each rendered row. It is the reason a position earns or does
not, and it explains the fee column beside it — during this run the in-range position
accrued from `1.3E-8` to `1.93E-7` base while the out-of-range one stayed at exactly zero,
which without the flag reads as two positions behaving inconsistently.

Mutation-checked: reverting to `positions_owned`, and returning the single position
unwrapped, each fail the three new tests in `tests/test_manage_clmm.py`.


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
- **GW-12 through GW-16 and GW-18**, none of which is a wrong answer — they are ways a
  wrong request is accepted quietly, or a right one is described badly. GW-17 *was* in a
  different category — a stored number with the wrong sign — and is now fixed, along with
  GW-19.
- **Connector-specific response fields are gone from HTTP** — orca's `sqrtPrice`/`tvlUsdc`,
  0x's `gasEstimate`, jupiter's `quoteResponse`. Deleting `/connectors/*` removed the only
  surface that exposed them. If any are wanted back, the fix is extending the unified
  response schemas, not restoring the routes.

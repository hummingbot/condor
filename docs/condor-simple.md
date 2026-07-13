# condor-simple — validating a Condor without hummingbot-api

Design spike: can Condor run trading agents against **Gateway alone** —
no hummingbot-api, no CEX connectors — with executors owned by Condor
itself? Scope is deliberately cut to DEX spot + LP; CEX-style venues
(including hyperliquid perps) are excluded and re-enter later via a
venue adapter, not by reopening this architecture.

Written 2026-07-13, grounded in the coupling census of this branch
(`spike/simpler-agent-framework`), `~/hummingbot` (LPExecutor),
`~/hummingbot-api` (executor orchestration), and `~/gateway` on branch
**`feat/robinhood-chain`** — the spike pins that branch for its wallet
redesign (see §2).

## 1. Thesis

The agent framework (four primitives, policies, journal, skills,
sessions) is already venue-agnostic. Everything hummingbot-specific
sits in two replaceable layers: the **execution backend** (hummingbot-api
runs the executors) and the **read surface** (`condor/fetchers/` +
`mcp-hummingbot` wrap its client). For gateway-reachable venues, both
layers collapse into Condor calling Gateway's REST API directly.

Target stack — two processes instead of three-plus:

```
LLM brain (consult / delegate / experiment / session ticks)
   │  mcp__condor__* (framework verbs) + optional read-only venue MCPs
   ▼
manage_executors ──► Condor executor runtime ──► Gateway HTTP ──► chain
                          │
                    condor.db (executor state, reconciliation)
                          │
                    journal / risk / track record  (native types,
                                                    no translation layer)
```

## 2. Why the pieces already fit

- **Framework layer: zero changes.** `run_agent` + policies
  (`condor/agents/policies.py`), the journal tracker duck-type
  (`risk.py:82-84` — no client reference), and the four primitives are
  untouched. The LLM's write tool keeps its name (`manage_executors`);
  only its backend changes.
- **LPExecutor is a port, not a rebuild.**
  `hummingbot/strategy_v2/executors/lp_executor/lp_executor.py` (1,109
  LOC) is already clock-free — its own comments note it "directly
  awaits gateway operations... works without the Clock/tick mechanism".
  Its connector calls map 1:1 to Gateway REST:
  `_clmm_add_liquidity` → `/connectors/{dex}/clmm/open-position`,
  `_clmm_close_position` → `close-position`, `get_position_info` →
  `position-info`, plus `collect-fees`, `quote-position`,
  `execute-swap`. ~40-50% is portable business logic (state machine,
  IN/OUT_OF_RANGE + limit-close decisions, close-out swap math, and the
  range-selection logic in `lp_rebalancer.py`); the rest is framework
  plumbing (ExecutorBase scaffolding, events, `TrackedOrder`,
  connector wrapper) that drops.
- **Gateway is a clean standalone service.** Fastify + Swagger
  (`~/gateway/src/app.ts:155-330`); per-connector CLMM routes, generic
  `/trading/clmm/*`, `/pools`, `/wallet`, `/tokens`, `/chains/*`.
  Nothing hummingbot-specific — any process can call it.
- **The `feat/robinhood-chain` branch hardens exactly the invariant
  this design leans on.** Its wallet redesign deletes the
  `showPrivateKey` and raw `sendTransaction` routes outright, moves
  keys to a hardened keystore (`src/services/secure-keystore.ts`,
  scrypt + AES-256-GCM at rest), adds hardware-wallet support and a
  default-wallet concept (`src/wallet/routes/`), and gates
  `/trading/*` behind API auth with loopback-by-default binding. "Keys
  never leave Gateway" is enforced by the API surface, not by
  convention — and Condor's gateway client must carry the API token.
- **Market data comes from Gateway itself** (`pool-info`,
  `position-info` both return price) plus GeckoTerminal's public API
  for OHLCV/TA. The one gap is FX conversion (hummingbot's
  `RateOracle` for native→quote) — replace with a gateway quote or a
  price API.
- **Portfolio = Gateway.** Wallet balances + `positions-owned`
  replace the accounts/credentials subsystem and
  `fetchers/portfolio.py` aggregation entirely.
- **Risk gets simpler and stronger.** Executor configs become Condor's
  own types, so `risk_gate` computes max-loss at creation natively.
  The `DANGEROUS_TOOLS` name-allowlist problem (fails open for unknown
  tools, `handlers/agents/_shared.py:207`) disappears: there are no
  foreign write tools. Third-party MCP servers, if plugged, are
  read-only by manifest; execution has exactly one door.

## 3. The executor contract (what Condor defines)

Condor owns the **contract**; runtimes implement it.

- **Config schema** per executor type, declarative intent: pool/pair,
  notional, ranges, stop conditions.
- **Lifecycle**: `pending → active → closing → closed`, persisted to
  `condor.db` on every transition.
- **Risk declaration**: a platform-computable mapping from config →
  `{max_notional, max_loss}`. `risk_gate` approves the whole intent at
  creation; the runtime's gateway handle refuses orders outside the
  declared budget (enforcement at the adapter, not trust in executor
  code).
- **Reporting shape**: fills, fees, PnL — the journal consumes this
  directly; track record = list of closed executors. No
  fetchers-style normalization layer.
- **Watchdog**: missed-heartbeat threshold → flatten via `close_all`;
  on restart, reconcile persisted state against Gateway
  `positions-owned`.

User-defined executor types follow the routines pattern: code + manifest
under a registry, authored only through a builder agent
(`executor_builder`, hard routing rule like `routine_builder`), safety
enforced platform-side (budget at the handle, watchdog) rather than by
code review.

Initial types: `SwapExecutor` (submit → confirm → done; trivial, exists
to prove the path and to give one-shot swaps uniform accounting) and
`LPExecutor` (the port).

## 4. New code to build

1. **Executor runtime** — task lifecycle, `condor.db` persistence,
   restart reconciliation, watchdog. ~1-2k LOC; the part that must be
   boringly reliable.
2. **Gateway client** — typed httpx wrapper over the routes in §2
   (authenticating per the branch's API-auth gate on `/trading/*`),
   ~300 LOC, plus FX conversion.
3. **Two executor types** — ~800 LOC total (SwapExecutor + LPExecutor
   port incl. rebalance logic).
4. **Rewire `manage_executors`** to the native runtime; providers
   (`condor/agents/providers/`) re-point from hummingbot client to
   runtime + gateway.

## 5. Deleted or dormant

`mcp-hummingbot` dependency · `condor/fetchers/` (12 files) ·
`handlers/cex/*` · bots/controllers/backtesting/archived web routes and
dashboard pages · the hummingbot wheel-build workflow · `config.yml`
servers-as-hummingbot-endpoints (becomes a gateway endpoint + venue
registry). Net system LOC goes down.

## 6. Honest losses (until "later on")

- **Hyperliquid perps** — `mm_expert` and `revival_trader` don't run on
  simple-Condor v1. Hyperliquid reaches Condor through hummingbot's
  CLOB connector, not Gateway. Re-entry path: a venue adapter
  conforming to the executor contract (reads possibly seeded from
  hyperliquid-mcp), added deliberately, later.
- **`run_backtest`** — though actual backtests here are already custom
  routines (`spcx_jit_backtest`, `oracle_jit_backtest`, ...), so this
  may cost nothing in practice.
- **Funding-rate feeds** for `funding_rate_watcher`, if sourced from
  hummingbot-api.
- **Uptime obligation moves to Condor.** Today a crashed Condor means
  "no new ticks; bots keep running". With Condor-hosted executors it
  means "open positions unmanaged" — which is why the watchdog and
  restart reconciliation in §3 are not optional extras but the core of
  milestone 0.

## 7. Validation milestones

Same playbook as the MCP harness spike: prove the riskiest seam live,
small. Gateway runs from `~/gateway` on `feat/robinhood-chain`
throughout.

- **M0 — runtime + SwapExecutor + LPExecutor port.** In order:
  1. Runtime skeleton + `SwapExecutor`: one real gateway swap on
     Solana with persistence; then the kill test — stop Condor
     mid-executor, restart, verify reconciliation against
     `positions-owned`. This exercises the gateway path (incl. API
     auth), the persistence model, and the watchdog — everything
     architecturally novel.
  2. LPExecutor migration: port the state machine + rebalance-range
     logic onto the same runtime. Run as an *experiment* first
     (mutations cancelled), then a small live session on one pool with
     an existing agent.
  M0 done = the thesis is validated end-to-end on both executor
  shapes (one-shot and stateful position management).
- **M1 — surface cleanup.** `manage_executors` fully native, providers
  re-pointed, §5 deletions. Dashboard simplification can lag.

### M0 results (2026-07-13) — PASSED

Implementation: `condor/executors/` (contract, sqlite store, gateway
client, SwapExecutor, LpExecutor port, ranges.py, runtime with
watchdog + reconcile, CLI). 19 unit tests; full suite 292 green.

Live, on Solana mainnet via gateway `feat/robinhood-chain`:

- Swap: 0.01 SOL -> 0.748081 USDC confirmed, lifecycle persisted
  (`4fVGk6...JcDK`).
- LP kill test: opened raydium SOL-USDC position `Be6iTb...fotW`
  (~$1, 2% width), `kill -9` mid-monitor, restart reconciled and
  re-adopted the live position from condor.db + chain, SIGTERM left it
  open by design, out-of-process `stop` closed it on-chain
  (`jJkE5c...jdHs`). Position P&L clean (IL ~0 over the drift).

Two connector-semantics findings the live run surfaced (both fixed):

1. **Gateway's open/close `*TokenAmountAdded/Removed` fields are wallet
   balance CHANGES, not position amounts** — negative on open,
   rent-polluted on close. Initial deposit must be frozen from
   position-info after open; close accounting must keep the last
   position-info reading. Raw wallet changes are now recorded
   alongside (`open/close_wallet_*_change`).
2. **Raydium classic CLMM burns ~0.0166 SOL (~$1.2) of non-refundable
   rent per position lifecycle** (NFT metadata 0.01512 + mint 0.00146;
   verified on-chain against both txs). Gateway's `positionRent`
   reports only one of four rent accounts. First-order input to
   rebalance frequency on raydium; check meteora/orca semantics before
   relying on cross-connector cost parity.

### M1 results (2026-07-13) — wired

- **Runtime hosted in the main process** (`condor/executors/service.py`
  singleton, started from the web app lifespan: reconcile + watchdog).
- **REST**: `/api/v1/executors` (list/get/create/stop). Swap creates
  auto-price `notional_quote` from a live gateway quote.
- **MCP**: native `manage_executors` on the condor server
  (create/stop/get/list), calling the REST routes — executors outlive
  the MCP subprocess. Same stripped tool name as the hummingbot one, so
  DANGEROUS_EXECUTOR_ACTIONS, experiment cancellation, and human_gate
  apply unchanged.
- **Risk gate**: native-shape branch in
  `RiskEngine.check_executor_action` — notional computed from the
  executor type's `RiskDeclaration`, fail-closed when it can't be
  (unknown type, swap without declarable notional). The
  hummingbot-shape `controller_id` requirement no longer blocks native
  creates. Known approximation: declarations are in pool-quote units
  vs nominally-USD limits — exact for USD-quoted pools.
- **Provider**: `native_executors` core provider reads the store by
  `agent_id` (new store column, migrated in place) and mirrors the
  hummingbot `executors` provider shape — exposure, open count,
  realized PnL — into tick summaries and the journal.
- 15 integration tests; full suite 307 green.
- **§5 deletions deliberately deferred**: live agents (`mm_expert`,
  `revival_trader`) still run on hummingbot-api/hyperliquid — it stays
  a peer venue runtime until they migrate or retire. Dashboard pages
  for native executors also deferred.
- Executor attribution threads the session id end-to-end (engine →
  `run_agent(agent_id=)` → `--agent-id` → MCP settings → tool default),
  matching how hummingbot executors are keyed (`controller_id` =
  session id). Fallback: slug for delegations, "" for chat.

## M2 (planned) — LP agent + provider PnL

Decision: **no runtime-level rebalancing loop.** Rebalancing is an
agent concern — an LP agent's tick decides close/reopen, mirroring the
`lp_rebalancer` controller's logic but with the brain in the loop
(skip a rebalance in silly volatility, log why, accumulate learnings).
Determinism stays where it belongs: range math in `ranges.py`, position
management in `LpExecutor`, judgment in the tick.

### 1. LP agent (agent-driven rebalancing)

- **Agent**: new trading agent (agent-builder flow), small risk
  baseline in AGENT.md (e.g. `{max_position_size_quote: 50,
  max_open_executors: 2}`).
- **Strategy playbook** `strategies/lp-rebalance.md` —
  `default_config`: connector, pool_address, trading_pair,
  total_amount_quote, position_width_pct, position_offset_pct,
  rebalance_threshold_pct, buy/sell price limits, update cadence.
- **Deterministic planning via an agent-local routine**
  (`plan_lp_position`, authored through routine_builder): wraps
  `condor.executors.ranges.plan_position` + a live pool price and
  returns the exact `manage_executors(create)` config JSON — the model
  never hand-computes bounds; the create args stay explicit and
  auditable through the risk gate.
- **Tick logic** (playbook, mirroring the controller):
  1. Read `native_executors` provider: open executor state.
  2. No open executor → run `plan_lp_position` → single-sided balance
     check (gateway balances; create a `swap` executor first if the
     planned side needs the other token — the controller's autoswap) →
     `manage_executors(create, lp)`.
  3. Open + IN_RANGE → nothing.
  4. Executor CLOSED since last tick (limit price hit) → replan at
     current price and reopen: that IS the rebalance (controller's
     stop-and-recreate, `rebalance_threshold_pct` encoded as the
     executor's limit prices).
  5. Price outside both buy and sell limits → STAND-DOWN, wait.
- **Validation path**: experiment first (creates cancelled by the
  gate; the tick reasons and journals what it would do), then a live
  session at tiny size. Prefer running the meteora/orca rent check
  first — raydium costs ~$1.2 per rebalance cycle (M0 finding), which
  dominates economics at small width.

### 2. Unrealized PnL in the native provider

`NativeExecutorsProvider` reports `pnl: 0` for open executors (no live
price at read time). Fix: one `clmm_pool_info` per unique open
pool_address (via the runtime's gateway client), then per executor
`unrealized = (base·price + quote + base_fee·price + quote_fee) −
(initial_base·add_mid_price + initial_quote)` — same math as
`LpExecutor.net_pnl_quote`. Value `total_exposure` at the live price
too (today it uses add-time price). Fail soft per pool: a price fetch
error reports that executor's pnl as unavailable, never blocks the
tick.

## 8. Relation to the venue-registry design

This document is the degenerate single-venue case of the wider
generalization discussed on this branch: standardize the **write path**
(the executor contract, always Condor's, keys never leave Gateway),
leave the **read path** free (any approved read-only MCP server can be
plugged per the venue manifest). CEX venues, when wanted, are one more
runtime behind the same contract — hummingbot-api demoted from
mandatory substrate to optional venue runtime.

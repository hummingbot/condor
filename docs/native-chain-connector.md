# Native chain connector — deprecating Gateway, Solana first (Jupiter)

Replace the external Hummingbot **Gateway** (TypeScript service) with a **native
in-Condor chain connector** that just **signs / sends / polls** transactions, plus
per-protocol clients that build those transactions. **Solana first** (Jupiter
swap, then lend), **Ethereum later**, **LP and other tx types later**.

## The unifying picture

This is the same move as the Hyperliquid perp executor: Condor holds the key and
executes natively, per venue, feeding one executor/log/barrier stack.

```
Condor daemon  (executors + transaction log + barriers + control socket)
   ├─ Solana   → SolanaConnector (sign/send/poll) + Jupiter{Swap,Lend} clients   [replaces Gateway]
   ├─ Ethereum → EthereumConnector (later) + Uniswap/Aave clients
   └─ Perp     → HyperliquidClient  (sep. spec)
```

Endgame: **Gateway and hummingbot-api both gone**; Condor is a pure-Python
execution layer across chains/venues. The connector is deliberately thin —
chain primitives only — and protocols plug in on top (exactly the split the
"it just needs to sign/send/poll" framing implies).

## Why deprecate Gateway — honestly

**For:**
- One less service and language (no TS/pnpm/certs); pure-Python daemon, simpler ops.
- **Full control over tx landing** — priority fees, compute budget, blockhash
  refresh, retries, Jito. On Solana this is the difference between txs landing and
  silently dropping; Gateway abstracts it away from you.
- Uniform key/execution model with the Hyperliquid native path.
- Gateway crashed twice during this session's validation; removing it removes that
  failure mode and the version-matching dance.

**Against (the honest costs):**
- **You own Solana tx plumbing** (versioned txs, ALTs, priority/compute budget,
  blockhash lifetime, confirmation, ATA creation, wrapped SOL, RPC failover) — but
  for the swap/lend path this is small, because Jupiter builds the tx.
- **Key custody** — the Solana key lives in Condor. At MVP you **import an existing
  keypair** (encrypted at rest) via the dashboard; bounding the risk with a
  Condor-generated, fund-what-you-risk agent wallet is on the roadmap (below).
- You take on maintenance as the Solana tx format / fee market evolves.

**Net:** **no LP** ⇒ Gateway can be **fully** deprecated once native swaps (+ lend)
are proven — no fork, no LP reimplementation. The existing LP executor
(`lp.py`) and Gateway's CLMM code retire along with Gateway.

## Architecture

Split what Gateway did (protocol **and** chain) into two clean layers:

- **`ChainConnector`** — chain primitives only, reusable across every protocol:
  ```python
  class ChainConnector(ABC):
      wallet_address: str
      async def sign_and_send(self, tx_b64: str, *, signers=()) -> str      # -> signature
      async def poll(self, signature: str, *, timeout_s=60) -> TxResult      # confirmed/failed + parsed
      async def simulate(self, tx_b64: str) -> SimResult                      # pre-flight, catch reverts
      async def balance(self, token: str | None = None) -> Decimal
      async def recent_blockhash(self) -> tuple[str, int]                     # (hash, lastValidBlockHeight)
  ```
  `SolanaConnector` now; `EthereumConnector` later (same shape: sign/send/poll a
  receipt). Chain-agnostic executors depend on this, not on Gateway.

- **Protocol clients** — build unsigned transactions, one per integration:
  ```python
  class JupiterSwap:
      async def quote(self, input_mint, output_mint, amount, slippage_bps, ...) -> Quote
      async def build_swap_tx(self, quote, user_pubkey, *, priority=...) -> str  # base64 versioned tx
  class JupiterLend:
      async def build_supply_tx(...) / build_withdraw_tx(...) / build_borrow_tx(...) -> str
  ```

- **Executors** orchestrate: `protocol.build_*_tx()` → `connector.simulate()` (opt)
  → `connector.sign_and_send()` → `connector.poll()` → parse fill. The existing
  `SwapExecutor` / `PositionExecutor` move off `GatewayClient` onto
  `SolanaConnector` + `JupiterSwap`; storage/reconcile/barriers unchanged.

## What the SolanaConnector owns (the finicky part)

The value of going native is doing these *well*:
- **Versioned transactions + ALTs** — Jupiter returns a v0 `VersionedTransaction`;
  deserialize, sign, send as-is.
- **Priority fee + compute budget** — pass Jupiter `prioritizationFeeLamports`
  (auto or a computed value) + `dynamicComputeUnitLimit`; this is the main landing lever.
- **Blockhash lifetime + confirmation** — send, then poll `getSignatureStatuses`
  against `lastValidBlockHeight`; on expiry, rebuild/resend. This is the retry loop
  Gateway hid.
- **RPC** — use the operator's endpoint (e.g. the QuickNode Solana URL) with
  failover/backoff; `skipPreflight` + our own `simulate` for clearer errors.
- **Token accounts / wrapped SOL** — Jupiter's swap tx handles ATA creation and
  wSOL wrap/unwrap; a raw protocol later may need us to.

Stack: `solders` (keypair, tx primitives) + `solana` AsyncClient (or `httpx` to
the RPC) — the same primitives hummingbot/Gateway use under the hood.

## Jupiter swap flow (why swaps are cheap)

1. `JupiterSwap.quote(input_mint, output_mint, amount, slippage)` → Jupiter v6
   `/quote`.
2. `build_swap_tx(quote, wallet, priority)` → Jupiter `/swap` returns a ready
   base64 v0 tx (routing, ALTs, compute budget, fees all built in).
3. `SolanaConnector.sign_and_send(tx)` → signature.
4. `poll(signature)` → parse `getTransaction` for actual in/out amounts + fee.

That's the entire swap path — no AMM math, no pool handling. It replaces
`GatewayClient.execute_swap`/`quote_swap`/`poll_tx` for the memecoin/position
executors with a couple hundred lines.

## Jupiter lend (new executor type)

Same connector, new protocol client + a `LendExecutor` (supply / withdraw, later
borrow / repay). Barriers differ (no TP/SL; instead APY targets, health-factor /
utilization guards for borrow). **Verify Jupiter Lend exposes a public
tx-building API** before committing; otherwise start with a lending protocol that
does (the connector layer is identical regardless of which lender).

## Key custody — import a keypair (MVP), agent wallet later

Native sign/send means Condor must hold a real Solana signing key — unlike
Hyperliquid, where the browser generates a throwaway *agent* key and the main key
never leaves the wallet. Solana has **no native trade-only delegate**, so there's
no equivalent "approve an agent" step: whatever key Condor holds can move that
wallet's funds. Two consequences shape the MVP — the key must be **imported** (a
connected Phantom/Solflare session can't hand Condor a server-signable key), and
you **bound the risk by funding a dedicated wallet lightly** (a fresh wallet used
only for the agent, holding only what you're willing to risk — not your main
holdings).

**MVP — import an existing keypair via the dashboard.** The user adds a Solana
wallet through the existing web dashboard — the same Settings → wallets surface
that already hosts `ConnectHyperliquid` (`frontend/src/components/settings/`).
They paste a base58 secret key or upload a `keypair.json`; it's POSTed to Condor
and stored **encrypted at rest** (`config_manager`, a "solana" venue entry:
`private_key` + derived `public_address` + `network`). Executors sign inside the
daemon via the control socket, so the **LLM subprocess never sees the key**. This
reuses the existing add-credential UX — a new wallet *form*, not a new signing
scheme.

**Why not "connect Phantom" like Hyperliquid connects Rabby/MetaMask?** The
Hyperliquid flow (`frontend/src/lib/wallet/hyperliquid.ts`) works because the
*browser* generates an agent key and the user's real wallet signs an on-chain
`ApproveAgent` — Condor receives a bounded, **trade-only** key while the
withdrawal key never leaves the wallet. Solana has no such approve-agent
primitive, so connecting Phantom would only yield an *address*, not a key Condor
can sign with server-side. An approve-and-delegate-style connect flow is therefore
a **roadmap** item, not the MVP.

### Roadmap — Condor-generated agent wallet (with Ethereum + more perp DEXs)

Later — alongside the Ethereum connector and additional perp DEXs — add a
Condor-generated agent wallet as the bounded-risk default: `manage_wallet(create)`
mints a fresh encrypted keypair and returns its public address, the user funds a
capped amount (SOL + quote token), executors trade from it, and
`manage_wallet(withdraw)` sweeps back to the main wallet (the only way funds
leave). It's the Solana analog of Hyperliquid's agent wallet, achieved with a
*separate funded wallet* since Solana has no trade-only delegate — one
`manage_wallet` surface across venues (Solana here, Hyperliquid in the perp spec:
"a dedicated trading identity with bounded risk, created / funded / withdrawn the
same way"). Deferred because it adds key generation, an encrypted-backup / export
policy (a lost Condor install strands funds otherwise), a low-gas alert, and a
fund/withdraw/sweep flow — none of which the import-a-keypair MVP needs to prove
the connector out. A **minimal signer service** (a ~1-file process holding only
the key) remains an orthogonal isolation option under either mode.

## Phasing (keep it runnable throughout)

| Phase | Deliverable | Gateway still needed? |
|---|---|---|
| **S1** | `ChainConnector` + `SolanaConnector` (sign/send/poll/simulate/balance) + **import-keypair** wallet entry in the dashboard (encrypted config) + tests against devnet | yes |
| **S2** | `JupiterSwap` + port `SwapExecutor`/`PositionExecutor` onto native; validate tiny mainnet swaps (as we did with Gateway); **remove the LP executor** (no LP) | no |
| **S3** | `JupiterLend` + `LendExecutor` | no |
| **S4** | Remove Gateway + its CLMM code; drop the runtime's Gateway wiring and `hummingbot-api-client` | no |
| **later** | `EthereumConnector` + more perp DEXs + **Condor-generated agent wallet** (`manage_wallet` create/fund/withdraw/export) behind the same interfaces | — |

After S2 nothing calls Gateway (swap/position are native, LP is removed), so S4's
teardown is mechanical. Run native + Gateway side by side only within S2 until the
native swaps are proven, then flip the default.

Run native and Gateway side by side during S2–S3 (executors pick a connector by
type/venue), so a bad native swap never blocks the proven Gateway path until we
flip the default.

## What changes in the existing code

- `runtime.connector_for(config)` resolves the connector per executor: today
  `GatewayClient`; add `SolanaConnector` (for native swap/position) keyed by a
  config flag, then flip the default. Same hook the Hyperliquid spec needs.
- `SwapExecutor` / `PositionExecutor`: replace `self.gateway.execute_swap(...)` etc.
  with `JupiterSwap.build_swap_tx` + `SolanaConnector.sign_and_send/poll`. State
  models keep `open_tx_hash`/`quote_spent`/`base_bought` — now filled from the
  parsed tx result instead of Gateway's response.
- `condor/executors/lp.py` (`LpExecutor`) + Gateway's CLMM code are **removed** (no
  LP), simplifying the runtime's `_EXECUTOR_TYPES` to swap / position / lend / perp.
- `condor/executors/gateway.py` (`GatewayClient`) retires at S4.

## Open decisions

1. **Imported-key entry format**: accept a base58 secret key, a `keypair.json`
   byte array, or both in the dashboard import form. (Agent-wallet export policy is
   deferred with the Condor-generated wallet — see roadmap.)
2. **Lending target**: Jupiter Lend (pending API verification) vs a lender with a
   confirmed tx-building API for the first `LendExecutor`.
3. **RPC**: which endpoint(s) + failover policy (operator-supplied, e.g. QuickNode).
4. **Priority-fee policy**: trust Jupiter's auto fee, or compute our own from recent
   fees for hot momentum entries.

*(Resolved: no LP → Gateway fully deprecated, no fork. Key custody → import an
existing keypair via the dashboard for MVP; Condor-generated agent wallet on the
roadmap with Ethereum + more perp DEXs.)*

## Dependencies

- Add `solders` + `solana` (solana-py). Remove `hummingbot-api-client` and, at S4,
  the Gateway service entirely. Net: fewer moving parts, pure-Python execution.

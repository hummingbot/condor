# Spike: WalletConnect onboarding for Hyperliquid, triggerable from an MCP tool

## Context

Condor already has a browser-only Hyperliquid "connect" flow
(`frontend/src/components/settings/ConnectHyperliquid.tsx` +
`frontend/src/lib/wallet/hyperliquid.ts`): it generates a throwaway agent
("API wallet") keypair, has the user sign `ApproveAgent` and
`ApproveBuilderFee` EIP-712 messages with their own wallet, then saves the
agent key as the `hyperliquid`/`hyperliquid_perpetual` credentials. The
user's master private key never leaves their wallet — the security
motivation behind the original "WalletConnect onboarding" GitHub issue is
already solved for desktop-extension users.

Two real gaps remain, and this spike targets both:

1. **Desktop-extension-only.** Wallet discovery is EIP-6963 (MetaMask/Rabby
   browser extensions). There's no WalletConnect/Reown integration anywhere
   in the repo, so a user with only a mobile wallet can't connect.
2. **Dashboard-only.** The flow is unreachable from chat — no MCP tool or
   skill can trigger it, so it can't be started from Claude Code or from
   Condor's own Telegram bot.

## Goal

Prove the mechanism end-to-end for **one flow only** (Hyperliquid
agent-wallet + builder-fee approval): an MCP tool starts a WalletConnect
session, the user approves two signatures on their phone, and the resulting
agent-wallet credentials land in `master_account` exactly like the existing
browser flow.

Explicitly out of scope for the spike: referral code linking, partial-failure
retry UX, session cleanup/expiry polish, automated tests — those are
refinements once the mechanism is proven.

## Gateway vs. a Condor-owned sidecar

Considered hosting the WalletConnect route in `hummingbot/gateway`
(checked out locally at `~/gateway`, branch `feat/solana-router-connectors`)
instead of a new Condor-owned Node process, since Gateway is already a
Node/TS service Condor manages (start/stop/config via
`mcp_servers/hummingbot_api/tools/gateway.py`, `GatewaySettings.tsx`). Looked
closely and it's a worse fit than it first appears:

- **No CEX precedent.** Gateway's whole taxonomy is `src/chains/` (ethereum,
  solana only) + `src/connectors/` (Uniswap, Jupiter, Orca, etc. — all
  on-chain DEX/AMM routers). Even OKX, which has a real CEX API, is only
  wired for its on-chain router in Gateway. A Hyperliquid agent-wallet
  *authorization* route is a CEX-credential concern, architecturally foreign
  to "middleware for blockchain networks and DEXs" (Gateway's own README
  description). It'd be the first route of its kind.
- **The one thing Gateway is good at here doesn't apply.** Gateway's real
  wallet infrastructure is its encrypted-keystore-at-rest (`ethers` V3
  keystore, `conf/wallets/<chain>/<address>.json`, unlocked by one
  server-wide passphrase set at process start — `src/wallet/utils.ts`,
  `src/chains/ethereum/ethereum.ts`). This flow doesn't want that at all —
  the resulting agent key is meant to flow to Condor/hummingbot-api's own
  credential store, exactly like the existing browser flow, not live in
  Gateway's keystore. So the part of Gateway that's actually mature doesn't
  get used.
- **No real code reuse.** `ethers`/`viem` are dependencies, but grepping
  Gateway's source found **zero** existing EIP-712 (`signTypedData`) call
  sites anywhere in `src/` — only plain `signMessage` (`src/wallet/utils.ts`).
  The typed-data builders would be new code in Gateway just as much as in a
  Condor sidecar; "already has the libraries installed" is a much smaller
  benefit than "already solved this problem."
- **Wrong request shape.** Every existing Gateway route (Fastify,
  `src/wallet/wallet.routes.ts` pattern) is fire-and-return — RPC calls,
  quote fetches. Nothing blocks on external human interaction, and there's
  no start/poll (job-id) pattern to build on — this flow needs one either
  way, so building it in Gateway doesn't save that design work either.
- **Gateway's future is uncertain** — it may be deprecated. Sinking a
  first-of-its-kind, non-trivial piece of infrastructure (WalletConnect
  relay client + a new blocking/job request lifecycle + a new CEX-shaped
  route category) into a service that might go away is a bad bet for a
  spike — if Gateway is deprecated, this flow would need to be re-migrated
  onto Condor's own credential path anyway, which is where the durable logic
  (`add_credential`, `master_account`) already lives regardless.

**Generic vs. Hyperliquid-specific split.** The objections above are weaker
if Gateway hosted only a *generic* capability — "pair with a mobile wallet,
sign this typed-data payload I hand you" — instead of a Hyperliquid-specific
route. A generic WalletConnect-signing primitive is a much better fit for
`src/wallet/` (sibling to `addWallet`/`removeWallet`/`getWallets`, just
another way to connect a wallet) than a CEX-approval route would be; the
Hyperliquid-specific bits (ApproveAgent/ApproveBuilderFee payload
construction, submitting to Hyperliquid's `/exchange`, credential mapping)
would stay in Condor regardless of where the pairing/signing lives. But
going generic doesn't reduce the case for Gateway — it raises the bar:

- An endpoint that signs *any* typed-data payload a caller hands it is a
  materially larger attack surface than one that only ever signs two
  hardcoded Hyperliquid messages. It needs its own scoping/allowlist design
  that a narrow flow doesn't — that's new security work, not saved work.
- There's no second consumer today. Nobody's asked for generic
  WalletConnect signing outside this one Hyperliquid use case; building the
  reusable version now is speculative generality for a spike.
- The relay-client/job-lifecycle work — the actual hard part — is identical
  either way, so genericizing doesn't save the thing that's expensive to
  build. It only changes who's exposed if Gateway is deprecated: a
  "platform primitive" there is a bigger sunk cost to lose than a one-off
  script.

**Decision: keep it Condor-owned** (the `walletconnect_bridge/` sidecar
below), narrow and Hyperliquid-specific — not a generic signing service.
The overlap with Gateway is superficial — same runtime (Node), different
problem shape — and Condor is the repo whose lifetime this bet should
track. If a second real consumer of generic WalletConnect signing shows up
later, this is small enough to extract into a proper primitive then —
possibly in Gateway, possibly not, decided with actual second-use-case
evidence instead of speculation now.

## Key architecture decision: Node sidecar, not Python WalletConnect

`pyproject.toml` has no `eth_account`, `web3`, or `msgpack`, and there is no
mature, actively maintained Python SDK for WalletConnect v2's Sign API
(relay pairing + encrypted JSON-RPC). Re-implementing that protocol in
Python is the single biggest risk in this proposal and isn't worth taking on
for a spike. Node v24 is already available on the host, and the exact
EIP-712 payload builders we need already exist and work in
`frontend/src/lib/wallet/hyperliquid.ts` (`buildApproveAgentTypedData`,
`buildApproveBuilderFeeTypedData`, `signAndSubmit`), built on `viem` (already
a frontend dependency).

So: **a small standalone Node script does all WalletConnect + signing work**,
driven by the Python main process over stdin/stdout JSON lines. Python never
touches private keys or the WC protocol — it just starts the subprocess,
relays commands, and calls the existing `add_credential` path once it gets
signatures back.

External prerequisite (not something I can provision): a free WalletConnect
Cloud (Reown) `projectId`. Wire it through an env var
(`WALLETCONNECT_PROJECT_ID`) and fail fast with a clear error if unset —
flag this before running the manual verification step.

## Implementation

### 0. Branch

First step, before touching any files: `git checkout -b spike/hyperliquid-walletconnect`
(stacked on `spike/mcp-harness-validation`).

### 1. `walletconnect_bridge/` — new Node subprocess (new directory)

A small script (`bridge.mjs` + minimal `package.json` with
`@walletconnect/sign-client` and `viem`) that, per invocation:

- Ports `buildApproveAgentTypedData` / `buildApproveBuilderFeeTypedData` /
  `BUILDER_ADDRESS` / `BUILDER_MAX_FEE_RATE` verbatim from
  `frontend/src/lib/wallet/hyperliquid.ts` (same constants, same domain —
  duplicated intentionally for a spike; unifying into a shared package is a
  follow-up, not spike scope).
- Generates the agent keypair itself (`viem/accounts`, same as the frontend
  does), so Python never sees the raw agent private key mid-flight.
- Opens a WalletConnect pairing on `eip155:42161` (Arbitrum One — matches
  Hyperliquid's signing domain, so no explicit chain-switch step is needed
  the way the browser flow needs `ensureChain`).
- Prints one JSON line per lifecycle event to stdout: `{"event":"uri", "uri":"wc:..."}`,
  `{"event":"approved"}`, then drives the two `eth_signTypedData_v4` requests
  and submits `approveAgent`/`approveBuilderFee` to Hyperliquid's
  `/exchange` endpoint itself (reusing `signAndSubmit`'s logic), finally
  `{"event":"done", "mainAddress":..., "agentAddress":..., "agentPrivateKey":...}`
  or `{"event":"error", "message":...}`.

### 2. `condor/walletconnect.py` — main-process session registry (new file)

Mirrors why delegations live in the main process
(`mcp_servers/condor/condor_client.py`'s comment: subprocess-owned state
doesn't survive beyond the MCP subprocess). A small class that:

- `start()`: spawns `walletconnect_bridge/bridge.mjs` via
  `asyncio.create_subprocess_exec`, reads the first `uri` event, stores the
  process + latest status in an in-memory dict keyed by a generated
  `session_id`, returns `{session_id, uri}`.
- `status(session_id)`: returns the last-seen event for that session
  (non-blocking — a background task keeps draining the subprocess's stdout
  into the stored status as events arrive).
- On `done`, calls the same credential-save logic as `add_credential` in
  `condor/web/routes/settings.py` (`client.accounts.add_credential` for both
  `hyperliquid` and `hyperliquid_perpetual`, reusing
  `buildHyperliquidCredentials`'s field mapping from `wallet/hyperliquid.ts`
  ported to a plain dict).

### 3. `condor/web/routes/settings.py` — two new routes

- `POST /settings/walletconnect/hyperliquid` → `cm.get_client(server)` +
  `walletconnect.start()` → `{session_id, uri}`.
- `GET /settings/walletconnect/hyperliquid/{session_id}` →
  `walletconnect.status(session_id)`, triggering the credential save on
  first transition to `done`.

Same auth/access pattern as the existing `add_credential` route
(`user: WebUser = Depends(get_current_user)`, `cm.has_server_access`).

### 4. `mcp_servers/condor/tools/wallet_connect.py` — new MCP tool (new file)

Modeled directly on `mcp_servers/condor/tools/delegate.py`'s start/get shape:

```python
async def connect_hyperliquid_wallet(action: str, session_id: str = "") -> dict:
    if action == "start":
        result = await call_main_api("POST", "/settings/walletconnect/hyperliquid")
        # also push the QR as a Telegram photo, mirroring condor/routine_store.py's
        # _HttpBot.send_photo pattern, so Telegram users can scan without leaving chat
        return result  # {"session_id", "uri", "next_steps": "poll with action=get"}
    if action == "get":
        return await call_main_api("GET", f"/settings/walletconnect/hyperliquid/{session_id}")
    return {"error": f"Unknown action '{action}'. Use start | get."}
```

Register in `mcp_servers/condor/server.py` next to `delegate`, same
`@mcp.tool()` + `@handle_errors("connect hyperliquid wallet")` pattern.

QR delivery: generate a PNG from the `uri` (new small Python dep,
`qrcode[pil]`) and send it via the existing Telegram `send_photo` machinery
(`condor/routine_store.py`'s `_HttpBot.send_photo` /
`mcp_servers/condor/tools/notification.py`'s bot-token/chat-id config) so a
Telegram user gets a scannable image inline. For non-Telegram MCP hosts
(e.g. Claude Code), the raw `wc:` URI is returned as text — good enough for
a spike; rendering it as an inline image for arbitrary MCP hosts is a
follow-up.

### 5. `/keys` → "🔗 Connect Hyperliquid" (Telegram, deterministic entry point)

Chat-only delivery via the MCP tool turned out clunky in practice: without a
live Telegram bot in the loop, there's no good way for an agent to hand a
user a scannable QR — the fallback (build an HTML page, publish it,
send a link) is slow and adds a browser round-trip for something that should
be instant. The MCP tool's routing also depends on the agent reliably
inferring intent from free-form phrasing.

`handlers/config/hyperliquid_connect.py` adds a deterministic alternative: a
"🔗 Connect Hyperliquid" button on the existing `/keys` menu
(`handlers/config/api_keys.py`). It calls the same
`condor.walletconnect.start_walletconnect_session` /
`get_session_status` functions the MCP tool uses — directly, no HTTP
loopback, since Telegram handlers already run in the main process — sends
the QR via `context.bot.send_photo`, then edits that photo's caption in
place as the session progresses (`pending_approval` → `pending_signatures`
→ `done`/`error`), polling every 3s for up to 6 minutes. QR generation was
factored out into `condor.walletconnect.generate_qr_png` so both this and
the MCP tool's `_send_qr_photo` share one implementation.

This is now the primary, fast path; the MCP tool remains for
non-Telegram/agentic surfaces (Claude Code, etc.), where the skill stub
below still matters for routing.

### 6. Skill stub (optional, small)

A minimal `assistants/condor/skills/connect_hyperliquid_wallet/SKILL.md`
(same frontmatter shape as
`assistants/condor/skills/hyperliquid_tokenized_perps/SKILL.md`) so the
routing rule in `server.py`'s `_build_instructions()` picks this tool up for
phrasings like "connect my Hyperliquid wallet" / "I only have my phone."
Not required for the mechanism to work — the tool is usable directly — but
cheap to add and consistent with how the rest of Condor surfaces
capabilities.

## Explicitly not doing in this spike

- No referral-code linking (`setReferrer`) — separate, additive, already
  proven to work from the browser flow.
- No production session cleanup/expiry, no multi-session-per-user handling.
- No unification of the duplicated EIP-712 constants between
  `frontend/src/lib/wallet/hyperliquid.ts` and the new Node bridge script —
  flagged as a known follow-up if the spike proves out.
- No changes to the existing browser flow.

## Verification

**MCP tool path — validated end-to-end against a real wallet and a live
Hyperliquid account** (not just a smoke test): `start_walletconnect_session`
returned a genuine `wc:` pairing URI, a real mobile wallet paired and signed
both `ApproveAgent` and `ApproveBuilderFee`, and `hyperliquid` +
`hyperliquid_perpetual` landed in `master_account` — confirmed independently
via a direct credentials query against hummingbot-api, not just the tool's
self-reported result. Along the way this also caught and fixed: the SDK's
internal logger polluting the stdout event-prefix protocol,
`client.connect()` hanging forever with no internal timeout on a bad relay
handshake, and a swallowed exception in the credential-save path that
misreported a real failure (a crashed `hummingbot-api` container) as
"process exited unexpectedly."

**`/keys` → "🔗 Connect Hyperliquid" (Telegram) — not yet live-tested**, since
it requires restarting the running `main.py` process to pick up the new
handler. To verify: restart, `/keys` in Telegram, tap the button, confirm the
QR photo arrives and its caption updates live through
`pending_approval` → `pending_signatures` → `done` as you approve on your
phone, then re-run the credentials query above to confirm the save. The
underlying session/save logic is shared with the already-validated MCP path
— this is a new *delivery* surface (`send_photo` + caption polling) on
already-proven mechanics, not new signing/save logic.

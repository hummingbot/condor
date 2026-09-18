# Condor Vaults — architecture

**Status:** as implemented on `feat/condor-vaults-2`, 2026-09-18, and run on a
surfpool fork of mainnet. Program `Bbn3CpNCSH6WmD9uhNJXe76Dy9ouPki2nYyzVvb8jYVy`
(Anchor, `programs/condor_vaults`). The design history and the alternatives
that were rejected are in `docs/plans/CONDOR_VAULTS_2_PLAN.md`; this document
describes what exists.

## 1. In one paragraph

A Condor Vault is a **treasury that trades a strategy and is owned by a program,
not a person**. Anyone creates one, funds it, pins a strategy and lets a
delegate key (theirs, or Condor's crank) trade it. While it is *private* it is
just that person's treasury with extra structure. If they choose, they
**tokenize** it: a fixed-supply token is sold on a Meteora bonding curve, the
raise becomes the vault's capital, and from that moment **nobody — the creator,
the delegate, Condor — can move a token out of the treasury to any key**. The
treasury can still trade freely on allowed venues, including pools created
after launch, because the program checks *recipients* structurally instead of
keeping a list. The creator can direct the vault's trades themselves, run or
pause the agent-driven strategy, change the strategy, and wind the vault down
once — anything they can delegate to an agent they can also do directly, under
the same rules. After a wind-down, holders burn tokens for a pro-rata share of
the quote asset, paid by the program itself.

## 2. Accounts and addresses

A vault is **two PDAs of the program, derived from one random 32-byte `id`**
chosen at creation:

| PDA | Seeds | What it is |
|---|---|---|
| `Vault` | `["vault", id]` | The record and the vault's public identity. Creator, strategy pin, launch terms, state, delegate, mint. |
| `Treasury` | `["treasury", id]` | A system account with no data. Holds the SOL and **owns every token account and LP position**. Also the DBC *pool creator* after tokenize — not to be confused with the vault's creator, who is a person. |

Plus one per program:

| PDA | Seeds | What it is |
|---|---|---|
| `Protocol` | `["protocol"]` | `authority` (rotates keys, may wind down an abandoned vault), `administrator` (the crank's key), `fee_claimer` (where Meteora pays the protocol fee), `dbc_config` (an informational default). |

There is no key that can reach inside either vault account. The treasury signs
only through `invoke_signed` inside the program's own instructions.

```
                 id (32 random bytes, stored on Vault)
                 ├── ["vault", id]            → Vault record (identity)
                 └── ["treasury", id]  → treasury (money, ATAs, positions)
                                                 │
                                                 ├─ execute / execute_unchecked → any venue CPI
                                                 ├─ tokenize                     → DBC pool creator
                                                 └─ redeem                       → pays holders from its quote ATA
```

The `Vault` stores `authority_bump` so every instruction re-derives the
treasury's signing seeds without a `find_program_address` search; the treasury
address itself is derived, never stored, so it cannot disagree with the seeds.

## 3. Roles

| Role | Who | May | May not |
|---|---|---|---|
| **creator** | the treasury that called `create_vault` | **trade for the treasury directly** (everything a delegate can, through the same `execute*` instructions), pin / publish the strategy, run and pause it, install or replace the delegate, claim the pool-creator income, tokenize once, wind down once | after tokenize: move any token to any key |
| **delegate** | one key named on the `Vault` (Condor's crank key by default; the creator may install their own) | act for the treasury through `execute*` | change anything on the `Vault`; anything `execute` refuses |
| **administrator** | `Protocol.administrator` — Condor's backend | `finalize_wind_down` | trade, hold funds, move a delegate |
| **protocol authority** | `Protocol.authority` (a multisig before mainnet) | rotate protocol keys; `wind_down` an abandoned tokenized vault | anything with a vault's funds |
| **holder** | anyone with the vault token | `redeem` after a finished wind-down; `collect_seed` / `collect_leftover` (permissionless) | — |

The creator and the delegate have identical power over the *treasury*; the
difference is only that the creator also governs the `Vault`. That is
deliberate: what a key may do is decided by `execute`, not by whose key it is,
so a creator trading by hand, or installing their own key on a tokenized
vault, costs holders nothing. Delegation adds an agent; it never adds a power
the creator did not already have.

## 4. Lifecycle

```
create_vault ─► [private, Running] ⇄ Paused
                      │ tokenize (one way)
                      ▼
               [tokenized, Running] ⇄ Paused
                      │ wind_down (one way; creator, or protocol authority)
                      ▼
                 WindingDown  ── conversion to quote via execute ──► finalize_wind_down
                                                                          │
                                                                          ▼
                                                                     Redeemable ── redeem
```

**Private is not a state.** A vault is private exactly while `mint` is the
default key; `is_tokenized()` is the one question every guarantee hangs off.
A private vault:

* has no quote asset, no holders, no wind-down (`wind_down` refuses it);
* is emptied by its creator through `execute_unchecked` — a transfer is an
  instruction like any other, so there is no `withdraw` instruction;
* needs nothing from Condor: `install_delegate` does not read `Protocol`, so
  a vault on a chain where Condor initialized nothing still runs.

`tokenize` is the one-way door. `execute_unchecked` refuses from then on, and
every other protection below switches on.

`Running` and `Paused` are the only states in which the treasury acts. Pausing
means "take nothing new"; open positions stay open. `WindingDown` makes
`publish_version` and `set_active` refuse forever, so a vault cannot be wound
down and quietly restarted under another strategy.

## 5. How the treasury acts: `execute_unchecked` and `execute`

Anything the treasury does is a CPI the program makes on its behalf. Both
instructions take `signer, vault, vault_authority, target_program` plus the
target instruction's accounts as `remaining_accounts` and its bytes as `data`.
The program sets the treasury's signer flag and `invoke_signed`s. Guards common
to both: signer is creator or delegate; state is `Running | Paused`; target is
executable and **is not this program** (a treasury that could call
`install_delegate` or `tokenize` as the creator could rewrite who owns it).

* **`execute_unchecked`** — any program, any accounts. Private vaults only.
* **`execute`** — allowed in both phases and the *only* way after tokenize.
  `venues::check_before` runs before the CPI and `check_after` after it.

### 5.1 The recipient rule

The rule is about recipients, because that is where every other custody design
fails: a DEX swap names its output account as an ordinary account and checks
only its *mint* (verified in Meteora DLMM's `swap.rs`), so a treasury that may
"only send tokens to pool vaults" can still swap into a pool and have the
output land in whoever's account the caller wrote down. Destination-scoped
Swig limits cannot stop this; only the signer's own program can look at every
account. So `execute` asks, for **every writable account** in the call:

| The account is… | Before the CPI | After the CPI |
|---|---|---|
| the treasury itself | allowed (rent it pays, lamports it receives) | — |
| a signer (the caller) | allowed (their own lamports) | — |
| a token account owned by the treasury | allowed | — |
| a token account owned by a **venue pool authority** (any account in the call owned by the venue program; plus DAMM v2's fixed authority) | allowed | — |
| owned by the venue program (pool state, bin arrays, positions) | allowed | — |
| an **uninitialised, zero-lamport** system account | recorded as *fresh* | must now be a token account owned by the treasury, a DLMM `PositionV2` whose owner field is the treasury, or venue-owned — else `RecipientNotVault` |
| anything else — notably a token account owned by a key, or a *funded* system account (somebody's treasury) | `AccountNotAllowed` | — |

Non-venue programs are admitted only for what a trading treasury needs from
them, and **a token transfer is absent on purpose**:

| Program | Allowed under `execute` |
|---|---|
| Associated Token | `Create` / `CreateIdempotent` with owner == treasury |
| Token / Token-2022 | `SyncNative` on a treasury-owned account; `CloseAccount` of a treasury-owned account with rent to the treasury |
| System | `Transfer` only treasury → a treasury-owned token account (wrapping SOL); `CreateAccount` only when the payer is a signer other than the treasury |
| anything else | `ProgramNotAllowed` |

Venue allowlist, v1 (`venues.rs`): **Meteora DLMM, Meteora DAMM v2, Raydium
CLMM**. Routers are absent on purpose: a router's output account has the same
shape as a swap's, but its inner legs and the accounts it touches vary per
route, so v1 swaps and LPs directly with pools. Adding a venue is a program
upgrade, which is the right weight for a rule that binds every holder.

### 5.2 What this buys

* **Freedom without withdrawal.** A pool created this morning passes, because
  its reserves are owned by the venue's authority. A recipient that is a key
  fails, whoever's key it is.
* **The creator can sign their own trades** on a tokenized vault, so Condor is
  a convenience, not a custodian.
* Verified on the fork: a DLMM SOL→USDC swap through `execute` lands when
  `user_token_out` is the treasury's ATA and fails with `AccountNotAllowed`
  (6023) when it is another key's ATA. The same redirected swap lands through
  `execute_unchecked` on a private vault — the creator's own money.

## 6. Tokenizing

`tokenize(name, symbol, uri, issue_bps)` is one CPI into Meteora's Dynamic
Bonding Curve **with the treasury as pool creator** (Meteora's term for the
account that owns the launch's fee streams — here the treasury PDA, never the
person). DBC does the whole mint:
Token-2022, fixed supply, immutable authorities. The program never holds mint
authority because it never has one.

**The launch config is chosen by the creator and checked by its terms, not its
address.** A vault prices its own launch off assets it already holds, so each
one creates its own DBC config (`build-launch-config` in Gateway). The config
fixes the **quote asset** — SOL or USDC — which is written onto the `Vault`
as `quote_mint` *at tokenize*, never at creation: it is what the curve sells
for, what the migrated DAMM v2 pool quotes in, what a wind-down converts into
and what a redemption pays. A private vault has none and needs none.

Each economic term is a **bound** (the creator's decision, copied onto the
`Vault` so holders read it there) or a **constant** (changed only by upgrade):

| Term | Kind | Rule |
|---|---|---|
| `migration_fee_pct` | bound | 20–80. The share of the raise that becomes the vault's capital; the rest is permanently locked liquidity. This is the split between the strategy and the holders' exit depth. |
| `creator_trading_fee_pct` | bound | ≤ 50 |
| `migration_fee_option` | bound | one of Meteora's fixed-fee options (customizable option excluded) |
| pool creator's share of migration fee | constant | 100 % — the pool creator is the vault's own treasury, so nothing routes to the partner |
| migration threshold | constant | 10 wSOL-equivalent, the one Meteora's keepers migrate automatically |
| token type / supply | constant | Token-2022, fixed |
| fee claimer | constant | `Protocol.fee_claimer` |
| leftover receiver | constant | the vault's treasury |
| `issue_bps` | argument | share of supply sold; the rest is retained supply — the ceiling on later dilution |

### 6.1 After the curve fills

* **`collect_seed`** (permissionless): the pool creator's migration fee — the
  vault's capital — moves from DBC into the treasury's quote ATA. The pool
  creator is the treasury PDA, so the destination is fixed and anybody may trigger it; Condor's
  promptness is irrelevant.
* **`collect_leftover`** (permissionless): the unsold supply moves into the
  treasury. It is the **retained supply**, and it lives in the treasury on purpose: the
  strategy can market-make the vault's own token — an LP position against its
  own pool — which is how retained supply turns into capital as the market
  buys. There is no `inject`; it was built and removed because this does the
  same job at market price with the strategy as counterparty.
* **`claim_income` / `claim_position_fee`**: the *creator's* income — curve
  trading fees, the surplus above the threshold, and the locked migrated
  position's fees, all of which Meteora pays to the pool creator (the treasury).
  Every destination is derived from `Vault.creator`; these are the only doors
  out of the pool creator's streams. The *vault's* income (LP
  fees from the strategy) arrives in the treasury directly.

There is no buy-and-burn and no `fee_bps`: whether fees are used to buy the
token back is the strategy's discretion, exercised through `execute`.

## 7. The exit

Only a tokenized vault winds down; the ceremony exists to pay holders.

1. **`wind_down`** — creator, or protocol authority for an abandoned vault.
   One way; strategy changes refuse from here on.
2. **Conversion** — through `execute`, by the crank or anyone the vault lets
   act: close every position, swap every non-quote balance to `quote_mint`.
   Nothing moves anywhere; the treasury's own quote ATA *is* the redemption pot,
   because the treasury is the program's PDA and signs the payout itself.
3. **`finalize_wind_down`** — administrator-signed, a deliberate narrowing:
   it checks the token accounts it is handed (every non-quote balance ≤
   `WIND_DOWN_DUST` = 1 000 units) and a stranger could pass a short list and
   strand a position; the administrator is the party that knows the whole
   list. Requires the pot to be non-empty, clears the delegate, sets
   `Redeemable`.
4. **`redeem(amount)`** — any holder, any time after. Burns `amount` and pays
   `amount / circulating × pot`, where **circulating = mint supply − pool
   vault balance − retained supply (the treasury's own balance of its token)**. The
   pool's tokens are permanently locked liquidity; the retained supply's were never
   sold. Supply is read before the burn so a redemption is priced on the
   state it was quoted against. Paid by a program with no way to refuse: no
   delegate, no administrator, no key.

## 8. Off-chain system

```
browser (ConnectorKit wallet) ── signs creator builds
   │
Condor web (FastAPI :8088)  /api/v1/vaults  ── record store, config hash, crank
   │                                  │
   │            hummingbot-api ── /gateway/proxy ──► Gateway  /chains/solana/vaults
   │                 (agents' executors build for walletAddress = the treasury)
   ▼
surfpool / Solana ◄── Gateway signs: creator builds are returned unsigned;
                      delegate actions are wrapped into execute* and signed by the stored delegate
```

### 8.1 Gateway (`~/fengtality/gateway`, `feat/condor-vaults`)

* **Treasury type `vault`.** `getWalletType` returns it for any address in
  `conf/wallets/solana/vault-wallets.json` (`walletAddress`, `vaultAccount`,
  `delegateAddress`, `encryptedDelegateKey`). The signing chokepoint routes
  it to `sendAsVaultDelegate`, which decomposes the connector-built
  transaction (legacy or v0, lookup tables resolved), wraps **each
  instruction** in `execute_unchecked` (private) or `execute` (tokenized) via
  `src/vaults/vault-wrap.ts`, sizes the compute budget, and signs with the
  delegate as fee payer. Every existing connector — Meteora, Raydium, the
  balance and position readers — therefore works for a treasury
  unchanged: it builds for `walletAddress` as for any wallet.
* **Vault routes** (`/chains/solana/vaults`): `GET /`, `GET /:vaultAccount`,
  `GET /protocol`; creator builds returned unsigned for the browser —
  `build-create-vault` (create + install-delegate + pin in one transaction,
  minting and storing the delegate), `build-execute` (any transaction a
  `/trading/*/build-*` route built for the treasury, wrapped into
  `execute*` for the creator to sign — creator-driven trading), `build-deposit`,
  `build-install-delegate`, `build-publish`, `build-set-active`,
  `build-launch-config`, `build-tokenize`, `build-claim-income`,
  `build-wind-down`, `build-redeem`; delegate-signed — `withdraw`
  (private only), `fund-delegate`; platform-signed — `initialize`,
  `set-authority`, `set-administrator`, `collect-seed`, `collect-leftover`,
  `finalize-wind-down`. The platform key is Gateway's default Solana treasury,
  named in `conf/vaults.json`.
* `src/vaults/vault-program.ts` is the typed client over the vendored IDL:
  `vaultPda(id)`, `vaultAuthorityPda(id)`, `decodeVault` (re-derives
  `treasury` from the stored id), and one builder per instruction.

### 8.2 Condor (`condor/`)

* **Record store** (`vault_store.py`): one JSON per user keyed by the Vault
  PDA — label, server, `vault_id`, treasury and creator addresses, the private
  config and its pin. Written *before* the create signature, promoted by
  `confirm` once the chain carries the config hash, dropped by reconcile if
  the transaction never landed. Lifecycle state is never mirrored; the chain
  is read on every listing.
* **Config commitment.** What goes on chain is the public agent folder by
  commit (`AgentRef`) and the sha256 of the private config's canonical
  encoding (`config_hash`, one digest held identical in Python and TS). A
  crank handed a config hashes it and compares; nobody, Condor's operators
  included, can run a vault on parameters its creator did not sign.
* **Web routes** (`condor/web/routes/vaults.py`, `/api/v1/vaults`): create /
  confirm, the creator builds, `withdraw`, `holdings` and `lp-positions`
  (public; positions fanned out over Meteora / Raydium / Orca CLMM and AMM
  through hummingbot-api's `positions_owned`), `scan`, tokenize and redeem.
* **Crank** (`vaults_crank.py`, per server): reads every vault from Gateway,
  keeps an hbapi account per vault treasury, starts or stops the strategy engine
  as state demands, tops up the delegate's gas from the treasury, calls the
  permissionless collectors after migration, and attempts
  `finalize_wind_down` on winding-down vaults each pass.

### 8.3 Frontend (`frontend/`)

Vaults list (All / My vaults — "mine" is the connected browser wallet as
creator), Create Vault, Vault detail with public Summary / Portfolio (Solana
tokens + LP positions of the treasury) / Activity tabs and creator-gated Agent
and Token tabs, a Transfer drawer (deposits signed in the browser; private
withdrawals through the delegate), and settings for browser and Gateway
wallets. Creator actions are gated on `useCanSign` — the connected wallet
must be the creator.

## 9. What each party is trusting

* **A holder** trusts the program bytes — specifically `venues.rs` and
  `execute.rs` — and the program's upgrade authority. Nothing in Condor's
  backend, the crank, or the delegate key is in their trust base: any of them
  can at worst trade badly on an allowed venue, and none can move a token to
  a key. Redemption is paid by the program with no party able to decline.
* **A creator** of a private vault trusts nobody: it is their wallet, their
  delegate, and `execute_unchecked` is their withdrawal.
* **Condor** is trusted with nothing it can steal. Its crank's key is a
  delegate like any other; its administrator role can only *finalize* a
  wind-down, and only once the treasury is already in the quote asset.
* **The upgrade authority** is the residual trust. It moves to a multisig
  before the first mainnet vault; the protocol instructions take a separate
  payer and never read the authority's lamports so a Squads vault can hold it
  without a migration.

What the program deliberately does **not** do: attest that Condor reviewed a
strategy (Condor's scan is a private run policy), keep a name registry (a
vault's identity is its address), or put oracle-bounded prices on the
wind-down conversion (that is why finalize is administrator-signed and the
conversion is not permissionless — see the plan for the open question).

## 10. Instruction reference

| Instruction | Signer | Phase | Effect |
|---|---|---|---|
| `initialize` | program upgrade authority | — | writes `Protocol` |
| `set_authority`, `set_administrator` | protocol authority | — | rotates a protocol key |
| `create_vault(id, fund_lamports)` | creator | — | `Vault` + funded treasury (≥ rent floor) |
| `install_delegate(key)` | creator | not Redeemable | sets the delegate; no co-signer |
| `pin(agent_ref, config_hash)` | creator | version 0 | strategy v1, starts Running |
| `publish_version(...)` | creator | Running / Paused | new strategy version |
| `set_active(bool)` | creator | Running / Paused | pause / resume |
| `execute_unchecked(data)` | creator or delegate | private, Running / Paused | treasury invokes anything |
| `execute(data)` | creator or delegate | Running / Paused | treasury invokes a venue under the recipient rule |
| `tokenize(name, symbol, uri, issue_bps)` | creator | private | DBC launch, treasury as creator; writes quote and terms |
| `collect_seed` | anyone | migrated | creator's migration fee → treasury |
| `collect_leftover` | anyone | migrated | unsold supply → treasury |
| `claim_income(source)`, `claim_position_fee` | creator | tokenized | creator streams → creator |
| `wind_down` | creator, or protocol authority | tokenized, Running / Paused | one-way stop |
| `finalize_wind_down` | administrator | WindingDown | checks conversion, clears delegate, Redeemable |
| `redeem(amount)` | any holder | Redeemable | burn, pro-rata quote payout |

Error groups worth knowing when reading logs: `NotPrivate` (an
`execute_unchecked` after tokenize), `ProgramNotAllowed` /
`InstructionNotAllowed` / `AccountNotAllowed` / `RecipientNotVault` (the four
refusals of `execute`), `SelfInvoke`, `WindDownIncomplete` /
`RedemptionPotEmpty` (finalize too early), `LaunchTermsMismatch` /
`MigrationFeeOutOfRange` (a config outside `launch_rules`).

## 11. Where it stands

Run on a fresh mainnet fork on 2026-09-18: create in one signature, SOL and
USDC deposits, delegate withdraw and gas top-up through `execute_unchecked`,
pause / resume, private wind-down refused, a Gateway connector swap for the
vault treasury landing through the wrap path, and the honest-vs-redirected DLMM
swap pair through `execute` behaving as §5 says.

Not yet exercised against the rewritten program: the tokenized half —
`tokenize` → `execute`-only trading → `collect_seed` / `collect_leftover` →
`wind_down` → conversion → `finalize_wind_down` → `redeem`. Open design
items: routers under `execute`, a permissionless wind-down conversion with
price bounds, and the multisig handover of the upgrade authority.

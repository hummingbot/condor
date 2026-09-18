# Condor Vaults — adversarial verification of the security review

Reviewed 2026-09-18 against the working tree at branch `feat/condor-vaults-2`
(head `43de5aec`, with the uncommitted modifications present). This document
**verifies someone else's findings** rather than restating them: every claim
below was re-derived from the source, and several are corrected, re-scoped, or
rejected.

A separate reviewer's document already exists at
`docs/CONDOR_VAULTS_ARCHITECTURE_REVIEW.md` (findings A1–A15). It has been left
untouched; where a finding here corresponds to one of its items the mapping is
given. `docs/CONDOR_VAULTS_2_REVIEW.md` (the older Swig-era review) is likewise
untouched.

**What was run.** `cargo test --manifest-path programs/condor_vaults/Cargo.toml
--lib` → 12 passed, 0 failed. No source file was modified. Venue semantics were
checked against the IDLs shipped in
`gateway/node_modules/@meteora-ag/{dlmm,cp-amm-sdk,dynamic-bonding-curve-sdk}`.
No transaction was submitted.

**Headline.** Of the eight claims put to me, **six are REAL**, one is **REAL but
mis-scoped** (it is a trust-model statement, not a bug), and one is **REAL with
the mechanism partly wrong**. Nothing in the list is outright WRONG, but three
of the eight are materially *understated* — the true mechanism is worse than the
claim. Separately I found **seven issues the review does not contain**, one of
which (§B2, redemption dilution by the still-live graduated pool) is a
structural error in `redeem`'s denominator argument rather than an
implementation slip, and one of which (§A5, unchecked launch-config bytes) is a
plausible complete-theft path that the review's A8 gestures at without landing.

---

## Ranked findings

Severity scale: **Critical** = a direct path to taking value that belongs to
token holders, or permanent loss of it. **High** = substantial loss, a frozen
exit, or an advertised financial term that is not enforced. **Medium** =
operational failure, griefing, or disclosure. **Trust-model** = the code is
doing what it was designed to do and the *claim about it* is what is wrong.

---

### A1 — The signer exemption voids the recipient rule entirely

**Claim 2. Verdict: REAL. Severity: Critical.** (≈ A2 in the other review.)

**Evidence** — `programs/condor_vaults/src/venues.rs:85-102`:

```rust
for account in accounts {
    if !account.is_writable { continue; }
    let key = account.key();
    if key == *treasury { continue; }
    if account.is_signer { continue; }              // ← line 93
    if let Some(holding) = token::read_token_account(account) {   // ← line 96
        if holding.owner == *treasury || pool_authorities.contains(&holding.owner) { continue; }
        ...
        return Err(VaultError::AccountNotAllowed.into());
    }
```

The signer branch is unconditional and sits **before** the token-account branch,
so no writable signer is ever subjected to the ownership test.
`instructions/execute.rs:109` then calls `check_after`, which only walks
`before.fresh` (`venues.rs:122`) — and an account that already existed is never
in `fresh` (`venues.rs:110`). So a writable signer is checked by nothing, before
or after.

**The attack.** The delegate (or creator — `may_act`, `execute.rs:61`, accepts
either) generates a throwaway keypair `K`, funds it, and initialises an SPL
token account *at address `K`* for the vault's quote mint (a token account's
address is an ordinary pubkey; signature verification does not care that the
token program owns the account). They then submit one `execute` against Meteora
DLMM with `K` in the swap-output slot, marked writable **and signer**, and sign
the outer transaction with both the delegate key and `K`. `check_before` hits
line 93 and skips; the swap pays out to `K`; `check_after` ignores it. They walk
away with the entire output of the swap, and can repeat until the treasury is
empty. **Nothing about this requires a mispriced pool, a fake mint, or a venue
bug.** It is the single instruction the design exists to prevent.

The same exemption also admits a pre-created **position NFT account** at a
keypair address, which hands the attacker the LP position itself — after which
they remove liquidity by calling DAMM v2 directly, never touching this program
again.

**Smallest correct fix.** Two lines. Move the `is_signer` branch *after* the
`read_token_account` branch, and narrow it to accounts the treasury could not
have funded — realistically `account.owner == system_program::ID &&
account.data_is_empty()`, i.e. "a plain wallet paying rent", which is all the
comment on line 94 ever claimed. Cost: reorder two branches plus one
conjunction. No new instruction, no oracle.

---

### A2 — SPL approvals set before `tokenize` survive it

**Claim 3. Verdict: REAL. Severity: Critical.** (≈ A3.)

**Evidence.** `execute_unchecked` (`instructions/execute.rs:97-101`) applies
*no* account or instruction checks while the vault is private:

```rust
pub fn execute_unchecked<'info>(ctx: Context<'info, Execute<'info>>, data: Vec<u8>) -> Result<()> {
    may_act(&ctx)?;
    require!(!ctx.accounts.vault.is_tokenized(), VaultError::NotPrivate);
    invoke_as_treasury(&ctx, data)
}
```

`instructions/tokenize.rs:117-202` sets `vault.mint`, `dbc_pool`, `quote_mint`,
`issue_bps` and the three launch terms. It revokes nothing, and it cannot: the
treasury's token accounts are not passed to it at all.

**The attack, and why it is worse than the claim states.** Before tokenizing,
the creator calls `execute_unchecked` with SPL `Approve` (tag 4) on *the
treasury's associated account for the intended quote mint*, delegate = their own
key, amount = `u64::MAX`. The ATA address is deterministic
(`token.rs:94-104`), so it can be created and approved **while empty** — the
creator does not need to hold the asset, or even to have decided to tokenize.
They then launch. When the curve graduates, `collect_seed`
(`instructions/collect_seed.rs:101-123`) deposits 98 % of the unlocked raise
into exactly that account, and `finalize_wind_down` later requires exactly that
account to be the redemption pot (`instructions/wind_down.rs:150-156`;
`instructions/redeem.rs:124-129`). The SPL delegate transfers the lot out with a
plain `spl_token::Transfer` signed by their own key. The vault program is never
invoked and has no opportunity to refuse. `SetAuthority`
(`AuthorityType::AccountOwner`) is a second variant of the same hole.

**Smallest correct fix.** There is no cheap one, and that is the finding. The
honest options are (a) `tokenize` takes the treasury's token accounts as
`remaining_accounts` and CPIs `Revoke` + asserts `close_authority` /
`delegate` are unset on each — which is only as complete as the list it is
handed, i.e. the same weakness as `finalize_wind_down`; or (b) forbid `Approve`,
`SetAuthority` and `InitializeAccount`-with-delegate under `execute_unchecked`
from the start, by routing it through a data check like
`check_base_program`. (b) is sound and costs the private phase some freedom;
(a) is a new instruction and is not airtight. Recommend (b).

---

### A3 — `redeem` takes the pool balance on the caller's word

**Claim 4. Verdict: REAL, and understated. Severity: Critical.** (≈ A4.)

**Evidence** — `instructions/redeem.rs:66-67` and `111-116`:

```rust
/// CHECK: the pool's vault for the token, excluded from the denominator.
pub pool_token_vault: UncheckedAccount<'info>,
...
let pool_tokens = token::require_token_account(&ctx.accounts.pool_token_vault.to_account_info())?;
require_keys_eq!(pool_tokens.mint, ctx.accounts.vault.mint, VaultError::WrongMint);
```

That is the whole check: **the mint, and nothing else.** Contrast
`retained_token_account` two lines down (`redeem.rs:117-122`), which *is*
rebuilt with `token::require_associated`, and `redemption_pot`
(`redeem.rs:124-129`), likewise. The pool vault is the one balance in the
instruction that is accepted rather than derived — which the module's own
opening note (`token.rs:8-12`, "Token accounts are derived, never accepted")
says is precisely how a redemption pays out of someone else's balance.

**Why it is worse than claimed.** The claim is that passing the retained-supply
account a second time double-subtracts it. True, but the constraint is only
"any initialised token account of this mint", so the attacker passes **the
largest token account of the vault's mint they can find** — another holder's
account, a market maker's, or one they stuffed themselves. The denominator
`redeemable_supply = supply − pool − retained` (`redeem.rs:145-149`) shrinks by
that whole balance, and `payout = amount * pot / redeemable_supply`
(`redeem.rs:153-157`) scales up by the same factor. With a sufficiently large
substituted balance, a small holder drains the entire pot in one call; the only
brake is `require!(amount <= redeemable_supply)` on line 151.

**Smallest correct fix.** The program does not currently store the graduated
DAMM v2 pool or its token vault, so the cheapest sound fix is a constraint the
attacker cannot satisfy: require `pool_tokens.owner ==
dbc::DAMM_V2_POOL_AUTHORITY` (a fixed constant already imported into
`venues.rs`). That reduces the attack to "create your own DAMM v2 pool of the
vault's mint and park tokens in it", which costs the attacker the tokens they
are trying to inflate against. The complete fix is to record the graduated
pool's base vault on the `Vault` at `collect_seed` time (when the pool is known
to have migrated) and pin it by address here. Cost: one field plus one
assignment; no new instruction.

---

### A4 — `wind_down` disables the only instruction that can complete a wind-down

**Claim 5. Verdict: REAL. Severity: Critical (permanent loss of funds).** (≈ A6.)

**Evidence.** `instructions/wind_down.rs:78` sets `vault.state =
VaultState::WindingDown`. `instructions/execute.rs:62-65`:

```rust
require!(
    matches!(vault.state, VaultState::Running | VaultState::Paused),
    VaultError::VaultNotActive
);
```

`may_act` is shared by **both** `execute` and `execute_unchecked`
(`execute.rs:98`, `execute.rs:104`), so from the moment `wind_down` lands the
treasury cannot act at all. There is no way back: `set_active` and
`publish_version` both gate on `accepts_strategy_changes()`
(`instructions/strategy.rs:88-91`, `103-106`), which is false in `WindingDown`
(`state.rs:162-164`).

This directly contradicts the program's own documentation of itself —
`wind_down.rs:16-18`: *"Between the two calls the conversion happens through
`execute`, by the crank or by anyone the vault lets act: close every position,
swap every non-quote balance to `quote_mint`."* — and
`docs/CONDOR_VAULTS_ARCHITECTURE.md:85`, which draws the arrow
`WindingDown ── conversion to quote via execute ──► finalize_wind_down`.

**Consequence.** Either the treasury was *already* entirely in the quote asset
when `wind_down` was called — in which case an honest administrator can finalize
— or every open position and every non-quote balance is stranded forever, since
`finalize_wind_down` refuses while any listed balance exceeds dust
(`wind_down.rs:140-143`) and `execute` can never close them. If the pot happens
to be empty, `RedemptionPotEmpty` (`wind_down.rs:156`) leaves the vault in
`WindingDown` permanently, with no redemption ever possible. Note also that the
**protocol authority** can put any tokenized vault into this state unilaterally
(`wind_down.rs:64-72`).

**Smallest correct fix.** Add `VaultState::WindingDown` to the `matches!` on
`execute.rs:63` **for `execute` only**, leaving `execute_unchecked` on
`Running | Paused`. That means splitting `may_act` into the shared part and a
per-instruction state predicate — perhaps eight lines. It also makes the
existing dust/pot checks meaningful for the first time.

---

### A5 — Launch terms the program does not read, including the mint authority

**Claim 8 (partly), plus new material. Verdict: REAL. Severity: Critical if DBC
accepts the config; High regardless.** (A8 raises this direction but does not
identify the fields.)

`instructions/tokenize.rs:216-268` (`check_launch_terms`) is the only thing
standing between a holder and a caller-supplied config account
(`tokenize.rs:83-85`: `pub config: UncheckedAccount<'info>` — checked "term by
term", never by address). I reconstructed the `PoolConfig` layout from the DBC
IDL (`@meteora-ag/dynamic-bonding-curve-sdk`) and confirmed every offset in
`dbc.rs:108-120` is correct. That reconstruction also shows exactly which bytes
are **not** read:

| offset (post-disc.) | field | read? |
|---|---|---|
| 231 | `partner_permanent_locked_liquidity_percentage` | **no** |
| 232 | `partner_liquidity_percentage` | **no** |
| 233 | `creator_permanent_locked_liquidity_percentage` | **no** |
| 234 | `creator_liquidity_percentage` | **no** |
| 238 | `token_update_authority` | **no** |
| 288+ | `locked_vesting_config` | **no** |
| 227 / 224 | `token_decimal` / `collect_fee_mode` | no |

Three of these matter:

1. **`token_update_authority` (238).** The SDK enum is
   `CreatorUpdateAuthority=0, Immutable=1, PartnerUpdateAuthority=2,
   CreatorUpdateAndMintAuthority=3, PartnerUpdateAndMintAuthority=4`. Two of
   the five values hand the **creator a mint authority on the vault's own
   token**, which is the exact thing `state.rs:95-97` says must never happen
   ("`issue_bps` means nothing against a mint that can print more") and
   `tokenize.rs:46-49` asserts is impossible ("a fixed supply and immutable
   authorities… This program never touches mint authority, because it never has
   one to touch"). The program checks `fixed_token_supply` (byte 236,
   `tokenize.rs:238`) — a different field — and never looks at 238. A creator
   who mints freely after graduation redeems the entire pot against their own
   printed supply. *Caveat, stated plainly:* the SDK's client-side guard
   (`dist/index.js:3747`) says mint-authority options are "only supported for
   transfer-hook configs", which suggests DBC enforces the same on chain. If so
   the path requires a transfer-hook config — and a transfer hook on the vault's
   token is itself an unchecked term that can block holders from selling. **This
   one needs a fork test; you have the harness for it.** Either way the byte is
   unread.
2. **The four liquidity-distribution percentages (231–234).** The advertised
   product is "20–80 % of the raise becomes *permanently locked* liquidity the
   holders exit through" (`state.rs:44-53`;
   `gateway/src/vaults/launch-config.ts:162-168`, "the market a holder exits
   through must not be something anyone can withdraw"). What the program
   actually enforces is `100 - migration_fee_pct` — i.e. how much of the raise
   is *not* taken as fee. **Whether the resulting liquidity is locked at all,
   and whose it is, is four bytes nobody reads.** A config with
   `creator_liquidity_percentage = 100` produces a fully withdrawable position;
   with `partner_liquidity_percentage = 100`, the migration liquidity becomes
   the fee-claimer's. The bound named in `Vault.locked_liquidity_pct` and shown
   to buyers is therefore a number about fees wearing the name of a number about
   locks.
3. **`locked_vesting_config`.** A non-zero vesting allocation carves supply out
   to a Meteora locker. Those tokens are in `mint.supply` but are in neither the
   pool vault nor the treasury's ATA, so `redeem`'s denominator
   (`redeem.rs:145-149`) counts them and no one can ever claim them — every
   holder is silently underpaid by that fraction.

A fourth, related: **`quote_mint` is whatever the config names**
(`tokenize.rs:224`). Nothing restricts it to wSOL or USDC, so a creator may pick
a mint on which they hold the freeze authority and freeze the redemption pot
after the raise.

**Smallest correct fix.** Five `require!`s in `check_launch_terms` and five more
fields on `PoolConfigParams`: `token_update_authority == 1 (Immutable)`,
`partner_liquidity_percentage == 0`, `creator_liquidity_percentage == 0`,
`partner_permanent + creator_permanent == 100`, `locked_vesting` all-zero. Plus
a decision on whether `quote_mint` needs an allowlist (it probably does; that is
a design call, not a patch).

---

### B1 — `claim_position_fee` does not check *which* pool

**Claim 7, second half. Verdict: REAL. Severity: High.** (≈ A9.)

**Evidence** — `instructions/income.rs:230-231`:

```rust
/// CHECK: the graduated DAMM v2 pool.
pub pool: UncheckedAccount<'info>,
```

No `address =`, no relation to `vault.dbc_pool`, no check in the handler. The
handler's only gates (`income.rs:268-303`) are: the position NFT account is
owned by the treasury (276-281), and the pool's two mints are the vault's mint
and quote mint in either order (284-291). The destinations are then derived from
`Vault.creator` (292-303) — the **creator's personal wallet**.

**The attack.** `instructions/retained.rs:9-15` describes, as the intended
design, the strategy market-making its own token against its own pool with a
treasury-owned LP position. That position has exactly the pair
`(vault.mint, quote_mint)` and exactly the required NFT owner. So the creator
passes *that* position instead of the launch-created locked one, and every fee
the **strategy** earned — which is vault income belonging to holders
(`income.rs:6-8` is explicit that the two incomes "never mix") — is swept into
their own ATAs. Nothing distinguishes the two positions at any point.

**Smallest correct fix.** Record the graduated pool and the locked position on
the `Vault` (they are knowable at `collect_seed`, which already proves
`is_migrated`), and pin `pool` and `position` by address here. Cost: two fields
and two `address =` constraints. Partial mitigation available today: assert the
position is permanently locked, which the launch position is and a strategy
position is not — but that is a DAMM-layout read, not a one-liner.

---

### B2 — The redemption denominator is wrong because the graduated pool never stops trading

**Not in the review. Verdict: REAL. Severity: High.**

`redeem.rs:18-22` justifies excluding the pool balance like this: *"Buying from
the pool raises the denominator and hands the buyer the tokens; selling does the
reverse; arbitrage keeps the pool price and the redemption value in step."*

That argument requires the pool's quote side to be fungible with the redemption
pot. It is not. The migration liquidity is permanently locked in a DAMM v2 pool;
quote paid into it can never reach `redemption_pot`, which is funded only by
`collect_seed` and by `execute` swaps — and after `finalize_wind_down` there are
no more of either. The pool nevertheless keeps trading forever.

**The attack.** After the vault is `Redeemable`, pot = Q, supply = S, pool holds
P. An arbitrageur buys the pool's entire inventory P at the pool's stale price
(the pool has not repriced to NAV, because nothing arbitrages it back into the
pot). The denominator goes from `S−P−R` to `S−R`, and the buyer now redeems P
tokens for `P·Q/(S−R)`. Whenever NAV per token exceeds the pool's marginal
price — the normal case for a vault that made money, since the pool's price is
frozen at whatever the last trade left it — this is free money, funded entirely
by diluting the holders who did nothing. Symmetrically, if NAV is below the pool
price, holders sell into the locked pool and the pot is left over-weighted.
Either way the locked liquidity acts as a one-way leak on the pot.

**Smallest correct fix.** Snapshot the denominator once, at
`finalize_wind_down`, and store it on the `Vault`; `redeem` then divides by a
fixed number and the pool's later trading is irrelevant. Cost: one `u64` field
and moving the three reads. This is also strictly cheaper than what `redeem`
does today and removes A3's attack surface as a side effect.

---

### B3 — `permanent_lock_position` is reachable through `execute`

**Claim 7, first half, made concrete. Verdict: REAL. Severity: High.**

DAMM v2's `permanent_lock_position` (verified in the cp-amm IDL,
`dist/index.js:2310`) takes exactly: `pool` (writable), `position` (writable),
`position_nft_account` (readonly), `signer`, event authority, program. Under
`check_before`, `pool` and `position` are DAMM-owned so they take the
`account.owner == program` branch (`venues.rs:103-105`); `position_nft_account`
is not writable so it is skipped at line 87; the signer is the treasury. **The
call passes cleanly.**

A creator or a compromised delegate can therefore permanently and irreversibly
lock the vault's entire LP position. There is no owner, no instruction, and no
upgrade that can undo it — Meteora's lock is permanent by construction. Combined
with A4, this also makes the wind-down unfinishable. It is a pure destruction
vector: the attacker gains nothing, but a competitor, an ex-delegate, or a
creator holding a short position does.

The general shape of claim 7 — *"allowed venue instructions can set operators /
fee beneficiaries / locks, and the recipient rule says nothing about them"* — is
therefore **REAL**. A second confirmed instance: DLMM's
`update_position_operator` (IDL: `position` writable + `owner` signer only)
passes `check_before` and writes an arbitrary key into `PositionV2.operator`. I
could not read lb_clmm's own source here, so I will not claim how much an
operator may do; the finding stands regardless, because the custody guarantee is
being delegated to an access-control list this program never inspects.

**Smallest correct fix.** `check_before` currently ignores `data` entirely for
venue programs. It needs a per-venue **denylist of instruction discriminators**
— at minimum DAMM v2 `permanent_lock_position` ([165,176,125,6,231,171,186,213])
and DLMM `update_position_operator`. Cost: one match on `data[..8]` per venue,
plus the maintenance burden of tracking three venues' instruction sets across
their upgrades. That burden is real, and it is the price of the "structural
rather than an allowlist" choice in `venues.rs:13-17`.

---

### B4 — `finalize_wind_down` proves almost nothing

**Claim 6. Verdict: REAL on every sub-point. Severity: High.** (≈ A7.)

`instructions/wind_down.rs:130-144` walks `remaining_accounts` and requires each
treasury-owned, non-quote, non-own-mint token balance to be `<= WIND_DOWN_DUST`.
Four separate holes, three of which the claim names correctly:

1. **Position NFTs pass.** `WIND_DOWN_DUST = 1_000` (`state.rs:103`); a position
   NFT has `amount == 1`; `1 <= 1_000`. The comment immediately above the loop
   (`wind_down.rs:119-123`) asserts the opposite in so many words: *"A position
   NFT is such a balance — one unit of its own mint — so an open position is
   caught by the same rule that catches a forgotten token."* **It is not.** This
   is the cleanest single contradiction between comment and code in the program.
2. **DLMM positions are invisible.** A `PositionV2` is a DLMM-owned account, not
   a token account, so `token::read_token_account` returns `None`
   (`token.rs:56-59`) and `wind_down.rs:131-133` `continue`s past it.
3. **Native SOL is not considered at all.** The treasury is a system account
   holding lamports; nothing in the loop looks at `.lamports()`.
4. **The list is the caller's.** True, and it is the stated design
   (`wind_down.rs:22-26`). But see B5: making the administrator the author of
   the list is exactly what makes the check both trust-dependent *and*
   griefable, and the two cannot be fixed at once from this instruction.

An absent or unwilling administrator blocks redemption forever — there is no
timeout and no permissionless fallback. That is **trust-model**, and it is
disclosed (`architecture:341-342`), but it is disclosed as *"can only finalize a
wind-down once the treasury is already in the quote asset"*, which sells it as a
weak power. It is also the power to never finalize.

**Smallest correct fix.** For (1): special-case supply-1 mints, or lower the
dust test to `amount == 0` for any mint whose supply is 1. For (2): no cheap fix
— the instruction would have to accept and parse DLMM position accounts. For
(3): compare `treasury.lamports()` against the rent floor. None of these repairs
(4), which is structural.

---

### B5 — Anyone can block redemption forever with a junk token

**Not in the review (the brief lists it as "verify if cheap"). Verdict: REAL.
Severity: High.**

The treasury's associated account for *any* mint can be created by anyone
(`create_idempotent` is permissionless), and anyone can then transfer tokens
into it. `gateway/src/vaults/vault.routes.ts:1032-1034` shows the intended
honest path: the finalize route enumerates the treasury's token accounts from
chain unless the caller names them. So an attacker sends 1,001 units of a
worthless mint to the treasury's ATA for it; the enumerated list now contains a
non-quote balance above dust; `finalize_wind_down` returns `WindDownIncomplete`
forever, because A4 means `execute` can no longer sell it. Cost of the attack:
one transaction and some rent.

The only escape is for the administrator to pass a **hand-filtered**
`tokenAccounts` list — at which point the check is no longer verifying anything,
which is B4(4). The two findings are duals: an honest administrator can be
griefed, and an administrator who cannot be griefed is an administrator whose
check is vacuous.

**Smallest correct fix.** There isn't a small one. The realistic answer is to
stop enumerating and instead require the *positive* facts: pot funded, treasury
lamports at the floor, and an explicit creator-signed attestation of the
position list — or drop the emptiness check and accept that redemption is
pro-rata over the pot with leftovers stranded.

---

### B6 — The 10-SOL graduation threshold is unreachable through Gateway's builder

**Claim 8, plus the coordinator's fork evidence. Verdict: REAL, and I confirm
the code reading. Severity: High (liveness — no vault can tokenize today).**

`instructions/tokenize.rs:236-243` requires, as an exact equality:

```rust
&& c.migration_quote_threshold == rules::GRADUATION_QUOTE_THRESHOLD
```

with `GRADUATION_QUOTE_THRESHOLD = 10_000_000_000` (`state.rs:93`).
`gateway/src/vaults/launch-config.ts:123-177` builds its config with
`buildCurveWithMarketCap({ … initialMarketCap, migrationMarketCap … })`, and
that builder **derives** `migrationQuoteThreshold` from the market caps and the
supply split — there is no parameter to set it. The coordinator's measured
52_240_774_992 is consistent with that. So the reading is correct: **no config
this builder produces can pass `tokenize`, except by numerical coincidence.**

One correction to the framing: the incompatibility is with *this builder*, not
with the SDK. `buildCurve` (SDK `dist/index.d.ts:7666`) takes
`migrationQuoteThreshold` and `percentageSupplyOnMigration` directly. So the fix
is a Gateway change — either switch builders, or solve for the
`migrationMarketCap` that yields exactly 10 SOL — not necessarily a program
change. But the deeper tension the coordinator identifies is real: with
`buildCurveWithMarketCap`, fixing the threshold *fixes the graduation market
cap*, so "each vault prices its own launch off its NAV"
(`tokenize.rs:17-20`) and the constant threshold cannot both hold in the form
the docs describe.

I could not reproduce the `InvalidTokenSupply` (6020) result for
`lockedLiquidityPct` ∈ {20, 30, 70, 80} without a fork, so I neither confirm nor
dispute it. It is consistent with the builder solving a supply equation that
only closes for particular splits. If it holds, `state.rs:286-299`'s two tests
(`the_locked_liquidity_range_is_a_real_range`,
`every_launch_has_both_a_market_and_a_strategy`) assert a range no launch can
reach — the tests pass because they only compare constants to each other.

---

### B7 — `issue_bps` and the config's `leftover` are two numbers for one quantity

**Claim 8, plus the coordinator's observation. Verdict: REAL. Severity: High
(the advertised dilution ceiling is not a ceiling).**

`tokenize(…, issue_bps)` writes `Vault.issue_bps` (`tokenize.rs:196`) after
checking only `0 < issue_bps <= 10_000` (`tokenize.rs:132-135`). **Nothing ties
it to how much the curve actually sells.** What governs that is the config's
`leftover` (and the curve shape), which the program never reads and which
`gateway/src/vaults/launch-config.ts:132` hardcodes to `0` — directly under a
comment saying *"Whatever the curve does not sell is the vault's retained
supply, collected after graduation by `collect_leftover`."* With `leftover: 0`
there is no retained supply to collect, `collect_leftover`
(`instructions/retained.rs:96`) moves nothing, and the entire retained-supply
argument in `retained.rs:1-38` and `redeem.rs:24-30` — which is what makes
`issue_bps` "the ceiling on how far a holder can be diluted"
(`state.rs:203-208`) — has no on-chain referent.

So `Vault.issue_bps` is a number a buyer is told to read as a commitment
(`state.rs:205-208`: *"Buyers read it as the ceiling on how far the creator
could dilute them, so it is on chain beside the strategy rather than in a
listing"*) and which commits nothing.

**Smallest correct fix.** Derive `issue_bps` in `tokenize` from the config
instead of accepting it as an argument — the config holds
`pre_migration_token_supply` and `post_migration_token_supply`, from which the
sold share is computable — or, at minimum, require `leftover` to equal
`total_supply * (10_000 − issue_bps) / 10_000`. Cost: one more field read and
one `require!`; removes an instruction argument.

---

### B8 — Rent paid by the treasury can be drained to a key, repeatedly

**Not in the review. Verdict: REAL. Severity: Medium-High.**

A second, independent consequence of A1. DLMM's `closePosition2` takes a
writable, otherwise-unconstrained `rentReceiver` (IDL). Under `check_before`,
`rentReceiver` = the attacker's own wallet is a writable **signer** and is
skipped at `venues.rs:93`. Meanwhile the treasury is forced to sign every
`execute` CPI (`execute.rs:80`) and is exempt from the loop
(`venues.rs:90-92`), so it can be named as the `payer` when the position is
opened. `PositionV2` is a multi-kilobyte account, so each open/close cycle moves
roughly 0.05–0.07 SOL of the treasury's native lamports into the attacker's
wallet, and the cycle can be repeated until the treasury is at the rent floor.

This matters beyond the amount: it is a clean, no-caveats counterexample to
"nobody can move value out of the treasury to any key", using only allowed
venues and ordinary instructions. Fixed by the same two-line reorder as A1.

---

### B9 — `collect_seed` and `collect_leftover` have no state gate

**Listed in the brief as "late-arriving seed distorting redemption payouts".
Verdict: REAL. Severity: Medium.**

`collect_seed` requires only `is_tokenized` and that the pool has migrated
(`collect_seed.rs:72-83`); `collect_leftover` likewise
(`retained.rs:97-104`). Neither consults `Vault.state`. So both can land
**after** `finalize_wind_down`, and `finalize_wind_down` does not require either
to have happened — only `pot.amount > 0` (`wind_down.rs:156`).

Two ordering-dependent payouts follow. A seed collected after some holders have
redeemed pays those holders out of a smaller pot and the rest out of a larger
one. A `collect_leftover` after redemption begins increases
`retained.amount`, shrinking the denominator (`redeem.rs:148`) and paying later
redeemers more per token than earlier ones. Neither is theft, and both are
first-come-worse-served rather than exploitable at will — but "pro-rata" is what
the design promises and this is not pro-rata.

**Smallest correct fix.** Require in `finalize_wind_down` that
`pool.creator_migration_fee_withdrawn()` and `pool.is_withdraw_leftover` are
both set. One account read, two `require!`s.

---

### B10 — Gas replenishment: real, but the review has the direction backwards

**Listed in the brief; corresponds to A14. Verdict: PARTLY REAL, mechanism
inverted. Severity: Medium (liveness, not leakage).**

The review frames this as gas top-ups contradicting the tokenized transfer
policy — i.e. as a leak. It is the opposite. `gateway/src/vaults/vault.routes.ts:993-1004`
builds a `SystemProgram.transfer` **from the treasury** to the delegate and
sends it via `sendAsVaultDelegate`, which wraps into `execute` whenever the
vault is tokenized (`vault-wrap.ts:48`). `venues.rs:195-201` then refuses it:

```rust
Some(2) => {
    let to_is_wallets_token_account = accounts.get(1)
        .and_then(token::read_token_account)
        .is_some_and(|t| t.owner == *treasury);
    require!(key_at(0)? == *treasury && to_is_wallets_token_account, VaultError::InstructionNotAllowed);
}
```

A delegate key is not a treasury-owned token account, so `/fund-delegate`
**fails on every tokenized vault**. The policy holds; the operational path does
not. Note also that `condor/vaults_crank.py:328-331` documents this call as *"An
ordinary transfer out of the wallet, signed by the wallet. No vault instruction
is needed or wanted"* — which is not what the route does.

**Smallest correct fix.** Fund the delegate from Condor's own operator wallet,
which is what the crank comment already believes is happening. Zero program
change.

---

### B11 — Malicious trading: real, but this is a trust-model finding

**Claim 1. Verdict: REAL as an economic risk; the *claim about the code* is the
thing that is wrong. Severity: High, and unfixable by the current
architecture.** (≈ A1.)

I can confirm there is no price, slippage, venue-quality, counterparty or
cumulative-loss bound anywhere in `execute` or `venues.rs`. A creator who
controls liquidity in any pool of an allowed venue can make the treasury trade
against it at an arbitrary price; every recipient check in `check_before` and
`check_after` passes, because the value leaves through the *price*, not through
the *destination*.

But I want to be precise about what is broken here, because it is not the code.
`venues.rs:1-21` is entirely candid that the rule is about recipients and
nothing else, and it explains why. What is wrong is one sentence in the
architecture — `docs/CONDOR_VAULTS_ARCHITECTURE.md:336`: *"any of them can at
worst trade badly on an allowed venue"*. "Trade badly" reads as incompetence.
The correct sentence is "can trade at any price they choose, including against
themselves". That is a documentation defect with security consequences, not an
implementation defect, and no patch to `venues.rs` addresses it. See §D.

---

### B12 — Smaller and unconfirmed items

* **`pool_authorities` is caller-extensible.** `venues.rs:75-79` builds the set
  of acceptable token-account owners from *every account in the call that the
  venue program owns*. Venue programs generally tolerate trailing accounts
  (Anchor puts them in `remaining_accounts`), so a caller can append a pool of
  their own creation and thereby whitelist token accounts owned by it. They
  cannot sign for such an account, so I do not claim theft — but they can direct
  treasury assets into accounts nobody can spend from, which is destruction, and
  for any venue whose accounting credits raw reserve balances it would be worse.
  **Verdict: PARTLY REAL, needs a fork test.** Fix: restrict the set to accounts
  the instruction actually names as pool state, or to accounts also present as
  the `position`/`pool` relation — neither is cheap.
* **`install_delegate` works during `WindingDown`** (`install_delegate.rs:36-39`
  gates only on `Redeemable`). Harmless today only because A4 makes the delegate
  powerless in that state. It will stop being harmless the moment A4 is fixed.
* **Strategy-hash overclaims.** `Vault.config_hash` and `agent_ref` are
  commitments with no on-chain validation, and `strategy.rs:4-11` says so
  plainly. **Verdict: WRONG as a vulnerability; it is disclosed design.** The
  only issue is that a reader may take an on-chain hash for an on-chain
  guarantee about behaviour.
* **External programs in the trust base.** Holders trust DBC, DAMM v2, DLMM,
  Raydium CLMM, their upgrade authorities, the quote mint's authorities and the
  vault token's Token-2022 extensions. `architecture:§9` lists "the program
  bytes and the upgrade authority" and omits all of these. **REAL as a
  disclosure gap**, not a bug.
* **Reentrancy / self-invocation: I found nothing.**
  `execute.rs:69` blocks `target_program == crate::ID`, and Solana's runtime
  forbids non-self reentrancy, so venue → `condor_vaults` cannot re-enter
  either. `check_after`'s DLMM offset (`venues.rs:144`, `data[40..72]`) is
  correct — I verified `PositionV2` is `lb_pair(32) ‖ owner(32)` after the
  discriminator. `token::read_token_account`'s `state == 0` rejection
  (`token.rs:61`) correctly refuses uninitialised accounts. The `fresh`
  predicate's `lamports() == 0` requirement (`venues.rs:110`) correctly refuses
  funded wallets. These are the parts of the design that work.

---

## C. What the review missed, in one list

For quick reference, the items above that are **not** in
`CONDOR_VAULTS_ARCHITECTURE_REVIEW.md`:

1. **A5** — the specific unread config bytes, above all `token_update_authority`
   at offset 238 (mint authority) and 231–234 (the permanent-lock percentages).
   A8 raises the topic; it does not name a field or an offset.
2. **B2** — the redemption denominator is unsound because the graduated pool
   keeps trading after `Redeemable`. This is a design error in `redeem`'s stated
   argument, not a missing check.
3. **B3** — `permanent_lock_position` verified reachable through `execute`, with
   its discriminator.
4. **B5** — junk-token griefing permanently blocks `finalize_wind_down`, and is
   the dual of A7's administrator-trust finding.
5. **B8** — repeatable native-SOL drain via `rentReceiver`, a second exploit of
   the same signer exemption as A2.
6. **B9** — `collect_seed` / `collect_leftover` have no state gate, so
   redemption is not pro-rata across time.
7. **B10** — A14 is real but inverted: the gas path is refused, not permitted.

And the corrections to the review's own findings: A4 (redeem) is understated —
the substituted account need not be the retained account; A14's direction is
wrong; A1 should be labelled a trust-model/documentation finding rather than a
code finding, since `venues.rs` never claimed otherwise.

---

## D. The architectural question

> Is this "a managed treasury with restricted withdrawals" or "a protocol that
> resists a malicious trader"?

**The code implements the first, and the documentation sells the second.**

The evidence is `venues.rs` itself, and it is not ambiguous. The file's own
opening (`venues.rs:3-11`) says the rule is *about recipients, because that is
where every custody design that looked at anything else failed*. A recipient
rule is a complete answer to "can the trader take the assets out?" and no answer
at all to "can the trader hand the assets to themselves at a price?" — because
the second question is about value, and nothing in the program observes value.
There is no price, no oracle, no NAV, no exposure limit, no realised-loss
accounting and no notion of a counterparty anywhere in the crate. The design
therefore assumes an **honest-but-restricted** trader: someone who may be
incompetent, may be compromised in the sense of "signs the wrong swap", but who
is not trying to route the treasury's value into a pool they own.

That assumption is defensible. What is not defensible is `architecture:§9`,
which tells a holder their trust base is "the program bytes — specifically
`venues.rs` and `execute.rs` — and the program's upgrade authority", and that
the delegate and crank "can at worst trade badly". A holder who believes that
sentence has mispriced the thing they bought. The creator and the delegate key
are in the trust base, fully, for the entire value of the treasury.

**Minimum additional machinery for the second thing.** Not a patch — a different
program. In rough order of cost:

1. **Bound the asset set.** The treasury may only ever hold the quote asset, its
   own mint, and a short list of majors. This alone kills the "trade into a
   worthless token I control" family, and it is the cheapest real constraint
   available: a list on the `Vault`, checked in `check_after` against the mint
   of every token account the call touched.
2. **Bound the counterparty.** Only pools above a liquidity floor, or only pools
   whose creation predates the vault's. This is where "structural rather than an
   allowlist" (`venues.rs:13-17`) has to be given up; the freedom it buys is
   exactly the freedom to trade against a pool you made this morning.
3. **Bound the price.** An independent mark for each leg at the moment of the
   swap, with a maximum deviation — which means an oracle, which means Pyth or
   Switchboard feeds and a decision about what happens when a feed is stale. For
   assets with no feed, there is no sound answer, so (1) and (3) are the same
   constraint seen twice.
4. **Bound the loss.** A per-epoch NAV-decline limit that pauses the vault. This
   requires the program to be able to value the treasury, which requires (1) and
   (3), and it is the only one of the four that catches slow extraction.
5. **Say who may change 1–4**, and make that not the creator.

Until at least (1) and (3) exist, the accurate description — and the one I would
put in the architecture, the listing page and the Gateway route descriptions —
is: *a managed treasury whose manager cannot withdraw, and who is trusted not to
trade adversarially.* That is a real product with a real guarantee. It is just a
smaller guarantee than the one currently written down.

---

## E. Suggested order of work

1. `venues.rs:93` — reorder the signer branch (fixes A1 and B8). Two lines.
2. `execute.rs:63` — admit `WindingDown` for `execute` only (fixes A4).
3. `redeem.rs:111` — pin or derive the pool vault (fixes A3); better, snapshot
   the denominator at finalize (fixes A3 and B2 together).
4. `tokenize.rs:236` — add the five missing config `require!`s (fixes A5).
5. `income.rs:231` — pin `pool` and `position` (fixes B1).
6. `execute.rs` — forbid `Approve` / `SetAuthority` under `execute_unchecked`
   (fixes A2).
7. A discriminator denylist for the three venues (fixes B3).
8. Then decide §D, and rewrite `architecture:§9` to match whichever answer.

Items 1–3 are small and each closes a path to taking the whole treasury. Items
4–7 are bounded. Item 8 is the one that determines what this is.

# Condor Vaults — plan

**Status:** plan of record, 2026-09-17. **Branch:** `feat/condor-vaults-2`, cut from
`main` at `d89e74f2`. **Supersedes:** the archived `feat/condor-vaults` branch
(`ad23523d`, never merged) and the condor-app prototype
(`~/fengtality/condor-app`, branch `condor-vaults`; its handover is
`docs/plans/CONDOR_VAULTS_HANDOVER.md` there). How this plan got here is in
Appendix A; the questions it settled on the way are in Appendix B.

**In one paragraph.** A Condor Vault is a Swig wallet that trades a strategy
and is owned by a program, not a person. It starts **private**: no token, no
outside holders, and a runner who moves assets in and out freely through the
delegate they installed. That is a finished state, not an unfinished one, and most vaults will spend most of their
life in it — a private vault needs nothing from Condor at all, and a runner can
crank it from their own install. Tokenizing ends it, one way: the vault launches
a Token-2022 mint on a Meteora bonding curve, sells the share of the supply its
runner nominates, and from that moment **nobody can take assets out of it** —
not the runner, not Condor, not anyone — and the only way assets leave is a wind-down
followed by holders redeeming pro-rata. Eighty per cent of what the curve raises
becomes the vault's capital by a rule nobody can skip; twenty per cent becomes
permanently locked liquidity. The unsold supply stays in the vault's own wallet
as a treasury, which the strategy market-makes with. Condor's crank runs the
strategy and, on every position
close, sweeps a share of the realised fees into buying the token and burning it.

**Every guarantee this design makes is a guarantee to somebody who is not the
runner.** That is why they all start at `tokenize`, and why a private vault
carries almost none of them: there is nobody there to protect.

---

## 1. The vault

### 1.1 Two phases, and what each account is for

A vault is **two or three** on-chain accounts, depending on whether it has been
tokenized. Both are addressed by the first:

1. a **Swig wallet** whose **root authority is a PDA of the vault program**. Two
   roles on it: the delegate that trades, and the program itself, which installs
   delegates and moves funds only by rule. The root being a PDA is the whole
   trust model — once tokenized there is no key anywhere that can take assets
   out, and no instruction either;
2. the program's **`Vault` account**, a PDA seeded by the Swig, holding the
   runner, the quote asset, the strategy pin (public agent folder by commit,
   private config by hash), `version`, `fee_bps`, `issue_bps`, and the lifecycle
   state `Running | Paused | WindingDown | Redeemable`;
3. **only once tokenized**, a **Token-2022 mint on a Meteora DBC curve**, created
   by DBC, whose **pool creator is the same PDA**, so every creator-side stream
   is routed by program rule.

**Private is `mint == default`**, not a fourth state. A vault is private exactly
while it has no mint, so the two can never disagree, and a private vault runs,
pauses and winds down like any other. What changes at `tokenize` is:

| | private | tokenized |
|---|---|---|
| who has a claim on the assets | the runner | the runner *and* every holder |
| taking assets out | the runner does it with the delegate they installed, at top level | **impossible for everyone** — no instruction, no key, no path but `redeem` |
| `install_delegate` | the runner alone | the runner **and** `Protocol.administrator` |
| exit | stop, and move the assets out with the delegate | wind down, sweep to the pot, then holders `redeem` |
| who can run it | anyone the runner points at it, including their own Condor | the delegate holder, who is named on chain |

The asymmetry is deliberate and is the answer to "does a user need Condor's
hosted service?" — **no, unless they tokenize**. A private vault is a wallet, a
strategy and a key its runner controls; Condor is a convenient place to run it
and not a party to it.

### 1.2 Roles

| role | who | can | cannot |
|---|---|---|---|
| **runner** | the wallet that created the vault | publish the strategy, set the fee, pause and resume, install or replace the delegate, claim their income, wind down once — and, while the vault is private, move assets out through the delegate they installed | take anything out once the vault is tokenized; touch the Swig's root |
| **administrator** | whoever's delegate key is installed and whose crank ticks the vault. For a **tokenized** vault it must be the key in `Protocol.administrator` — Condor's backend. For a **private** one it is whoever the runner installed, which can be the runner's own Condor | every trade and every sweep while installed; the wind-down's closes, swaps and finalize | change the runner, the strategy, or the root |
| **protocol authority** | Condor's program admin key, one for the whole program | initialize, rotate itself and the administrator key, wind down an abandoned vault | trade, hold a delegate, touch a vault's funds |
| **holder** | anyone with the token | buy and sell on Meteora or Jupiter; redeem after wind-down | anything else |

Administrator and protocol authority are **one keypair in Gateway's wallet
store today**. They are two fields because they part ways before mainnet: the
authority moves to a multisig, the administrator stays hot (§1.7).

### 1.3 Trust, stated plainly

- **Nothing is guaranteed to anyone while a vault is private**, and nothing needs
  to be: the only person with a claim on what is inside is the runner, who can
  take it out whenever they like. Everything below begins at `tokenize`.
- **Meteora's lock** guarantees the market: the migrated liquidity can never be
  withdrawn by anyone.
- **The program** guarantees the seed and the redemption: from `tokenize` onward
  no instruction takes assets out, for anyone, and `redeem` is the only way they
  leave other than by trading. After a wind-down `redeem` pays from an account
  the *program* owns, so a holder is paid by something with no ability to
  refuse — no delegate, no administrator, no key.
- **`issue_bps` bounds the dilution**, and is on chain for that reason. It is
  circulating over max supply at launch; the remainder is a treasury held in the
  vault's own wallet, which is not circulating, does not divide a redemption, and
  enters circulation only when somebody buys it out of an LP position the
  strategy funded — at whatever the market is, with no privileged buyer.
- **Nothing on chain says Condor reviewed anything.** Condor scans a strategy
  folder before its crank runs it and refuses on failure — a private run policy
  in Condor's own database, keyed by vault and version. No key, no flag, no
  badge, no endorsement. Listings show verifiable facts only: commit, config
  hash, version, and the installed administrator.
- **The delegate key is the custody risk while a vault runs — until §1.8.**
  Today it is the only signer that can move funds, unrestricted, until the
  runner replaces it or winds down, and the vault page says so. Swig's
  *magnitude* limits were rejected in rev 13 because they fight LP flows; §1.8
  uses its *destination* limits instead, which do not — a role may move any
  amount into an allowlisted pool and nothing anywhere else. Once that lands,
  the sentence above becomes: the delegate can trade the allowlisted pools and
  cannot transfer.
- **Creation is permissionless, and so is running a private vault.** Anyone can
  create one, install any delegate they like, and never involve Condor. Only a
  **tokenized** vault requires the key in `Protocol.administrator` to co-sign an
  install, and only for one reason: an unregistered delegate is a key the runner
  could hold, and a runner holding the delegate could walk the seed out — which
  is other people's money. A second administrator, if one ever appears, turns
  that field into a registry; that growth is additive.

### 1.4 Token economics

Every number comes from the DBC SDK's formulas and Meteora's docs. **The partner
config is the vault's own, not Condor's** — a vault prices its launch off what it
already holds, so each one creates a config with its own start price. The program
therefore checks a config's *terms*, one by one, rather than its address. That is
stricter in substance and legible on chain, where an address would not be:

| config | value | why |
|---|---|---|
| quote | wrapped SOL | Meteora's keepers and every aggregator handle it |
| migration threshold | **10 SOL**, fixed | the one wrapped-SOL threshold Meteora's mainnet keepers migrate automatically. At any other number the curve fills and nothing happens |
| migration fee | **20–80 %, the runner's choice, 50 % by default**; creator share 100 % | the split between the strategy's capital and the holders' exit depth — see below. Safe to make large because nobody can withdraw it |
| creator trading fee share | **at most 50 %** of the non-protocol fee, on the curve and, through the locked positions, on the migrated pool | runner and Condor are paid the same way by the token's market; a runner may take less, not more |
| curve fee | 100 bps base + dynamic | as the prototype |
| migrated liquidity | partner 50 % + creator 50 %, both permanently locked | a floor nobody can remove |
| migrated pool fee | any of Meteora's **fixed** options (25–600 bps), 100 by default | checked as the *option*, which is what selects the pool's fee config. The customizable option is excluded: on that path the fee lives in a field nothing here enforces, so a vault taking it would be promising a number it could later change |
| token | Token-2022, 6 decimals, 1 B fixed supply, immutable authorities | DBC creates the mint, metadata and authorities |
| pool creator | the vault program's PDA | creator-side streams routed by rule: seed → the Swig; curve fees, surplus, position fees → the runner |
| leftover receiver | **the vault's own wallet** | the unsold supply is an asset the vault owns, in the place its delegate can trade it |
| start price | the vault's, from its NAV | the whole reason to be private first: the launch is priced against assets that already exist |
| issued at launch | `issue_bps`, the runner's choice | circulating over max supply; the rest is treasury, and the number is the ceiling on later dilution |

**The migration fee is a dial, and it is the product decision.** It is the share
of the raise that becomes the vault's capital; the rest becomes permanently
locked liquidity. At 80 % a 10 SOL raise gives the strategy 8 SOL and the pool
about 2 — a large strategy behind a thin market. At 20 % it is the reverse: a
small strategy behind a market holders can actually leave through. Neither is
better, they are different products, and which one a buyer is buying is on the
`Vault` account before they can buy it. The floor of 20 % keeps a market from
being an afterthought; the ceiling of 80 % keeps one from being a rounding error.

A term is a **bound** when it is a decision the runner is entitled to make and a
buyer entitled to read — the migration fee, the creator's trading share, the
migrated pool's fee option — and those are copied onto the `Vault` at `tokenize`.
A term is a **constant** when varying it would break something the design rests
on or quietly move value from holders to somebody else: the 10 SOL threshold, the
migration fee going wholly to the creator, Token-2022 with a fixed supply, Condor
as fee claimer, the leftover receiver being the vault's own wallet.

**Phase A — the curve is open** (reserve < 10 SOL). Buyers' SOL sits in the
pool as the reserve that lets anyone sell back. Each trade pays the curve fee:
Meteora takes 20 %, and the rest splits 40 % of gross to the runner and 40 % to
Condor; the runner claims theirs from the vault page (`claim_income`). The
strategy runs on whatever the runner has put into the Swig. If the curve never
fills, nothing else ever happens.

**Phase B — graduation** (the swap that reaches 10 SOL). Curve trading stops
in that transaction; nothing trades until migration lands. On mainnet Meteora's
keeper migrates; on the fork the crank does. Then, by
`migrationQuoteAmount = threshold × (1 − fee)` — the numbers below are for the
50 % default:

| amount | goes to | how |
|---|---|---|
| **5 SOL** (20–80 %) | the Swig, as seed capital | `collect_seed`, permissionless: CPI `withdraw_migration_fee(creator)` as the PDA into the wallet's quote account; DBC's own one-time flag makes it idempotent |
| **~4.99 SOL** + tokens at the graduation price | the DAMM v2 pool (0.2 % Meteora liquidity fee off the top) | Meteora |
| tokens not needed to match the migrated quote | the leftover receiver, which is the **vault's own wallet** — the treasury | `collect_leftover`, permissionless |
| surplus above 10 SOL on the final swap | 20 % Meteora; then 50 % runner, 50 % Condor | `claim_income`; Condor's partner claim |

**Phase C — running.** Two permanently locked positions, one Condor's and one
the PDA's; the PDA's position fees are the runner's, claimed with
`claim_income`. Holders' exit is the pool, about 2 SOL of depth, the price of
the default 50 % split. The vault runs on the seed and **its own LP fees less the
sweep**.
Sweeps buy the token and burn it. The runner may pause and resume.

**The treasury works.** `collect_leftover` moves the unsold supply out of DBC
into the vault's own wallet — the same wallet the delegate trades from — so the
strategy can market-make its own token: an LP position on its own pool, earning
fees for the vault and deepening the market its holders exit through. Its tokens
appear in Portfolio and its pool in the DEX browser like any other, because that
is what they are. Two consequences follow and both are enforced:

- the wallet's own balance of its own token is **excluded from the circulating
  supply** that a redemption divides by, exactly as the pool's balance is.
  Whether those tokens are idle, half of an LP position, or already sold, they
  were never issued;
- **capital reaches the vault after launch through the strategy, not through an
  instruction.** An LP position funded from the treasury sells unsold supply as
  buyers arrive and takes quote for it, into the vault — continuously, at
  whatever the market is, with the strategy as the counterparty. No new
  instruction, no price oracle, and no privilege the runner has to be trusted
  with. `issue_bps` still caps the whole of it.

**Phase D — wind-down and redemption.** Each vault names a quote asset at
creation (`quote_mint`, SOL first); the wind-down converts everything to it and
redemption pays it. A *private* vault that winds down has nothing to redeem: its
runner has already taken the assets out through the delegate, which is the point
of having stayed private — so its pot is empty and finalize accepts that.

1. The runner, or the protocol authority for an abandoned vault, calls
   `wind_down`. One-way. Strategy ticks stop; `publish_version` and `set_fee`
   are refused from here on.
2. The administrator closes every position (collecting fees, which are swept
   and burned like any other), swaps every non-quote balance to the quote asset
   at Gateway-quoted slippage, and then **sweeps the quote out of the Swig into
   the redemption pot** — a token account owned by the vault's own PDA. That
   last step happens at top level rather than through the program, because Swig
   refuses to be reached by CPI on any execution path (M3, the D3 gate). It is
   the moment the vault's assets stop being reachable by any key at all.
3. The administrator calls `finalize_wind_down`. It refuses while a position or a
   non-quote balance above dust remains, then removes the delegate. The vault's
   *own* token is the one exception and is not a loophole: it is unsold treasury,
   already outside the circulating supply, and nobody has a claim on it —
   requiring it to be sold first would make finishing a wind-down depend on there
   being a bid.
   Nothing trades again. Only the administrator may call it: a permissionless
   finalize could be handed an incomplete account list and strand positions.
4. Holders call `redeem(amount)`: burns `amount`, pays
   `amount / circulating_supply` of the **pot's** balance, where
   `circulating_supply` = supply − the pool's balance − the vault's own, both read
   on chain in the instruction that pays because both move. Dust below the
   finalize floor is the price of a one-asset redemption.
5. No timers. Condor cranks every wind-down to completion; the runner may
   replace the administrator at any time. If both are absent the vault is
   stuck, which is the same failure a running vault would have.

The wind-down is not permissionless: a stranger's close-and-swap signed by the
PDA would need oracle-bounded prices to be safe from a pool priced against the
vault. That is Phase 2 (§7).

### 1.5 What the token is

A **fee-royalty while the vault runs, a pro-rata claim once it is wound down**.
Holders' running return is the burn; the whole buy-and-sell surface is Meteora
and Jupiter, and Condor links out to them rather than building a widget.

- **NAV is meaningful again, and is shown.** Under the born-as-a-token design it
  was not: the launch price had nothing to be priced against, so capital and
  market cap were unrelated numbers and the plan said "the word NAV appears
  nowhere". A vault that is private first has assets *before* it has a price —
  the launch is struck against them, and the runner may strike it above or below
  — so NAV is the thing the token was sold on. It is the vault's capital plus its
  share of the locked liquidity, over the circulating supply, and the page shows
  it beside market cap and, after wind-down, redemption value per token.
- **Outside capital can reach the strategy after graduation**, by one route with
  a cap: an LP position the strategy funds from the treasury, which sells unsold
  supply as buyers arrive and takes their quote into the vault. Bounded by what
  `issue_bps` left unsold. Every other buy is between holders on Meteora and
  touches the vault not at all.
- **`fee_bps` defaults to 50 %** and the page shows trailing burn yield. At pilot
  scale a 10 % sweep is under 1 % a year; the engine exists only with a large
  share or large capital.
- **Capital and income never mix.** Runner income is curve fees, surplus and
  the PDA position's fees. Vault income is its strategy's LP fees less the
  sweep. Neither crosses.
- **A runner's buy on the curve is conviction, not capital.** The migration
  fee's share of it becomes the seed the runner cannot touch, and it crowds a SOL of outsiders out
  of the raise. Capital goes in before tokenizing, by transfer; afterwards it
  arrives only as the market buys the treasury out of an LP position.
- **LP vaults only** in the first cut: the sweep reads `fees_earned_quote`,
  which only LP executors report.
- **Replacing the administrator.** The runner signs `install_delegate` with a
  delegate key minted in the new administrator's Gateway; for a tokenized vault
  the registered administrator co-signs, and for a private one nobody does. The Swig's delegate role changes; funds and positions
  do not move. During wind-down the new administrator needs only the chain;
  while running it also needs the private config, which the runner re-enters
  through the new administrator's interface, the on-chain hash confirming it.

### 1.6 Threat model

| threat | answer |
|---|---|
| a runner installs their own key as delegate and drains the seed | `install_delegate` needs the co-signature of `Protocol.administrator` — for a tokenized vault. A private one has no seed to drain and needs no co-signature |
| a runner takes assets out after other people have bought in | there is no instruction that can, and the delegate route ends when the delegate does. The absence is the guarantee |
| the administrator refuses to pay a redemption | it cannot: `finalize_wind_down` moves the pot into a program-owned account first, and `redeem` pays from there with no Swig, no delegate and no administrator in the path |
| a runner inflates NAV with worthless assets, then sells treasury into it | bounded by `1 − issue_bps`, which is on chain before anyone buys. There is no instruction that prices treasury against NAV, so the inflation would have to fool the *market* into buying an LP position out — not fool a number |
| a runner tokenizes on a config with better terms for themselves | every economic term is checked at `tokenize`: quote asset, the migration fee within 20-80 and wholly to the creator, the creator trading share at most 50 %, a fixed migrated-pool fee option, the 10 SOL threshold, Token-2022, fixed supply, Condor as fee claimer, and the leftover receiver being the vault's own wallet |
| the treasury is sold or LPed by the delegate at a bad price | it is bounded by `issue_bps` and the proceeds land in the vault, so circulating supply and vault capital move together. Selling below NAV dilutes, which is why the cap is on chain before anyone buys |
| a stranger finalizes a wind-down with an incomplete account list, stranding positions | `finalize_wind_down` is administrator-signed |
| the administrator's delegate key leaks | it can move everything in the vaults it is installed on; one key per Swig bounds each leak to one vault; the store is the target — stated on the page |
| the protocol authority's key leaks | it can wind down every vault (forced liquidation, no theft) and rotate the administrator; rotatable to a multisig without redeploy, and required to be one before mainnet (§1.7) |
| the program upgrade key leaks | rewrites the program; on the same multisig before mainnet |
| vault spam | costs rent for a Swig, a mint and a pool; Condor administers only vaults whose runner obtained Condor's delegate, which only Condor's users can |
| spam on `collect_seed` | DBC's one-time flag; the caller pays gas for nothing |
| look-alike vaults | no name registry: a vault's identity is its Swig address; every card shows the address and the administrator, never a name alone |
| a fake strategy pin | nothing on chain validates `agent_ref`; Condor's scan gates what Condor runs |
| MEV on sweeps | predictable and small; Gateway-quoted slippage bounds and the dust floor; a burn that buys slightly high still burns |
| a runner wash-trading their own strategy to inflate fees | pays real fees to do it; the burn it triggers costs more than it earns |
| gaming redemption through the pool | buying from the pool raises the circulating supply but the buyer holds the tokens; selling does the reverse; arbitrage keeps pool price and redemption value aligned |
| SIWS replay | nonce with a short TTL |
| **Swig upgrades and changes its account layout or wire format** | unmitigated, and systemic: the Swig program is upgradeable (mainnet upgrade authority `8o2ZThbZ5Bky4RcPVBYjyWuzVtqfwfqPMbsboTkFf3aQ`), every vault reaches its funds through it, and a deployed `.so` has its assumptions baked in — so tracking Swig's own crates would not help at runtime either. What is owed here is monitoring, a behavioural fork test that uses the program rather than agreeing with a struct, and the honest statement that Condor vaults inherit a third party's upgrade authority |

Program rules that follow: derive every Swig token account from the funds-owner
and the mint, never trust a caller-supplied one; `claim_income` pays the runner
recorded in the account; `redeem` reads supply and the pool's token balance in
the instruction that pays.

### 1.7 Multisig-ready

A Squads multisig is a public key that signs by CPI, and Anchor's signer check
accepts a CPI signer, so any authority field can be handed to one later with a
single rotation. Three rules keep that true:

1. **Every authority-held field is rotatable**: `set_authority` and
   `set_administrator` exist from day one.
2. **Authority instructions take a separate `payer` and never read the
   authority's lamports.** A multisig executes through its own payer.
3. **Nothing time-sensitive needs the authority.** Its powers are rotate a key,
   wind down an abandoned vault. Everything the crank does every tick is paid
   by the hot key.

### 1.8 After tokenize: a role that can trade and cannot transfer

> **Corrected the same day it was written.** The Swig facts below hold, but the
> conclusion drawn from them does not: destination-scoped Swig actions constrain
> where the vault's assets *go*, and every DEX instruction lets the caller name
> where the *proceeds* go. Verified against Meteora's source
> (`programs/lb_clmm/src/instructions/swap.rs`): `user_token_out` is constrained
> by **mint only** — never by owner — so a swap of the vault's USDC into a pool
> with `user_token_out` set to the manager's own account is a withdrawal that
> every destination limit permits. `initialize_position` takes `owner: Signer`,
> which the manager's key satisfies while the vault funds it. Swig cannot see
> either, and the program cannot sit in the Swig's signing path (no CPI). So the
> role described here is **necessary and not sufficient**; the sufficient design
> is §1.9, which moves the tokenized phase off the Swig entirely. §1.8 is kept
> for the Swig analysis, which §1.9 relies on, and for the record of why.

**The problem.** The delegate role is `Permission::AllButManageAuthority` — sign
anything, never edit the role table. Trade and withdraw are therefore one
power, and that single fact shapes everything downstream: the administrator
must co-sign every install on a tokenized vault, the runner is locked out of
signing for their own strategy, and "the manager can't withdraw" is a statement
about *which key* holds the role, not about what the role can do. Holders need
the second kind of statement. Redemption has teeth only if nothing between
`tokenize` and `redeem` can move the assets to a wallet.

**What Swig actually enforces** (read from `program/src/actions/sign_v2.rs` and
`state/src/action/*.rs`, `anagrambuild/swig-wallet`, main, 2026-09-18):

- **Default deny.** Unless the role holds `All` or `AllButManageAuthority` —
  which skip every check — each inner instruction that uses the wallet as
  signer must be covered by a program permission, and every net outflow the
  transaction causes must be covered by a spend permission. No match →
  `PermissionDeniedMissingPermission`.
- **Program permissions** gate which program may be invoked: `ProgramAll`
  (anything; "highly privileged"), `ProgramCurated` (a list hard-coded in the
  Swig program: **System, SPL Token, Token-2022, Stake — and nothing else**), or
  `Program(program_id)`, repeatable, one per allowed program. The check is on
  the instruction's program id; it does not look inside that program's CPIs.
- **Spend is measured as net outflow per transaction**, after execution:
  lamports on the wallet address, and per mint on the wallet's token accounts.
  Outflow must be covered by `SolLimit`/`SolRecurringLimit` or
  `SolDestinationLimit(destination, amount)`, and `TokenLimit(mint)`/
  `TokenRecurringLimit` or `TokenDestinationLimit(mint, destination, amount)`.
  Inflow is never checked. Destination limits are repeatable, key on one exact
  destination account, and carry a `u64` that decrements — `u64::MAX` means
  "unlimited to this one place".
- **Editing a role's actions needs `ManageAuthority`**, which only the root
  holds. The root is this program's PDA (§1.1), so *the program* is what adds
  and removes permissions, under whatever rule it chooses to enforce.

**The consequence.** "Trade, but never send to a wallet" is expressible, and
the primitive is the destination limit, not the magnitude cap. A `TokenLimit`
would be leaky: `Program(Token)` comes free with `ProgramCurated`, so a role
with `TokenLimit(USDC, cap)` may `transfer` USDC to its own key's ATA, up to the
cap, and a recurring cap bounds the *rate* of theft rather than the fact of it.
A `TokenDestinationLimit(USDC, <pool vault>)` cannot be spent anywhere but that
vault. That is the difference between a speed bump and a lock.

**The role, concretely.** From `tokenize` onward, the trading role holds:

| action | value | why |
|---|---|---|
| `ProgramCurated` | — | System, Token, Token-2022: ATA creation, wrapping, the transfers every DEX instruction is made of |
| `Program(p)` per allowed DEX | Meteora DLMM `LBUZ…wxo`, Meteora DAMM v2 `cpamd…sGG`, Raydium CLMM `CAMM…rWqK`; Raydium CPMM and Pump AMM once their ids are confirmed against Gateway's connector constants (the first three are) | the venues the strategy may touch, and no others — **no router** (below) |
| `TokenDestinationLimit(mint, vault, u64::MAX)` × 2 per allowlisted pool | the pool's own reserve/vault token account for each side | any amount into this pool; nothing into any other account |
| `SolDestinationLimit(own wSOL ATA, u64::MAX)` | the wallet's own wrapped-SOL account | wrapping is a SOL transfer to yourself, and a wSOL-quoted pool needs it |
| `SolRecurringLimit(small, window)` | e.g. 0.1 SOL per day, in slots | rent for position accounts, bin arrays and new ATAs, which are created at addresses nobody knows in advance. Also the entire amount of SOL the role could ever leak per window — bounded, and shown on the vault page |
| — | **no** `TokenLimit`, `SolLimit`, `ProgramAll`, `All` | each of those is the hole |

What each flow does under it: a **swap on a pool** sends the input mint to that
pool's vault (covered) and receives the output (unchecked). **Adding LP** sends
both mints to the pool's vaults (covered) and pays rent (SOL budget).
**Removing LP / claiming fees** is inflow. **Wrapping SOL** is a transfer to the
wallet's own ATA (covered). **Transferring to any wallet** — System or Token,
any amount, including the manager's own — hits no matching action and fails.
That last line is the guarantee holders are owed, and it holds against the
administrator's key exactly as it holds against the manager's.

**Routers, and why v1 is pools only.** Jupiter, DFlow and Titan route through
pools chosen at quote time, so the transaction's outflows land in vault
accounts nobody can list in advance; a destination-scoped role cannot cover
them. `Program(Jupiter)` alone does not help — the spend check is on where the
money went, not on who was called. The only way to admit a router is a
`TokenLimit` per mint, which reopens the transfer hole up to the cap. So **v1
swaps directly with allowlisted pools** on the DEX programs above, which is the
same set of pools the strategy LPs into. If a router is wanted later, the
honest form is a *small recurring* `TokenLimit` presented to holders as exactly
what it is: the most the strategy could lose to a bad route — or to a bad
manager — per window.

**Which pools, and who decides.** Destination limits are per pool, so a pool
has to be admitted before the role can touch it, and that admission is the one
remaining place a manager could do harm: a fresh pool with mints the vault
already holds, priced by the manager, drained by the vault swapping into it.
Two rules, one instruction:

1. At `tokenize` the vault declares its **asset universe** — the mints the
   strategy may hold, recorded on the `Vault` beside `quote_mint`, read by
   holders before they buy. The vault's own mint is in it (market-making the
   treasury against its own pool is a stated feature, §1.4).
2. `allow_pool(pool)` reads the pool account, requires its program to be an
   allowed DEX and both mints to be in the universe, and then — as Swig root —
   adds the two `TokenDestinationLimit`s. The vault's own migrated DAMM v2
   pool is admitted by `tokenize` itself; its address is derived.

**v1 makes `allow_pool` administrator-signed.** That is curation, and it is
trust — but it is far less trust than today, when the administrator holds an
unrestricted key over the wallet. The administrator can add a pool; it still
cannot move a token anywhere but into one. **v2** makes it permissionless with
a canonical-pool rule (the deepest existing pool per pair on each program),
which removes the administrator from the trading path entirely. Either way the
own-token rug is closed mechanically: a mint outside the universe has no pool
the role can reach.

**Pool creation** is not something the vault role does in v1. A pool's vaults
do not exist until it is created, so nothing can be allowlisted ahead of the
transaction, and creating pools is precisely the rug vector. The runner creates
a pool with their own wallet, like anyone; the vault LPs into it once admitted.
(The launch itself is unaffected: the DBC curve and the migrated pool are
created by the program's own flow.)

**The manager's own key.** Because the program now guarantees the *shape* of
the role rather than the identity of the key, post-tokenize `install_delegate`
installs this scoped role and needs no co-signature. A runner may install their
own key and sign their own trades on a tokenized vault; Condor's crank gets an
identical role for the agent. The administrator's co-signature on install —
rev 15, "a runner holding the delegate could walk the seed out" — is retired,
because the delegate can no longer walk anything out. The administrator keeps
two jobs: `allow_pool` (v1) and `finalize_wind_down`.

**Costs, measured before building.** Each destination action is ~80 bytes on
the Swig account; twenty pools × two sides is ~3 KB of rent the runner pays at
`allow_pool` time (Swig reallocs; confirm its ceiling). `sign_v2` scans actions
linearly, so compute grows with the allowlist — measure a 40-action role on the
fork. Recurring windows are in **slots**, and Swig's docs warn slot time is
moving from 400 ms to 200 ms; the rent budget window has to be re-scaled with
it. `u64::MAX` limits never need replenishing.

**What changes.** `swig.rs` grows four action encodings — `Program`,
`TokenDestinationLimit`, `SolDestinationLimit`, `SolRecurringLimit` — with
layouts taken from `state/src/action/*.rs` at the pinned commit, not
reconstructed (discriminants: Program 3, SolLimit 1, SolRecurringLimit 2,
SolDestinationLimit 16, TokenDestinationLimit 18). `install_delegate` branches
on `is_tokenized()` and builds the scoped role; `tokenize` records the universe
and admits the migrated pool; `allow_pool` is new; Gateway gains
`build-allow-pool`; the vault page lists the universe and the admitted pools,
because both are what a holder is now trusting instead of a key.

### 1.9 The program is the wallet

**Decision (rev 19).** The vault's authority PDA is the wallet for the vault's
whole life. Swig is removed. The two phases keep their different promises
(§1.3) and now differ by one check on two instructions, not by mechanism.

**Why.** §1.8 established that "trade freely, transfer never" needs a check on
the *recipient accounts* of each instruction, and that only the signer's own
program can make it non-bypassably — which the Swig, refusing CPI, cannot let
this program be. That decided the tokenized phase. The private phase followed
for the reason the plan has hit twice before (revs 2 and 11): the program
already holds the only two roles a vault has, `runner` and `delegate`, and
Swig's role table was a second copy of them, paid for with a hand-rolled wire
format, two CPIs in every create, a CPI-refusal that forced the redemption pot,
and — for §1.9 as first written — an asset migration at launch that would have
had to close every open DLMM position. With the PDA owning everything from
creation, `tokenize` moves nothing.

**Accounts.** `Vault` at `["vault", id]` — the vault's public identity — and
the wallet at `["vault_authority", id]`, a system account with no data that
holds the SOL and owns every token account and position. `id` is the 32 random
bytes chosen at creation and is stored on the `Vault` so every instruction can
re-derive the wallet's seeds. `swig_account` and `funds_owner` are gone.

**Instructions.**

| | signer | phase | what |
|---|---|---|---|
| `create_vault(id, fund)` | runner | — | the two PDAs, funded. One account, no CPI |
| `install_delegate(key)` | runner | not Redeemable | sets the field. **No co-signature, ever**: what a delegate may do is decided by the two instructions below, not by whose key it is |
| `pin`, `publish_version`, `set_active` | runner | as before | unchanged |
| `execute_unchecked(data, accounts…)` | runner or delegate | **private only** | invoke any program as the wallet. This *is* `AllButManageAuthority`: the runner's own money, no guarantees, exactly as §1.3 says. Refused from `tokenize` on. Withdrawing is a transfer through it |
| `execute(data, accounts…)` | runner or delegate | any; **mandatory after `tokenize`** | invoke an allowed venue as the wallet, under the recipient rules below |
| `tokenize` | runner | private → tokenized | as before, minus any asset move; writes `quote_mint` from the config |
| `collect_seed`, `collect_leftover`, `claim_income`, `claim_position_fee` | as before | tokenized | unchanged; they already signed as the authority |
| `wind_down` | runner or protocol authority | tokenized | unchanged |
| `finalize_wind_down` | administrator | WindingDown | every non-quote balance of the wallet ≤ dust, and the wallet's quote account non-empty; clears the delegate. **No sweep, no pot**: the wallet's quote account is what `redeem` pays from |
| `redeem` | any holder | Redeemable | unchanged — it always paid from the authority's quote account; that account is now simply the wallet's |

Gone: `sweep-to-pot`, the pot as a separate account, the co-signed install and
its optional `protocol`/`administrator` accounts, `swig.rs`, four Swig error
codes, `NoRootRole`.

**`execute`'s recipient rules.** With `p` the target program:

1. `p` is an allowed venue — Meteora DLMM, Meteora DAMM v2, Raydium CLMM in
   v1 — or one of System, Token, Token-2022, Associated Token, in which case
   the narrower rules in 3 apply. Never this program.
2. Every account passed as **writable** is one of:
   a. the wallet itself;
   b. a signer other than the wallet — the caller, paying rent or fees with
      their own key;
   c. a token account whose owner is the wallet;
   d. a token account whose owner is `p`'s own pool authority, derived the way
      `p` derives it (DLMM: the `lb_pair` owns its reserves; DAMM v2: the fixed
      pool-authority PDA; Raydium CLMM: the pool state);
   e. an account owned by `p` (pool state, bin arrays, oracles, positions —
      the venue keeps its own invariants on these);
   f. an uninitialised system account (zero data, zero lamports): a position or
      token account about to be created, which the venue will initialise with
      the owner it is told to — and 2g is what constrains that owner;
   g. for venues whose position owner is an *account*, not data (DLMM's
      `initialize_position`), that account must be the wallet.
3. For the four base programs: `Token`/`Token-2022` only `SyncNative` and
   `CloseAccount` on a wallet-owned account whose rent destination is the
   wallet; `Associated Token` only `create` with the wallet as owner;
   `System` only `transfer` from the wallet to a wallet-owned token account
   (wrapping) and `create_account` with the caller as payer.
4. Anything else is refused with the account that failed named in the error.

Rule 2 is structural, not an allowlist, and that is where the manager's freedom
comes from: a pool created an hour ago passes because its reserves are owned by
the venue's own authority, and a swap whose output account is anyone's but the
wallet's fails 2c. What a holder trusts is the recognition table in rule 2d —
a few lines per venue, pinned to that program's layout, extended by upgrade —
and that is small enough to read.

**Gateway.** One signing mode, `sendAsVaultDelegate`, replaces
`sendAsSwigDelegate`: a connector builds for the wallet address as it always
did, and the mode wraps the inner instructions in `execute_unchecked` (private)
or `execute` (tokenized), signed by the delegate key it holds, exactly where it
used to wrap them in Swig's `sign`. The delegate store is keyed by the vault
account. Everything else in Gateway that said `swigAccount` says
`vaultAccount`; everything that read `fundsOwner` derives the wallet.

**Condor and the page.** Nothing changes in what a runner does. Withdraw is the
delegate moving what it can, as before, now through `execute_unchecked`.
Tokenize is a launch, not a migration. The vault page's two addresses are still
two — the vault account and its wallet — and the wallet is the one you fund.

**What does not change.** Self-hosting: a runner installs any key as delegate
and never involves Condor; that was always a program instruction. CPI depth:
transaction → this program → venue → Token, the same three levels Swig used.
hbapi: a wallet is a pubkey and the PDA is one.

**What is still true, and never stops being.** The price rug — trading badly
on purpose into a thin pool the manager seeded — passes every recipient rule,
because the vault does receive what it paid for. It is bounded, not prevented:
the asset universe fixed at launch and a canonical-pool or per-window rule on
non-canonical pools (§1.8). Holders read it as what it is.

**Before the first mainnet vault:** the protocol authority *and the program
upgrade authority* move to a Squads vault. The administrator key stays hot; its
powers are bounded by needing the runner's signature for an install and the
`WindingDown` state for a finalize.

---

## 2. Decisions

| # | decision | why |
|---|---|---|
| D1 | Anchor v1 (`anchor-lang` 1.0.2); the QuickNode reference is `finance/vault-strategy/anchor-v1` | The prototype program is Anchor 1.0.2 and Gateway's client is `@coral-xyz/anchor ^0.30.1`; the v2 copy is a release candidate. |
| D31 | **Swig's and Meteora's wire formats are hand-written in this program**, not taken as dependencies | Swig publishes Rust crates — `swig-sdk`, `swig-interface`, `swig-state`, `swig-compact-instructions`, `swig-cli`, at `anagrambuild/swig-wallet` v3.0.0, all AGPL-3.0 and git-only in practice. Three findings, all tested: (1) **`cargo add swig-sdk` gets somebody else's crate** — crates.io's `swig-sdk` 0.3.2 is MIT, by a different publisher, and its stated repo `bitrouter/swig-sdk` 404s; Swig's own is the git one; (2) `swig-interface`, which has these exact builders, **cannot compile for SBF** (`solana-sdk` → `getrandom`, no SBF backend), and `swig-sdk` depends on `solana-client` — both are client-side; (3) `swig-state` and `swig-compact-instructions` *are* pinocchio-based and **do** build for SBF, and would delete the role-table reader and the compact-instruction encoder — but they are AGPL-3.0 and Condor is MIT, so linking either relicenses the program. **Settled: keep the hand-written encoders, and stay MIT.** Measured on this binary: `swig-state`'s role reader took the `.so` from 499,024 to 537,960 bytes — **+38,936 for one function**, almost all of it `libsecp256k1`, pulled in for Secp256k1 authority types this program never uses. (`swig-compact-instructions` alone was +696, so the cost is entirely `swig-state`.) Taking them also means Anchor *and* pinocchio in one binary, pinocchio at two versions; dropping Anchor instead would cost the IDL Gateway builds every instruction from and the account constraints that are load-bearing security. And the headline benefit is weaker than it looks: a deployed `.so` bakes its dependencies in, so a breaking Swig upgrade costs the same either way and the crate only shortens the eventual fix. The mitigations that are worth more than the swap are a behavioural fork test (create a Swig, add a role, assert the role id this program finds is the one Swig's own `sign` accepts) and, if a cross-check is ever wanted, using the AGPL crates as an oracle in a harness outside the program's dependency graph. Meanwhile the hand-written layouts are checked against *both* of Swig's own implementations: byte-for-byte against the TS SDK in a test, and against `swig-state`'s own `Position` (16 bytes; type/len at 0, 2; id at 8; boundary at 12) and `Swig` header (48 bytes, `roles: u16` at 34) by reading the source. |
| D2 | Gateway holds no user key: delegate keys and the platform key only | It is a dapp. |
| D3 | The Swig is the agent wallet and the vault program's PDA is its root | The crank needs a key Gateway holds that acts without the user's, and nobody may withdraw. **Gate run 2026-09-17 and split:** a PDA *can* be the root and *can* manage authorities by CPI (both proven on the fork); it **cannot** make the Swig sign, because `sign_v2` requires stack height 1. `withdraw` was deleted (the delegate already does it) and `redeem` now pays from a program-owned pot the administrator sweeps into before finalize — see M3 and D32. |
| D4 | The Swig wallet *is* the vault: one record, `Vault` PDA seeded by the Swig | One account per piece to reconcile; no slug, no runner registry. |
| D5 | **One create flow, one signature**: the Swig, its delegate and the strategy pin are three instructions in one transaction; no standalone agent wallet | There is no useful moment between them — a vault with no delegate cannot trade and one with no strategy has nothing to trade. Three signatures needed a stage ladder, three confirm routes and a resumable stepper to survive a closed tab, which was machinery for a problem that only existed because there were three. Superseded the draft-and-continue design; a record whose transaction never landed is now discarded, not resumed. |
| D23 | **A vault is private until it is tokenized, and tokenizing is one way** | Most vaults never need a token. While one has no outside holders, locking its runner out of their own capital makes nothing safer and makes the product unusable for its commonest case. Every guarantee the program makes is a guarantee to somebody who is not the runner, so all of them start where such people do. |
| D24 | **There is no `withdraw` instruction at all** — but there *is* a withdraw route, and it is the delegate | While a vault is private its runner already controls the delegate, which can move anything in the wallet at top level; an instruction would have been a second way to do what one key already does. The route (`vaults/withdraw`, refused once tokenized) is what makes that true in the product rather than only on the chain: a private vault nobody can take money out of is not the design, it is a missing button, and a live run is what found it missing. Once tokenized there is no path out but `redeem` — and the seed's untouchability is that absence, not a check inside something. |
| D25 | The launch config is checked by its **terms**, not its address | A vault prices its launch off the assets it already holds, so each needs its own config and there is no address to recognise. Checking the terms is stricter, and it puts the economics on chain where a holder can read them. |
| D26 | `issue_bps` on chain: circulating over max supply at launch | It is the ceiling on how far a holder can later be diluted, so it is committed before anyone buys rather than described in a listing. |
| D32 | **The wind-down sweeps into a program-owned pot, and `redeem` pays from there** | Forced by the D3 gate: Swig refuses CPI on every execution path, so the program cannot pay out of the Swig however it is written. `ProgramExec` (Swig's supported pattern for program-authorised signing) was the alternative and was rejected — it verifies only that *an* instruction of ours ran, not what the paired Swig sign then does, so a permissionless `redeem` could be paired with a sign that drains the wallet; closing that would need us to parse Swig's wire format in the hot path. The sweep is simpler and ends stronger: after finalize the pot is program-owned, so redemption needs no Swig, no delegate and no administrator. Proven end to end on the fork. |
| D34 | **A private vault passes no protocol account.** `pin` never read one; `install_delegate` and `wind_down` take it as optional and read it only for the tokenized co-signature and the abandoned-vault path | D29 says a private vault needs nothing from Condor, and three instructions quietly disagreed: each required `Protocol` to exist *and deserialize* on that chain. Found by creating a vault on the fork, where the account is a stale layout — every private create failed at `AccountDidNotDeserialize`, on an account none of them had a use for. |
| D33 | **Condor encodes no Solana instructions.** The sweep's burn and the delegate top-up are Gateway routes (`vaults/burn`, `vaults/fund-delegate`); Condor decides the amounts | It had grown a second implementation of Solana's primitives in Python — PDA derivation, an ed25519 on-curve test, ATA derivation, SPL `Burn` and System `Transfer` encoders — about 120 lines, in the one path that spends a holder's assets. Gateway does all of it already, from the IDL and `@solana/web3.js`, and knows the mint's decimals rather than assuming six. Policy stayed where D11 put it: the ledger, the share and the minimum are still Condor's. |
| D30 | **The migration fee is the runner's, 20–80 %, default 50 %**; the creator's trading share and the migrated pool's fee option are likewise bounded rather than fixed. All three are recorded on the `Vault` | The migration fee *is* the split between the strategy's capital and the holders' exit depth, and a large strategy behind a thin market and a small one behind a deep market are different products rather than a right and a wrong answer. Bounded, not free: below 20 % the market is an afterthought, above 80 % it is a rounding error. What stays constant is what would break the design (the 10 SOL threshold) or move value quietly (the creator's 100 % share of the fee, the fee claimer, the leftover receiver). |
| D27 | The treasury lives in the **vault's wallet**, not a pot of its own | It is an asset the vault owns. In the wallet, the delegate can market-make the vault's own token; in a pot, it could only ever shrink. Excluded from the circulating supply either way. |
| D28 | **No instruction issues treasury supply.** Capital reaches a tokenized vault through an LP position funded from the treasury | A second DBC sale was considered and dropped (DBC creates its own mint, so it would have been a second token), then an `inject` that sold treasury to the runner at the pool price — also dropped. The LP position does the same thing better: supply enters circulation as buyers arrive, the quote lands in the vault, the counterparty is the market rather than the runner, and there is no price for anyone to assert. It deleted an instruction, a Q64 price reader, and a privilege. |
| D29 | A private vault needs no administrator co-signature and no Condor | Self-hosting is the default for the phase where there is nobody to protect. |
| D6 | Token-2022 at tokenize, DAMM v2 migration | DBC does the whole mint; every new DBC config migrates to DAMM v2. |
| D7 | The vault's NAV prices the launch; first buy is optional | Its own config's start price. `firstBuySol ≥ 0` already. |
| D8 | The migration fee goes wholly to the creator, and the creator is the PDA (the *size* of that fee became the runner's, 20-80, in D30) | A seed nobody can take can be large; the PDA as creator lets the program route every creator-side stream. |
| D9 | 10 SOL threshold | The only wrapped-SOL threshold Meteora's keepers migrate. |
| D10 | `fee_bps` on the pin, default 50 %, changed by `set_fee` | The tick reads it from chain; it is the holders' entire running return. |
| D11 | The sweep is crank code in `condor/`, not a tool and not a prompt rule; it polls terminated executors rather than taking a callback, and burns in a second transaction | Automatic and verifiable, with no MCP surface for spending funds. Polled because the ledger is keyed by executor id, so re-reading is free while a missed callback is lost forever — and because the agent engine has no other reason to know vaults exist. Two transactions are safe here: tokens bought and not yet burned are treasury, already outside the circulating supply, so a failed burn is deferred rather than lost. |
| D12 | Every runner-signed transaction is a Gateway build signed in the browser | Gateway's build mode already does this; a minimal Wallet Standard signer ships in Phase 1. |
| D13 | Wallet attach is SIWS, once, on `user_id`; one attached wallet per user | Private-config reads are gated by proof of the wallet. |
| D14 | **Condor reaches Gateway through hummingbot-api's passthrough** (`/gateway/proxy/{path}`), never on its own connection; there is no `gateway_url` setting | hbapi has no typed method for the routes the vaults need and should not grow twenty vault-specific ones — but that argues against *those endpoints*, not for a second address. A direct client meant two settings for one Gateway with nothing keeping them in step, and the drift is silent in the worst way: Condor would build against one node while every executor traded on another, and the first symptom would be a delegate key that "does not exist". It cannot even be fixed by copying one setting into the other — an hbapi in a container reaches its Gateway at `host.docker.internal`, which does not resolve from Condor's host. Through hbapi there is one address, the server's own, and the question cannot be asked twice. Upstream: one passthrough route plus a public `request` on its Gateway client. It also settles D20's mTLS, since hbapi holds the certificates. |
| D15 | The RPC is Gateway's; the browser is told the host, never the URL | Two answers about one chain is how the wrong one wins; the URL may carry a key. |
| D16 | Surfpool is detected by `getVersion` (`surfnet-version` present) | Verified against the fork and a QuickNode endpoint. |
| D17 | Creation is permissionless; no attestation on chain | An on-chain "Condor reviewed this" is an endorsement and a liability. The scan is Condor's private run gate. |
| D18 | Wind-down is one-way; conversion to the per-vault quote asset; redemption pays it only; no timers | §1.4 Phase D. |
| D19 | One hummingbot-api account per vault, bound to its wallet — the one upstream hbapi change | hbapi's Gateway connector uses Gateway's default wallet, so two vaults could not trade at once. |
| D20 | Work stays on the `vaults` server (hbapi `:8001`, Gateway `:15889`, surfpool `:8899`) | One stack, one Gateway. mTLS is no longer a Phase 2 item for Condor: every Gateway call goes through hbapi, which already holds the certificates (D14). |
| D21 | `Protocol.administrator` co-signs every `install_delegate`; `finalize_wind_down` is administrator-signed | Without the co-sign a runner installs their own key and drains the seed; without the signer restriction a stranger strands positions. |
| D22 | One platform key now; `set_authority` / `set_administrator`; authority instructions take a separate payer | Multisig-ready without redeploy (§1.7). Two keys in one wallet store protect nothing. |

---

## 3. Where each piece lands

```
programs/        condor_vaults (Anchor v1); build.sh writes the IDL, which Gateway vendors
Gateway (TS)     Swig routes (exist), launch routes (exist), vault routes (program-instruction builds; finalize/collect/migrate executes), delegate keys + the platform key, submit/poll
condor/ (py)     Gateway client, SIWS attach, one vault store, private configs + canonical hash, scan, crank, sweep, routes
frontend (React) …and a Vaults section in Portfolio, a Vaults tab in the DEX pool browser
hummingbot-api   one change, upstream PR: a per-account Gateway wallet passed as `address` to the connector (D19)
frontend (React) wallet connect + sign + submit; Vaults tab, Create Vault, vault page; Settings: chain badge
```

Who signs what:

- **the runner's wallet:** the create flow (create the vault under the PDA,
  install the delegate, pin), `tokenize`,
  `publish_version`, `set_fee`, `set_active`, `install_delegate`,
  `claim_income`, `claim_position_fee`, `wind_down`, and the per-vault launch
  config
- **the program's PDA, by CPI only:** Swig root operations, seed collection, fee
  routing, redemption transfers
- **the platform key, as administrator:** co-signs `install_delegate` on a
  *tokenized* vault, signs `finalize_wind_down`; its per-vault delegates sign
  trades, sweeps, treasury LP positions, and the wind-down's closes and swaps
- **the platform key, as protocol authority:** `initialize`, `set_authority`,
  `set_administrator`, `wind_down` on an abandoned vault; pays for
  `collect_seed` and fork migration
- **any holder:** `redeem`
- **anyone:** `collect_seed`, `collect_leftover`

Existing Condor files touched, and only these:

| file | change |
|---|---|
| `frontend/src/components/layout/AppShell.tsx` | a Connect Wallet control in the header |
| `frontend/src/lib/nav.ts` | `{ to: "/vaults", icon: Vault, label: "Vaults" }` between Routines and Settings |
| `frontend/src/App.tsx` | routes `/vaults`, `/vaults/new`, `/vaults/:account`; wallet provider around the shell |
| `frontend/src/components/layout/AppShell.nav.test.ts` | Vaults sits after Routines and before Settings |
| `frontend/src/components/settings/GatewaySettings.tsx` | chain badge in the status card |
| `frontend/src/lib/api.ts` | the new client calls |
| `frontend/package.json` | `@solana/web3.js`, `@wallet-standard/app`, `@solana/wallet-standard-features` |
| `condor/web/app.py` | `include_router` for `wallet`, `vaults` |
| `condor/web/models.py` | request/response models |
| *(none)* | the sweep polls terminated executors from the crank instead of hooking the engine — the ledger makes re-reading free, and the engine has no other reason to know vaults exist |
| *(none)* | no Gateway setting of any kind: a server's Gateway is whichever one its hummingbot-api talks to (D14) |

hummingbot-api (PR against `hummingbot/hummingbot-api` `main`; the vaults stack
builds its image from the branch until merged):

| file | change |
|---|---|
| `routers/gateway.py` | `ANY /gateway/proxy/{path}` — forward one request to this API's Gateway, unaltered, so a caller needing a route hbapi has no method for does not open its own connection (D14) |
| `services/gateway_client.py` | a public `request(method, path, …)`, so the passthrough need not reach into a private one |
| `services/accounts_service.py` | per-account `gateway_wallets.json` `{chain: address}`; get/set |
| `routers/accounts.py` | `GET/POST /accounts/{name}/gateway-wallet` |
| `services/unified_connector_service.py` | `_create_trading_connector` passes `address=` when the account has one |
| `hummingbot-api-client` | the two client methods; Condor bumps its pin |

Everything else is new. The archived branch's `tools/vault.py` is a reference
for the fee math and the ledger's idempotency, not a file to port: the MCP
server is untouched.

---

## 4. The record

`condor/vault_store.py`, per user, `paths.user_dir(user_id)/vaults.json`, keyed
by the Swig account address:

```
{
  "<swig_account>": {
    "pending":        { ... } | null,                  # what the create transaction stages, until `confirm`
    "label":          "Momentum LP",
    "server":         "vaults",
    "network":        "mainnet-beta",
    "swig_id":        "<hex>",
    "wallet_address": "<funds-owner>",                 # trades and sweeps; Gateway wraps it for the delegate
    "runner_address": "<the user's attached wallet>",
    "delegate":       { "address": "...", "granted_at": ts } | null,
    "token":          null | { "name", "symbol", "image", "links", "mint", "dbc_pool", "launch_signature", "first_buy_sol" },
    "pin":            null | {
      "agent_ref":   { "repo", "repo_hash", "commit", "agent_slug", "strategy_slug" },
      "version":     3,                                # == chain
      "config_hash": "<hex>",                          # == chain
      "fee_bps":     5000,                             # == chain
      "quote_mint":  "So111…",                         # == chain
      "config":      { ... },                          # PRIVATE — served to the runner only
      "signature":   "<tx>",
      "pending":     { ... } | null,
      "scan":        { "passed", "at", "findings" } | null
    },
    "created_at": ts
  }
}
```

A record with a `pending` still on it is a transaction nobody signed, or one
that never landed: the card says "never created" and offers to discard it.
There is nothing to resume — creating a vault is one signature. Sweeps are not stored; they are
read from chain by signature. The per-run sweep ledger (one signature per
executor, ever) is idempotency, not history.

**Reconcile on every list:** Swig account, `Vault` PDA, DBC pool. An account the
RPC says is absent drops the record or rolls the stage back to the last piece
that exists. A timeout changes nothing.

The private config's canonical encoding — JSON, keys sorted at every depth, no
whitespace, array order preserved — is one function shared byte-for-byte by
the browser and the backend, tested against the prototype's pinned vector. If
it drifts, every vault on chain becomes unverifiable.

---

## 5. Milestones

Each ends with something you can click or curl. In order; nothing built early
is thrown away later.

### M1 — Gateway chain badge

`condor/gateway_client.py` — Gateway through the server's hummingbot-api
(`/gateway/proxy/{path}`), so the Gateway the badge describes is the one the
server's bots trade through and there is no second address to configure (D14).
`GET /api/v1/settings/gateway/chain?server=` → `{kind: surfpool|node, rpc_host,
surfnet_version?, solana_core?, slot}`, host only, 502 with the cause when
unreachable. A badge beside "Gateway Running": amber for a fork, neutral for a
node. **Verify:** `vaults` shows `surfpool fork · 127.0.0.1:8899`; stop surfpool
and the badge becomes an error, not a stale answer.

### M2 — Wallet connect, attach, sign, submit

`frontend/src/lib/wallet/`: Wallet Standard discovery and connect (Phantom is
the target), `signTransaction`, `signMessage`; dev keypairs from
`VITE_DEV_WALLET_SECRET_*` registered as wallets only after M1's endpoint
answers `surfpool`; a provider exposing `signAndSubmit(build)` and
`signAndSubmitAll(builds)`; remembered connector with one silent reconnect.
`condor/web/routes/wallet.py`: `nonce`, `attach` (SIWS, Ed25519 verify, stored
on `user_id`), `GET /wallet`, `DELETE /wallet` (refused while any record has a
delegate or a pin that is not `Redeemable`), `submit` and `poll` proxied to
Gateway. The browser never holds an RPC URL. **Verify:** connect Phantom and a
dev keypair on the fork; attach; sign and submit a 0-lamport self-transfer
built by Gateway.

### M3 — The program and its Gateway routes

Lives in this repo at `programs/condor_vaults/` (Anchor workspace carried over
from condor-app `vaults/` with its `RUSTUP_TOOLCHAIN` pin and IDL step; the IDL
is vendored into the Gateway fork). Split into `instructions/*.rs`,
`state/*.rs`, `error.rs`.

**Gate — RUN, 2026-09-17, and it split.** The question was whether a PDA of
this program can be a Swig authority and act as one by CPI. Against the real
mainnet-forked Swig program:

| | result |
|---|---|
| PDA created as the Swig's **root authority** (`create_vault` → CPI `CreateV1`) | ✅ role 0's authority is the PDA |
| PDA **manages authorities** by CPI (`install_delegate` → `AddAuthorityV1` signed by the PDA with `invoke_signed`) | ✅ landed; two roles on the Swig |
| the program's own role-table parser | ✅ proven — Swig accepted the `acting_role_id` it produced |
| PDA **signs a transfer** by CPI (`withdraw` → `SignV2` wrapping an SPL transfer) | ❌ **`SwigError::Cpi` (0x8)** |

The failure is deliberate, not a bug in this program:
`program/src/actions/sign_v2.rs` opens with `check_stack_height(1,
SwigError::Cpi)`. **Swig's execution paths refuse to be reached by CPI at all** —
`sign_v2`, `sub_account_sign_v1` and `sub_account_sign_v2` all carry the check;
`create_v1`, `add_authority_v1`, `remove_authority_v1`, `update_authority_v1`
and `transfer_assets_v1` do not. So a program may *own and administer* a Swig,
and may not *spend* from one. (`sign_v1` has no such check, but it is the
v1-account path and these accounts are v2.)

**What it cost: two instructions, and both are now resolved.** `withdraw` and
`redeem` were the only places the program itself moved money to a person, and
both were written as `sign_as_wallet` — a SignV2 CPI, which cannot work.
`withdraw` was **deleted**: while a vault is private its runner already controls
the delegate, which moves anything in the wallet at top level, so an instruction
was a second way to do what one key already does. `redeem` now pays from a
**program-owned redemption pot** that the administrator sweeps into before
`finalize_wind_down`, which refuses until it has. Everything else in the create
flow, the delegate lifecycle and the wind-down was unaffected.

The whole exit path was then run on the fork: finalize refuses while the wallet
still holds quote, the delegate's top-level sweep empties it into the pot,
finalize then succeeds and removes the delegate, and the pot is owned by the
program's PDA — no delegate, no administrator, no key.

**`AuthorityType::ProgramExec` was the alternative, and was rejected.** Swig has an
authority type for exactly this: it "validates that a preceding instruction in
the transaction matches configured program and instruction prefix requirements,
and that the instruction was successful"
(`state/src/authority/programexec/mod.rs`). So instead of the program calling
Swig, the two sit side by side in one transaction — `[condor_vaults::…, Swig
SignV2]`, both top-level — and Swig reads the instructions sysvar to confirm the
program's instruction ran and succeeded before it signs.

It verifies that *an* instruction of ours ran — not what the paired Swig sign
then does. The prefix is fixed when the authority is created, so it can pin a
discriminator but never an amount and never a destination, which is an account
rather than data. `redeem` is permissionless, so a holder could pair
`redeem(1)` with a Swig sign moving the whole pot to themselves and every check
would pass. Closing that would mean `redeem` reading the instructions sysvar and
parsing the paired SignV2's compact payload — a security-critical parser for
Swig's wire format, in the hot path, which is more of exactly what we decided
not to own (D31). The sweep below is simpler and ends stronger.

`transfer_assets_v1` was checked as a shortcut and is not one: it only moves a
swig account's assets to its own wallet address, a migration helper.

**What was built instead:** the redeemable pot is a token account owned by the
vault's own PDA. `finalize_wind_down` was already administrator-signed and
already the moment the vault stops trading, so the administrator sweeps the
quote across at top level (`sweep-to-pot`) and finalize *verifies* it, refusing
until the wallet is empty and the pot is funded. `redeem` then pays with a plain
`invoke_signed` and needs Swig not at all — which makes redemption stronger than
the design it replaces, because after finalize nothing with a key is in the
path.

Accounts:

- `Protocol { authority, administrator, fee_claimer, dbc_config, bump, _reserved: [u8; 64] }`
- `Vault` — PDA `["vault", swig_account]`, `#[derive(InitSpace)]`:
  `{ runner, swig_account, funds_owner, mint, dbc_pool, agent_ref, config_hash,
  quote_mint, version, fee_bps, issue_bps, state, delegate, created_ts,
  tokenized_ts, wind_down_ts, bump, authority_bump, _reserved: [u8; 64] }`.
  `mint == default` **is** "private"; there is no separate flag for the two to
  disagree about.

Instructions — seventeen. The first group needs nobody but the runner; the second
is what tokenizing adds:

| instruction | signer | does |
|---|---|---|
| `initialize(administrator, fee_claimer, dbc_config)` | upgrade authority → becomes `authority` | writes `Protocol` |
| `set_authority(key)`, `set_administrator(key)` | authority (+ separate payer) | rotation; what makes a multisig a one-call move (§1.7) |
| **`create_vault(id, quote_mint, fund_lamports)`** | runner | CPI Swig create with the PDA as root; runner pays and funds. The quote asset is chosen here — a private vault needs a unit of account from its first deposit |
| `install_delegate(pubkey)` | runner, **+ `Protocol.administrator` once tokenized** | CPI Swig: remove the current delegate role if any, add the new one; records `delegate`. Also how an administrator is replaced |
| `pin(agent_ref, config_hash, fee_bps)` | runner | version 1, `state = Running`. Requires no token; signed in the same transaction as `create_vault` and `install_delegate` (D5) |
| `publish_version(agent_ref, config_hash)` | runner | `version += 1`; refused after wind-down |
| `set_fee(bps)`, `set_active(bool)` | runner | refused after wind-down |
| **`tokenize(name, symbol, uri, issue_bps)`** | runner | one way. CPI DBC initialize with the PDA as creator and the wallet as leftover receiver; checks every economic term of the config (§1.4); runner pays; the mint keypair signs the outer tx |
| `collect_seed` | anyone (payer) | CPI `withdraw_migration_fee(creator)` as the PDA into the wallet's quote account; DBC's own flag makes it one-time |
| **`collect_leftover`** | anyone (payer) | CPI `withdraw_leftover` — the unsold supply into the wallet, where the delegate can trade it. Needs no signature at all: DBC pays whoever the config named |
| `claim_income(source)` | runner | CPI DBC claim creator trading fee *or* surplus as the PDA; receiver = the runner in the account |
| **`claim_position_fee`** | runner | the migrated pool's locked position fees, as the PDA, into the runner's accounts |
| `wind_down` | runner, or authority (+ payer) | one-way: `state = WindingDown` |
| `finalize_wind_down` | `Protocol.administrator` | refuses until the wallet is empty (a passed account holding a position or a balance above dust stops it, the vault's own token excepted) **and, for a tokenized vault, the redemption pot is funded**. It *verifies* the sweep, it does not perform it. Then CPI Swig remove delegate; `state = Redeemable` |
| `redeem(amount)` | any holder | burns `amount`; pays `amount × pot_balance / circulating_supply` straight out of the **redemption pot** with `invoke_signed` — no Swig — where `circulating_supply = mint.supply − pool_vault.amount − wallet.amount` |

`create_vault`/`tokenize` are the split (one instruction became two),
`collect_leftover` puts the treasury where the strategy can work it, and
`claim_position_fee` is separate from `claim_income` because DAMM v2 is a
different program with an account list that shares nothing — one instruction
covering both would be a 25-account struct that Anchor could check none of.

There is deliberately **no `withdraw`**, and that is not a restriction. While a
vault is private its runner already controls the delegate, which can move
anything in the wallet at top level through Gateway; an instruction would have
added a second way to do what one key already does. Once tokenized, no path out
exists at all except `redeem`, which is the guarantee.

Gone from the prototype: `VaultRunner`, `join_vault`, `leave_vault`,
`set_buyback`, the slug, the attestor, every owner-as-root assumption. Reserve
bytes are so a later field is an instruction, not an account migration.

Mapping to the QuickNode v1 example: `Registry` + `ApprovedAsset` ↔ `Protocol`;
`Strategy` by index with `has_one` ↔ `Vault` by Swig; its `fee_bps` +
permissionless `collect_fees` ↔ our `fee_bps` + the crank's sweep; its
program-owned vaults with `withdraw` ↔ our program-rooted Swig with `redeem`
after wind-down, trading through Gateway's delegate instead of per-venue CPI.

Gateway routes under `/chains/solana/vaults/`:

```
GET  vaults?network=                                       every Vault, decoded
GET  vaults/:swigAccount?network=                          one, decoded (+ treasury, circulating supply, the migrated pool, and value per token when Redeemable)
POST vaults/build-create-vault     {walletAddress, quoteMint, fundLamports, agentRef, configHash, feeBps}
                                                                                   → build (runner): the Swig,
                                     its delegate and the strategy pin in ONE transaction (D5)
POST vaults/build-tokenize | build-publish | build-set-fee | build-set-active
     | build-claim-income | build-wind-down | build-launch-config                   → build (runner)
POST vaults/build-install-delegate {walletAddress, swigAccount}                     → build (runner; Gateway mints the delegate and pre-signs with the platform key as an extra signer)
POST vaults/build-redeem           {walletAddress, swigAccount, amount}               → build (holder)
POST vaults/collect-seed | collect-leftover | migrate                                 → execute (platform key pays)
POST vaults/sweep-to-pot           {swigAccount}                                      → execute (the delegate, at top level — Swig will not sign by CPI)
POST vaults/withdraw               {swigAccount, destination, amount, mint?}          → execute (the delegate): a PRIVATE vault's runner taking assets out. Refused once tokenized
POST vaults/burn                   {swigAccount, amount}                              → execute (the delegate): what a sweep burns
POST vaults/fund-delegate          {swigAccount, lamports}                            → execute (the delegate): gas for the key that signs
POST vaults/finalize-wind-down     {swigAccount}                                      → execute (platform key)
POST vaults/set-authority | set-administrator {key}                                  → execute (platform key, until it is a multisig)
```

`pool-info` gains `dammV2Pool`, `claimableCreatorFee`, `migrationFeeClaimed`,
`surplusClaimed`. `scripts/migrate-dbc-pool.mjs` drives a completed curve
through `migrationDammV2CreateMetadata` and `migrateToDammV2` on the fork,
where there is no keeper.

Gateway's wallet store gains **one platform key**, used as administrator and as
protocol authority until the authority is rotated to a multisig. It is not a
user key.

**Fork setup, once:** `dbc-create-config.mjs` with
`migrationFee: { feePercentage: 80, creatorFeePercentage: 100 }`,
`creatorTradingFeePercentage: 50` and a flat 100 bps migrated-pool fee
(`DammV2BaseFeeMode` fixed, no scheduler); recreate the partner config; restart
Gateway; record the address where `dev-chain.sh` does; `initialize` the
protocol with the platform key as administrator.

**Verify:** the gate test; then create → publish → tamper legs of the
prototype's `test-vault-run.mjs` rewritten against Gateway with a dev keypair;
the hash equal on chain, in the store, and in the browser.

**Run 2026-09-17, on the fork, through hummingbot-api's passthrough.** One
signature created vault `6MYcqzn…`: the Swig carries two roles (the program's
PDA as root, the delegate), the record reads back `version 1`, `Running`,
`delegate CvscUA…`, the signed config hash and the agent pin, and the funding
moved into the wallet in the same transaction. Four defects fell out of doing
it rather than reading it — see revision 28.

### M4 — Vaults tab, Create Vault, and the vault page

`condor/vault_store.py` (§4) and `condor/web/routes/vaults.py`. Every mutation
returns a build; a confirm call records once the signature is confirmed on
chain. Runner = the attached wallet; 409 without one.

```
GET    /vaults?server=                              records + balances, reconciled
POST   /vaults?server=                              {label, quote_mint, fund_lamports, agent_slug,
                                                    strategy_slug, config, fee_bps} → {account, build}
POST   /vaults/{acct}/confirm                       {signature} → promotes the staged pin, if the
                                                    chain carries its hash
POST   /vaults/{acct}/build/{name}                  set-fee | set-active | wind-down | claim-income —
                                                    one route over an allowlist, since each was a
                                                    handler whose whole body was "pass it on"
POST   /vaults/{acct}/build-publish                 a later version; → /published {signature}
POST   /vaults/{acct}/build-install-delegate        replacing the administrator; → /delegated
POST   /vaults/{acct}/build-launch-config           the launch terms; → /launch-config-created,
                                                    which remembers the address so nothing asks for it
POST   /vaults/{acct}/build-tokenize                {name, symbol, uri, issue_bps}
POST   /vaults/{acct}/withdraw                      {destination, amount, mint?} — private only
POST   /vaults/{acct}/build-redeem?server=          {amount} — any holder, no record needed
GET    /vaults/{acct}/holdings                      what the wallet holds, plus where its token trades
POST   /vaults/{acct}/scan                          → {passed, findings, at}   (Condor's run gate; stored per vault+version)
GET    /vaults/{acct}/config                        runner only
GET    /vaults/{acct}/metadata.json                 unauthenticated; the token's metadataUri
PATCH  /vaults/{acct}                               {label}
DELETE /vaults/{acct}                               only a record whose transaction never landed
```

Creation stages the config as pending and `confirm` promotes it **only if the
chain carries its hash** — the check that makes the whole commitment mean
something, since Condor's copy of a config is believed only because it hashes to
what the runner signed. The scan runs in the flow and its verdict is shown to
the runner ("Condor will not run this version"), but it gates nothing on chain.
`build-publish` is the same two steps at a later version.

**Vaults tab** (`pages/Vaults.tsx`): the attached wallet, Create Vault, and one
card per vault — label, Swig address, **phase** (Private / Tokenized), chain
state, strategy and version, burn and issued shares, and the installed delegate.
A record whose transaction never landed says "never created" and offers only to
be discarded — there are no steps to resume. A record that disagrees with the
chain says so, and says the vault will not run until it is resolved.
**Create Vault** (`pages/VaultCreate.tsx`): one form — a name, funding, agent and
strategy from Condor's catalog, the burn share, and the private config — and one
signature. **No token fields**: nothing here decides anything about a token,
because nothing here has to.

**Tokenizing** is on the vault page's Token tab, behind an explicit
acknowledgement, with the name, symbol, metadata URI, the vault's own DBC config
and `issue_bps`. It belongs there rather than in the header because a one-way
action does not go next to Pause.

**Gateway fix:** `/wallet/swig/delegate/forget` sweeps the delegate key's own
SOL to the funds-owner before dropping the key; today it strands it.

**Verify:** the three signatures on the fork; the Swig has two roles; close the
tab after step 1 → "incomplete", continue → finishes; re-fork → the record
drops. Then a private vault end to end: fund it, run it, move the assets back
out with its delegate. `uv run pytest`; `npm test && npm run lint`; tab order at desktop and
phone widths.

### M5 — hummingbot-api: a wallet per account

The change that lets the crank run more than one vault on a server (D19):
`gateway_wallets.json` per account, two routes to read and set it, and
`_create_trading_connector` passing `address=` when the account has one.
Accounts without the file behave as today. Condor's crank creates one hbapi
account per vault on first start, binds the funds-owner, and passes that
`account_name` in the run config. **Verify:** two accounts bound to two Swigs,
one LP executor each, both opening positions in the same minute, each position
owned by its own wallet on chain. Ship as an upstream PR; the vaults stack
builds hbapi from the branch until merged.

### M6 — Crank, sweep, vault page

**Crank** (`condor/vaults_crank.py`, one loop per server). Each pass lists the
vaults from chain and, for each one Condor has a record of, decides whether to be
running it. The start checks are pure and testable without a chain, which is the
only way a list like this stays honest — every one of them has a failure it
prevents:

| check | what it prevents |
|---|---|
| the chain says `Running` | the runner's pause being advisory |
| a delegate is installed | failing transaction by transaction instead of noticing |
| the installed delegate is the one Condor holds | running a vault its runner has taken back |
| version, config hash and fee equal the chain's | running parameters the runner did not sign |
| the stored config **re-hashes** to the chain's | the same, against Condor's own operators |
| Condor's scan passed this version | Condor's private run policy, which is not on chain |
| one live engine per vault | doubling every position |

It also pushes the two permissionless post-migration calls (`collect_seed`,
`collect_leftover`), tops the delegate up from the wallet when its SOL falls
below a floor — a delegate that cannot pay for gas looks, from every surface,
like the strategy failing — and drives a `WindingDown` vault to
`finalize_wind_down`.

**Four things bind a run to its vault, and each was found missing by running
one.** The crank creates the vault's hummingbot-api account and binds it to the
vault's wallet; it pins the engine to the vault's *server*, not the runner's
default; it hands the engine the user whose store the record came from; and the
agent's tool seat is started on the vault's account rather than
`master_account`. Any one of them missing and the strategy trades — plausibly,
and from the wrong wallet or the wrong chain, with every surface still saying it
is running the vault. The delegate is funded at creation for the same reason in
reverse: the top-up is signed *by the delegate*, so a delegate born with nothing
can never be given anything.

The crank keeps no registry of its own: engines register with the loop
supervisor, and it holds only which agent id belongs to which vault.

**The seat sees the vault's money and no other.** An account with a Gateway
wallet bound to it gets that wallet's balances filed under its own name —
hummingbot-api used to read the default wallet and file everything under
`master_account`, so a vault's agent read the operator's balance as its own —
and a seat given an account is scoped to it for reads as well as writes. Both
were found by watching the first tick reason about 200 SOL and 235 USDC on
Hyperliquid, none of which the vault held.

**Sweep** (`condor/vault_sweep.py`). On each pass the crank reads the vault's
terminated LP executors and accrues `fee_bps` of each one's
`fees_earned_quote` — income, never `cum_fees_quote`, which is gas: a burn
proportional to how much Condor spent on transactions is not a return. When the
accrual passes `min_sweep_lamports` (0.01 SOL) it buys the token — on the curve
before graduation, through the router after — and burns it.

Three properties, each the reason for a specific line of code:

- **one accrual per executor, ever.** The ledger is keyed by executor id and
  written *before* anything is signed. A crash between the two costs the burn; the
  other order spends the same capital twice.
- **rounding is down.** Up would spend a lamport no fee earned — holders' assets
  buying holders' assets, once per close, forever.
- **a failed burn is not a loss.** Tokens bought and not yet burned sit in the
  vault's wallet as treasury, which `redeem` already excludes from the circulating
  supply. They are out of circulation either way, and the next sweep burns them.
  That is what makes two transactions acceptable where the plan first asked for
  one.

**Vault page** (`/vaults/:account`), four tabs and a header:

- header: phase and state, and the runner's actions — Pause / Resume, and either
  Stop for good (private) or Wind down (tokenized, one confirmation: "holders
  redeem what the vault holds")
- **Summary**: holdings read from the chain, the wallet to fund, the installed
  delegate and what it can do, and — while private — **Withdraw**. There is no
  withdraw *instruction* (D24); the card asks the delegate to move what the
  delegate can already move, and it disappears at `tokenize`
- once `Redeemable`, a **Redeem** card above the tabs, for any holder rather
  than the runner alone
- **Strategy**: agent, strategy, version, config hash, the config itself
  (read → edit → Publish vN), and whether Condor's check passed this version
- **Activity**: runs, open positions, sweeps and burns from chain
- **Token**: while private, the launch config — start and end value, the
  20-80 raise split, the creator's fee share, the migrated pool's fee — as a
  transaction of its own, then what tokenizing means and the form to do it;
  once tokenized, the mint, the pool, issued and burn shares, and a link out to
  trade

**Portfolio and Pools.** A vault's assets are not in any hummingbot-api account —
they are in a Swig the program owns — so Portfolio lists vaults in their own
section rather than folding them into a personal total, which for a tokenized
vault would be false. The DEX browser gains a **Vaults** tab: a vault's pool
exists the moment its curve graduates, long before any indexer has seen it, so
those rows are built from what Condor knows rather than looked up, and carry no
volume or TVL because nothing has measured them yet. From a row, the existing
pool workspace opens an LP executor against the vault's own token — funded, when
the runner wants, out of the treasury.

**Verify:** the gate test; launch → pool-info → quote → build, checking the
arithmetic; a full run with a sweep and a burn visible on chain; graduation on
the fork via the migrate script, seed and leftover collected, income claimed;
an LP position on the vault's own pool funded from the treasury; wind-down
through redemption with two holders.

---

## 6. Rules carried over (each cost the prototype time)

- No fallbacks, no public mainnet RPC, no default Gateway URL. A missing setting
  is an error that names the setting.
- Credentials never in the repo: QuickNode URLs, Gateway `apiKeys.yml`, the
  platform keypair, `CONDOR_WEB_JWT_SECRET`, dev wallet secrets.
- The backend reads the chain through Gateway's RPC; the browser signs builds
  and never holds an RPC URL.
- Reconcile before anything else on a fresh fork; drop only on a definite
  "absent".
- One router, one model catalog, one canonical hash function, one record, one
  flow.
- Authority instructions take a separate payer and never read the authority's
  lamports (§1.7).
- Verify by running it and checking the numbers, not by reading the grep.
- Condor restarts kill engines: a stop that finds no engine is "already
  stopped".
- macOS `ps` has no `etimes`; a stale SBF build dies at entry
  (`rm -rf target/sbpf-solana-solana`).
- The 10 SOL threshold is load-bearing: it is what makes Meteora migrate for us.

---

## 7. Before mainnet, and Phase 2

**Before the first mainnet vault** (not optional): the protocol authority and
the program upgrade authority on a Squads multisig; token metadata pinned to
IPFS or Arweave at launch; the leftover-tokens check done on the fork.

**Phase 2:**

- Permissionless wind-down: caller-built close-and-swap signed by the PDA,
  allow-listed to real DEX programs and bounded against an oracle price. The
  one open design question.
- A second administrator: `Protocol.administrator` becomes a registry.
- Profit-share sweeps for non-LP vaults (realised P&L on close), if LP vaults
  prove the model.
- Hardware and mobile wallets, multiple attached wallets, wallet UI polish.
- Delete the bridge and the Next app in condor-app.

---

## Appendix A — Revision history

All revisions 2026-09-17, in one design session.

| rev | what changed | why |
|---|---|---|
| 1 | Plan cut from the prototype's handover: Vaults tab, Add Agent Wallet in Settings, chain badge; Anchor v1 over the v2 release candidate; Gateway-held owner key for the first cut | start point |
| 2 | Swig dropped: agent wallet = a Gateway-generated keypair | with owner key, delegate and crank all in Gateway, Swig separated nothing |
| 3 | Gateway never stores a user key; Swig back with a browser-held root; browser signer and SIWS in Phase 1 | it is a dapp |
| 4 | Vault and agent wallet merged into one record; PDA seeded by the Swig | the vault was metadata about one Swig |
| 5 | Token launch required at create; `fee_bps` on the vault; sweep made deterministic | fee accrual is the product |
| 6 | One create flow with resumable drafts; SPL Token | no standalone agent wallet |
| 7 | Skeptical review: hbapi's Gateway connector uses the default wallet, the migration gap, dust, buy-and-hold, delegate gas, metadata URI; Token-2022 restored | findings folded into milestones |
| 8 | The raise funds the vault via the DBC migration fee; pool creator = the Swig | the only way a curve hands reserve to anyone |
| 9 | The token is a fee-royalty, not a share; 30 % seed; sweep default 50 % | sweeping is the whole investment interface |
| 10 | Creator = the manager's wallet; 20 % seed; creator streams are the manager's income | mirrors other launchpads |
| 11 | The program's PDA is the Swig root and DBC creator; 80 % seed nobody can withdraw; manager becomes a role; one-way shutdown; in-kind redemption | "the whole idea is trustless funding" — automatic is not trustless; root is |
| 12 | Wind-down converts to the quote asset (SOL first); shutdown process defined; wind-down stays permissioned | oracle-free permissionless close-and-swap is unsafe |
| 13 | Creation permissionless; protocol authority may wind down; no Swig action limits; timers dropped; roles renamed manager → **runner**, delegate holder → **administrator** | final vocabulary and trust model |
| 14 | Attestor removed; the scan is Condor's private run gate; plan rewritten as one document | an on-chain review claim is a liability |
| 15 | Threat model: administrator registry, co-signed install, administrator-signed finalize; fee share back to 50/50, flat 100 bps migrated-pool fee | a runner could otherwise install their own delegate and drain the seed |
| 16 | **2026-09-18.** `quote_mint` chosen at launch (the config that fixes what the pool quotes in), not at creation; a private vault does not wind down; `fee_bps` and the buy-and-burn sweep removed; native SOL wrapped into the wSOL ATA before a tokenized wind-down's sweep; `build-deposit` route | a private vault is an agent wallet its runner controls — nothing about a token should be asked of it before there is one; buybacks are the manager's discretion, not a mechanic |
| 17 | **2026-09-18.** §1.8: after tokenize the trading role is destination-scoped — trade allowlisted pools, transfer nowhere — via Swig's `Program` + `TokenDestinationLimit`; asset universe fixed at launch; `allow_pool` (administrator-signed in v1); co-signed install retired; routers deferred; pools only for v1 | redemption has teeth only if nothing between `tokenize` and `redeem` can move assets to a wallet; rev 13 rejected Swig *magnitude* limits, and destination limits are a different primitive |
| 18 | **2026-09-18, same session.** §1.8 corrected — destination-scoped Swig actions cannot stop a swap whose `user_token_out` is the manager's, verified in Meteora's `swap.rs`; §1.9 moves the tokenized phase to a program-owned PDA with an `execute` that validates recipients structurally; this deletes the pot, sweep-to-pot, the co-signed install and `allow_pool` | "freedom to swap and LP into new pools, and no withdrawal" needs a check on *recipients*, which only the signer's own program can make, and the Swig will not let a program be that signer |
| 19 | **2026-09-18.** Swig removed entirely: the vault's authority PDA is the wallet in both phases; `execute_unchecked` (private) and `execute` (recipient-checked, mandatory after launch) replace the Swig delegate role; `tokenize` moves nothing; no pot, no sweep, no co-signed install; Gateway's one signing mode wraps into `execute*` | the program already held the vault's two roles; Swig was a second copy of them, and the Swig→PDA migration §1.9 first needed would have closed every open position at launch |
| 20 | **2026-09-18, implemented and run on the fork.** Program rewritten to §1.9 rev 19 (`execute`, `execute_unchecked`, `venues.rs`; `swig.rs` deleted), Gateway gained the `vault` wallet type (`sendAsVaultDelegate`, `vault-wallets.json`, `vault-wrap.ts`) and lost `sweep-to-pot`/`burn`, Condor and the frontend renamed `swigAccount`/`fundsOwner` → `vaultAccount`/`wallet`. Verified against a fresh mainnet fork: create/deposit/withdraw/top-up/pause/resume land; a DLMM SOL→USDC swap through `execute` lands when `user_token_out` is the wallet's ATA and fails with `AccountNotAllowed` (6023) when it is another key's ATA; the same redirected swap lands through `execute_unchecked` | the check that a Swig could not make, made by the signer's own program — the redirected-output withdrawal §1.8 found is now a refused transaction |
| 21 | **2026-09-18.** *Runner* renamed *creator* everywhere — `Vault.creator`, `creator_address`, `CreatorOnly`, `NotCreator`, the Gateway and Condor fields, the UI. Earlier rows keep the old word. Meteora's *pool creator* (the wallet PDA) is always written out in full | "we don't need another term since we're using creation anyway" — one word for the person who made the vault, and it is the word the act already uses |
| 22 | **2026-09-18.** The wallet PDA is the **Treasury**: seeds `["treasury", id]`, account `treasury`, `treasury_bump`, `treasuryPda`, `treasury_address`; a vault is *Vault + Treasury*. The unsold supply it holds, previously also called the treasury, is the **retained supply** (`retained.rs`, `retained_token_account`, `retainedSupply`). The mint stays DBC-created, pointed to by `Vault.mint` | "call the wallet PDA Treasury, so it's Vault and Treasury" — and one word cannot name both the account and one of its balances |
| 16 | Simplicity pass: `remove_delegate`, in-kind redeem, the seed flag, `fee_buyback_bps`, the sweep tool and prompt rule, every MCP change, the Settings section and the Buy/Sell widget removed; `active` folded into `state`; `route_creator_fees` → runner-signed `claim_income`; vault page to four tabs. One platform key, `set_authority` / `set_administrator`, registry deferred; multisig-ready rules and the before-mainnet list | fewer moving parts, same properties |
| 17 | **Vault creation split from tokenizing.** A vault is private until it launches a token: `withdraw` exists and refuses from `tokenize` onward; `quote_mint` moves to creation; `pin` needs no token; the administrator co-signs only once tokenized; NAV comes back, because a launch priced against existing assets has something to be priced against. Config checked by terms rather than address, since each vault prices its own launch | "before token, the vault is private, meaning runner can deposit/withdraw freely for own use" — and a private vault has nobody to protect, so a self-hosted runner needs nothing from Condor |
| 18 | `issue_bps` on chain — circulating over max supply at launch. The second-DBC sale was dropped: DBC creates its own mint, so it would have been a second token | "they set the circulating vs max supply, essentially" — and one token per vault is what makes redemption mean anything |
| 19 | **The treasury moved into the vault's wallet.** Leftover receiver = the funds owner; `inject` transfers through the Swig; `redeem` excludes the wallet's own balance as well as the pool's; `finalize_wind_down` stops counting the vault's own token | "the runner can LP for their own token… using the non-circulating vault tokens in treasury" — in a PDA-held pot the delegate could never reach it, so the treasury could only ever shrink. Vault tokens now appear in Portfolio and vault pools in the DEX browser |
| 20 | `inject` removed, with the DAMM v2 price reader it needed. An LP position funded from the treasury already converts unsold supply into vault capital as the market buys, at the market's price | "the runner can LP the tokens in treasury, so no inject capital is needed" — and the instruction was strictly worse: a runner-only counterparty and a price to argue about |
| 21 | Launch terms became **bounds** rather than constants: migration fee 20–80 (default 50), creator trading share ≤ 50, any fixed migrated-pool fee option — all three recorded on the `Vault`. `build-launch-config` builds the per-vault config | "make migration fee settable by the creator… that value and other config values should be settable". The migration fee *is* the split between the strategy and the holders' exit, so it is a product decision, not a constant |
| 22 | **The D3 gate was run against the real Swig program and split.** A PDA *can* be a Swig root and *can* manage authorities by CPI — both proven on chain, and the role-table parser with them. It *cannot* make the Swig sign: `sign_v2` opens with `check_stack_height(1, SwigError::Cpi)`. So `withdraw` was deleted (the delegate already moves anything at top level, and the runner controls the delegate) and `redeem` now pays from a program-owned redemption pot that the administrator sweeps into before finalize. `ProgramExec` was evaluated and rejected: it cannot bind what the paired Swig sign does, so a permissionless `redeem` could be paired with a drain | found by running it, not by reading it — and the result is stronger than the design it replaced, because after finalize nothing with a key is in the redemption path |
| 23 | Built: the Anchor program (17 instructions, Swig and DBC wire formats hand-written and pinned by encoder tests), Gateway's vault routes and vendored IDL, Condor's wallet/vault routes and stores, the browser wallet and canonical-hash pair, the Vaults tab and vault page, hbapi's per-account Gateway wallet, and the crank and sweep | M1–M6 |
| 24 | **One Gateway, by construction.** Condor's direct Gateway client is gone: every call goes through the server's hummingbot-api passthrough, and the `gateway_url` setting with its per-server URL is deleted. Found by looking: on the working stack hbapi reported a Gateway container on `:15888` while trading through `:15889`, and the new chain badge sat beside that status row saying something true about a different Gateway | "why have condor talking to a 2nd gateway?" — and the answer is that avoiding hbapi endpoints justified not adding twenty vault routes upstream, not keeping a second address. Forcing them to agree is simpler than detecting when they do not |
| 25 | **Creating a vault is one signature.** `create_vault`, `install_delegate` and `pin` in one transaction; the stage ladder, the three confirm routes, `build-pin` and the resumable stepper are deleted, and a record whose transaction never landed is discarded rather than resumed | there is no useful moment between the three, and every piece of resume machinery existed only to survive a closed tab between them |
| 26 | **The two promises with no button.** `build-launch-config` and `build-redeem` reach the product: the Token tab builds the vault's own launch terms (the 20-80 raise split among them) instead of asking for a pasted config address, and a Redeem card appears above the tabs once a vault is `Redeemable`, for any holder rather than the runner alone. The runner's plain builds collapse into one allowlisted route, which also picks up `claim-income` | both were built on chain and in Gateway and reachable from neither; a page that cannot redeem is a program whose central promise nobody can call |
| 29 | **Ran the cover LP agent on a private vault, which found the run bound to almost nothing.** The delegate was born with no SOL and its top-up is signed by the delegate, so it could never be funded — the create transaction now seeds it. The engine was handed user 0, then the runner's *default server*, so a vault on the fork ran its strategy against a different hummingbot-api, a different Gateway and a different chain. The vault's hbapi account was named but never created or bound. And the agent's tool seat defaulted to `master_account`, so an LP executor would have opened from the operator's wallet — `account_name or "master_account"` was a hardcoded fallback in the MCP tools, now the seat's own account. The crank's second engine registry went to the loop supervisor, caught by the repo's own guard test the moment the file was tracked | every one of these is invisible to reading: the vault runs, the logs say so, and the money is somewhere else |
| 28 | **Created a vault on the fork, which found four things reading never would**: `decodeVault` read camelCase from a snake_case IDL, so every vault read was broken; `dammPool` was in the response schema and computed nowhere, so the listing 500'd and the DEX Vaults tab could never have shown a row; one undecodable account took the whole listing down; and `pin`, `install_delegate` and `wind_down` all required a protocol account a private vault has no use for (D34). One signature now lands a vault with two roles on its Swig, `version 1`, `Running`, and its funding inside | "verify by running it and checking the numbers, not by reading the grep" |
| 27 | **Condor stopped encoding Solana instructions**: the burn and the delegate top-up became Gateway routes, deleting PDA derivation, an ed25519 on-curve test, ATA derivation and two instruction encoders from Python (D33) | a second implementation of a wire format, in another language, in the path that spends a holder's assets |

## Appendix B — Questions settled

| question | answer |
|---|---|
| mTLS to the hbapi-managed Gateway? | not Condor's problem: every Gateway call goes through hbapi, which holds the certificates (D14) |
| Owner key in Gateway? | Never; delegate keys and the platform key only |
| Where does the program live? | this repo, `programs/condor_vaults/`; Gateway vendors the IDL |
| One wallet per vault? | yes; third-party runners are not planned |
| Why not a direct Gateway connection from Condor? | because two addresses for one Gateway cannot be kept in step, and the drift is silent — Condor building against one node while the bots trade on another. One passthrough route upstream is cheaper than twenty vault endpoints *and* than a check that the two agree |
| How many signatures to create a vault? | one. The Swig, its delegate and the strategy pin have no useful moment between them (D5) |
| Token required at create? | **no.** A vault is private until its runner decides otherwise, and tokenizing is one way |
| Does a runner need Condor's hosted service? | not for a private vault: own delegate, own crank, own machine. A tokenized one installs the registered administrator, because its holders' money is what the co-signature protects |
| Do we need a crank at all? | something must trade; **Condor** need not be it. `collect_seed` and `collect_leftover` are permissionless, Meteora's keeper migrates, and only `finalize_wind_down` is Condor-signed — and that is to stop a stranger stranding a position, not because Condor is owed anything |
| What happens to the supply not sold at launch? | it is the vault's treasury, in the vault's own wallet: not circulating, not dividing a redemption, and LP-able by the strategy. There is no way for the runner to buy it directly, and none is needed — a runner who wants the token opens an LP position on the pool, or buys from it, like anyone else |
| How does the retained supply ever reach the market? | through an LP position the strategy funds from it. No second DBC pool (that would be a second token) and no `inject` instruction (that would make the runner the counterparty and need a price somebody asserts). Capped by `1 − issue_bps` either way |
| Is NAV shown? | yes — reversing rev 9. It was meaningless when the token was born before the assets; a launch priced against existing assets makes it the thing the token was sold on |
| Dev keypairs on mainnet? | refused unless the chain endpoint says `surfpool` |
| What is "quote asset locked by the user"? | the first buy — optional quote into the curve |
| Where is the quote asset chosen? | **at launch** (rev 16, reversing this row). It is what the curve sells for, what the migrated pool quotes in, what a wind-down converts into and what a redemption pays — fixed by the launch config, written to the vault at `tokenize`. A private vault has none and needs none |
| Can the manager sign the vault's own trades? | private: yes — install your own key as the delegate. Tokenized: today no (the delegate can sign anything, so it must be the administrator's); after §1.8 yes, because the role can trade and cannot transfer, so whose key holds it stops mattering |
| Why not Swig's `TokenLimit` to bound the manager? | it bounds the *amount*, not the *place*: `Program(Token)` comes with `ProgramCurated`, so a transfer to the manager's own ATA is allowed up to the cap. `TokenDestinationLimit` to pool vaults is the lock; `TokenLimit` is a speed bump |
| Routers (Jupiter, DFlow, Titan) in the scoped role? | not in v1: a route's outflows land in pool vaults chosen at quote time, which no destination limit can name in advance. Admitting one means a `TokenLimit`, i.e. the hole above. v1 swaps on allowlisted pools directly |
| Can Swig's actions stop a swap whose output goes to the manager? | **no.** They constrain where the vault's tokens *leave to*, and a DEX swap's output account is constrained by mint only (`lb_clmm/src/instructions/swap.rs`, `user_token_out`). The recipient check has to be made by the signer's program, which is why the tokenized phase moves to a program PDA (§1.9) |
| Why keep Swig at all, then? | for the private phase, which is what it was chosen for: any delegate key, any transaction, the runner's own money, no program changes for a new venue. Nothing in §1.9 touches it |
| Isn't a program allowlist enough — `Program(DLMM)`, `Program(Raydium)`, no `Program(Token)`? | it is the cheap half and it is kept: it blocks a plain `transfer` (Token is not a permitted top-level program; the DEXes' own CPIs into Token are not checked). It cannot block `DLMM.swap { user_token_in: vault USDC, user_token_out: manager's SOL account }` — an allowed program, outflow covered by `TokenLimit`, proceeds redirected by an account the caller names and the DEX checks by mint only. Which program was called says nothing about which accounts it was handed; only the signer's own program can read those (§1.9 rule 2) |
| hbapi one-live-vault limit? | fixed in Phase 1 (M5) |
| A vanished runner? | the protocol authority may wind down, no notice |
| Delegate blast radius? | accepted and stated; no Swig action limits |
| Migration fee split? | the runner's, 20–80 %, 50 % by default — it is the split between the strategy's capital and the holders' exit depth, so it is a product decision rather than a constant, and it is on the `Vault` before anyone can buy |
| Which launch terms are settable? | the migration fee (20–80), the creator's trading share (≤ 50) and the migrated pool's fee option (any fixed one). Fixed: the 10 SOL threshold, the creator's 100 % share of the migration fee, Token-2022 with a fixed supply, Condor as fee claimer, and the leftover receiver being the vault's wallet |
| Creator = Swig or manager? | neither: the program's PDA |
| Permissionless wind-down? | Phase 2; needs an oracle bound |
| Timers on wind-down? | none; Condor cranks to completion; the runner can replace the administrator |
| An attestor? | no: an on-chain "Condor reviewed this" is an endorsement Condor does not want to make |
| Can anyone be an administrator? | for a private vault, yes — whoever the runner installs. For a tokenized one, no: `Protocol.administrator`, a registry only when a second one exists |
| Who finalizes a wind-down? | the administrator; a permissionless finalize could strand positions |
| Two platform keys? | one, until the authority moves to a multisig; rotation instructions from day one |
| Per-vault quote asset? | kept: `quote_mint` on the pin, SOL preselected |

# Condor Vaults architecture — adversarial review

Reviewed 2026-09-18 against `CONDOR_VAULTS_ARCHITECTURE.md` and implementation at Condor commit `43de5aec`, plus the local Gateway vault client. This reviews the rewritten PDA-treasury design; it does not assume the old Swig design remains.

The architecture and several source files changed during the review. The final diff added a 1% protocol graduation fee (99% of the migration fee to treasury), its constant and collection attempt. That amendment was inspected and does not resolve the findings below. Architecture line numbers refer to the initial snapshot and may have shifted. Only this review file was added by the reviewer; concurrent edits were left untouched.

**Verdict: the document's central no-theft claim is not supported.** There are direct authorization bypasses, an unsafe private-to-tokenized transition, and an impossible wind-down transition. Independently of these implementation bugs, unrestricted trading in permissionless pools permits deliberate extraction of economic value. Calling that “bad trading” does not remove the adversarial trust assumption.

The move to a program-signed treasury fixes the old Swig CPI limitation and provides a useful enforcement point. It does not by itself prove that all economic claims on treasury assets remain with holders.

## Scope and evidence

- Read the complete architecture and the current execution, venue, tokenization, redemption, income, finalization, and supporting code.
- Ran the program's existing Rust unit tests: **12 passed**.
- Built a separate harness importing the actual program crate and calling its actual `venues::check_before` / `check_after`: **six tests reproduced acceptance of unsafe account/state combinations**. These are reproductions of missing checks, not six security tests establishing safety.
- Harness: `/private/tmp/condor-architecture-adversarial/src/lib.rs`. Run with `cargo test --offline --manifest-path /private/tmp/condor-architecture-adversarial/Cargo.toml`.
- Consulted primary Solana documentation and Meteora's published SDK/IDL for external authority semantics.
- No exploit transactions, token launches, transfers, or program upgrades were submitted. Venue CPI exploit sequences still require end-to-end fork tests; local checker acceptance is distinguished below from an executed drain.

Severity here is about the promised protocol: **Critical** means a direct theft path or a fundamental failure of the advertised custody model; **High** means substantial asset loss, frozen exits, or unenforced financial terms; **Medium** means operational or disclosure failures.

## Critical findings

### A1. Permissionless trading makes economic theft possible even with perfect recipient checks

**Architecture:** §§1, 3, 5.2, 9, especially lines 16–19 and 327–336. **Evidence:** architectural counterexample, independent of implementation bugs.

Attack:

1. A malicious creator or compromised delegate establishes or controls liquidity in a pool on an allowed venue.
2. They make the treasury buy an asset from that pool at a deliberately disadvantageous price, selecting a permissive minimum output.
3. The acquired asset lands in a treasury-owned account. Every recipient condition holds.
4. The attacker withdraws or trades out the valuable quote deposited into their pool using their own external LP position.

The same attack can use established assets and a thin manipulated pool; it does not require a fake mint or a DEX bug. Repeated unfavorable trades and self-directed fees are additional extraction channels. There is no on-chain bound on fair value, cumulative loss, exposure, or the caller's economic relationship with counterparties.

**Impact:** essentially all economically valuable capital may be extracted while satisfying the stated structural rule. “Can at worst trade badly” includes intentionally trading badly against oneself.

**Required decision:** either describe a discretionary managed treasury whose trader is trusted against malicious execution, or constrain markets/assets, independently derive execution bounds, limit exposure/loss, and specify governance for changing those constraints. Caller-chosen slippage, an oracle for only one leg, or a trusted venue name is insufficient. There is no general price-safety guarantee for arbitrary newly created assets.

### A2. The signer exception permits redirected swap outputs

**Architecture:** §5.1, line 136. **Code:** `programs/condor_vaults/src/venues.rs:93`; `instructions/execute.rs:80`. **Evidence:** directly reproduced in the Rust checker.

`if account.is_signer { continue; }` precedes token-account ownership checks. It is not restricted to the designated caller, a system account, or a paying account.

A normal SPL token account can be created at a fresh keypair address. An attacker keeps that address's key, uses the account as `user_token_out`, marks it signer, and signs the outer transaction with it. The wrapper preserves the signer privilege. The exact externally owned token account that was rejected when unsigned is accepted when signed; no postcheck examines it.

This targets the same mint-only output validation the document relies on for its honest/redirected DLMM comparison. That fork test tested only one unsigned recipient shape. Solana distinguishes a token account's address from the owner authority stored inside it; see [token-account creation](https://solana.com/docs/tokens/basics/create-token-account).

**Fix:** classify token accounts before considering signer status. A signature never establishes who benefits from an account. Explicitly identify allowed payers and verify their net lamport behavior. Do not exempt additional transaction signers from ownership checks.

### A3. Private approvals survive tokenization and bypass the program entirely

**Architecture:** §§4–6. **Code:** `instructions/execute.rs:97`; `instructions/tokenize.rs:123`; `token.rs` account reader. **Evidence:** source-confirmed authority path; the checker also accepts a treasury-owned account containing an external SPL delegate and unlimited allowance.

Attack:

1. While private, use `execute_unchecked` to approve an external SPL delegate on the treasury's quote ATA. A large allowance can be installed before the future seed arrives.
2. Tokenize. Nothing inspects or clears that approval.
3. Collect seed or receive other quote deposits.
4. The external key invokes SPL Token directly to transfer the approved balance away. Neither `execute` nor its recipient policy runs.

Replacing `Vault.delegate` does not revoke an SPL token-account delegate. Close authorities, venue operators, fee beneficiaries, and other durable permissions are also separate state that unrestricted private execution can establish. The treasury's claimed system-account invariant also needs checking after unrestricted operations.

[Solana's approval documentation](https://solana.com/docs/tokens/basics/approve-delegate) explicitly describes transfer/burn authority stored on the token account.

**Fix:** define a verifiable transition that removes every external claim over admitted assets and positions. A fresh tokenized treasury with controlled migration of admitted assets is easier to reason about than sanitizing a wallet that previously invoked arbitrary programs. Checking a caller-supplied subset of accounts cannot establish that no other permission exists.

### A4. Redemption accepts a substituted pool account and can overpay a holder

**Architecture:** §7, lines 241–247. **Code:** `instructions/redeem.rs:111` and `:145`. **Evidence:** source-confirmed account substitution; not executed on chain.

The pool token account is checked only for its mint. Its address, pool identity, authority, and distinction from retained supply are not checked.

For supply 1,000, retained balance 400 and genuine pool balance 100, circulating supply should be 500. Passing the retained token account again as the pool makes the denominator 200. Burning 100 then receives half the pot rather than one fifth. Every implemented mint/owner check can still pass.

**Fix:** derive the actual pool and reserve from verified launch/migration state and the selected fee option. Reject account aliasing. Define the reserve used before migration, after migration, and when there is no migrated pool. The Gateway builder requiring a pool account from its caller is not a substitute for an on-chain proof.

## High-severity findings

### A5. The recipient rule does not control who owns LP claims or future fee rights

**Architecture:** §5.1, lines 138–140. **Code:** `venues.rs:69`, `:103`, `:121`. **Evidence:** checker reproductions plus venue SDK/IDL; individual venue attack sequences need fork validation.

The policy admits every instruction of an allowed venue without decoding the operation. Any existing venue-owned account is accepted. Only some newly created accounts receive postchecks.

That leaves several distinct problems:

- A pre-existing position with another beneficiary passes the Condor checks. A venue's legitimate “fund this position” capability is not automatically a vault-safe operation.
- `update_position_operator` is accepted: the writable position is venue-owned and the new authority is encoded in instruction data, which Condor does not inspect. The harness confirms this policy acceptance. This creates an external capability whose exact limits must be audited, not assumed.
- DLMM operator-managed positions have a separate `feeOwner` and `lockReleasePoint`. Checking the position's principal owner alone does not establish who receives its income or when it can exit.
- A fresh position that signs is excluded from `fresh`, so its beneficiary is never checked after creation.
- A zero-lamport system destination recorded as fresh may finish funded and still pass: `check_after` accepts any account that remains system-owned without rechecking its balance.

Meteora's [SDK documents separate owner, operator, fee owner and lock-release fields](https://raw.githubusercontent.com/MeteoraAg/dlmm-sdk/main/ts-client/src/dlmm/index.ts). It also says operator withdrawals are constrained to the position owner: **do not equate installing a DLMM operator alone with a demonstrated arbitrary withdrawal**. The confirmed defect is that Condor does not enforce the authority and beneficiary policy at all.

There is an opposite availability problem: the postcheck treats every fresh DLMM-owned account as if it had a PositionV2 owner at bytes 40–72, without checking the discriminator. New bin arrays and other legitimate venue account types do not satisfy that assumption. “Every connector works unchanged” is not established by one swap.

**Fix:** implement operation-specific adapters for supported swaps, opens, increases, decreases, claims and closes. Bind pool, mints, position owner, NFT ownership, fee recipient, operators, lock state and destinations. Snapshot and verify relevant existing accounts after execution too. Admit new instruction types explicitly.

### A6. Wind-down disables the instruction required to complete wind-down

**Architecture:** §5 says execute requires Running/Paused; §7 requires execute after WindingDown. **Code:** `instructions/execute.rs:63`; `instructions/wind_down.rs:77`; `condor/vaults_crank.py` `_wind_down`. **Evidence:** explicit document contradiction and matching code.

After `wind_down`, every close, swap, wrapping operation and other conversion through `execute` fails `VaultNotActive`. The transition is irreversible. The crank stops the engine and attempts finalization; it does not perform conversion.

Merely admitting all `execute` operations in WindingDown would let the same actor open new exposure or continually undo liquidation. Specify a liquidation-only capability: allowed closes/claims, bounded conversions into quote, and no new LP or directional exposure.

The Paused state has the reverse ambiguity: it permits all execution, including new positions. If pause only disables Condor's agent, call it that; if it means “take nothing new,” enforce the distinction on chain.

### A7. Finalization relies on administrator honesty and does not prove liquidation

**Architecture:** §7.3 and §9. **Code:** `instructions/wind_down.rs:130`; `state.rs` `WIND_DOWN_DUST`; Gateway `vault.routes.ts` finalize route. **Evidence:** source-confirmed.

- A malicious administrator can omit all non-quote accounts, provide a pot with one quote unit, and finalize. Signing authority proves identity, not inventory completeness.
- An NFT amount of 1 passes the 1,000-unit threshold, even with an honest complete token-account list.
- DLMM positions are venue-owned state, not token balances. The token-account reader skips them.
- Native SOL is not checked or wrapped by finalization.
- A fixed raw-unit threshold is not a value bound across different decimals and prices.

The result can be terminally locked positions or principal. Conversely, if completeness is enforced off chain, anyone can airdrop more than 1,000 units of an untradeable token and grief an “all non-quote balances” exit. Those two cases require a definition of admitted assets/positions, not a longer account list.

The administrator can also refuse to finalize forever. Protocol-authority rotation is a recovery dependency, not removal of administrator trust. If the creator disappears after installing a delegate unavailable to Condor, the protocol authority can enter WindingDown but still lacks conversion authority. Its power to initiate a stop is not a complete abandoned-vault recovery path.

**Fix:** track admitted assets and positions, their outstanding liabilities and closing state; separate unsolicited assets from protocol-accounted capital; explicitly close position claims; account for native SOL and quote outside the canonical ATA; define zero-value insolvency and administrator recovery. Until then disclose administrator honesty and availability as trust assumptions.

### A8. Launch allocations and permanent-lock guarantees are not enforced

**Architecture:** §§6–6.1. **Code:** `instructions/tokenize.rs:132`, `:234`; `dbc.rs:131`; Gateway `launch-config.ts`. **Evidence:** source-confirmed.

`issue_bps` is checked for range and recorded, but never related to the curve's actual sold or retained supply. The config builder does not receive it and sets `leftover: 0`. It therefore cannot support a statement that an arbitrary requested issuance percentage determines the retained allocation.

The checked config fields do not include permanent-lock percentages or the immutable-authority option. They check a fixed-supply flag, not the complete supply/authority/vesting constraints promised by the architecture. A creator may supply another config; safe builder defaults are not enforcement.

“Ceiling on later dilution” also needs precise units: issuing fraction f and later circulating all remaining supply increases circulation by up to `(1-f)/f` relative to the initial circulation, not merely `1-f`. LP reserves, token burns and the actual sale allocation must be distinguished.

**Fix:** define exact raw-unit allocation equations and validate them from the on-chain config. Require the exact locked-liquidity and authority terms independently of Gateway. Add tampered-config tests for each field.

### A9. `claim_position_fee` can redirect strategy-owned position income to the creator

**Architecture:** §6.1 promises strategy LP fees belong to the vault and only migration-position fees belong to the creator. **Code:** `instructions/income.rs:274`. **Evidence:** source-confirmed missing position provenance; actual CPI behavior needs a fork test.

The handler accepts a treasury-owned position NFT and verifies only that the mint pair is the vault token and quote asset. It does not bind the position to the specific locked position created by migration or verify its lock/provenance. A strategy position on the same pair satisfies those checks and its fees can be paid to the creator's ATAs through this instruction, bypassing `execute`.

**Fix:** identify the exact migrated creator position through a canonical derivation or stored verified identity. Route all other position fees to treasury. Every privileged non-execute instruction must be included in the recipient policy's threat analysis.

### A10. Redemption is allowed before all claimable capital is collected

**Architecture:** §§6.1, 7. **Code:** `collect_seed.rs`, `retained.rs`, `wind_down.rs`. **Evidence:** source-confirmed missing ordering; economic consequence follows from the payout formula.

Finalization requires only a non-empty quote ATA. It does not require graduation, seed collection, retained collection, or settlement of outstanding venue claims. Collectors do not refuse Redeemable state.

Consider two equal holders and an existing 2 SOL pot with another 8 SOL migration fee still claimable. The first holder redeems half the supply for 1 SOL; collection then adds 8 SOL; the remaining holder redeems for 9 SOL. Both transactions follow the code, but payout order decides entitlement to capital already economically owed to the vault.

An unfilled curve is another unresolved case: buyers' reserve is still in DBC, the launch may never graduate, yet wind_down is already permitted. Define what happens to that reserve, retained supply, later migration and future claims.

**Fix:** require settlement before opening redemption, or retain a separate claim mechanism for later proceeds. Specify both pre-graduation wind-down and post-finalization late inflows. Do not promise order-independent pro-rata liquidation while excluding outstanding receivables.

## Additional architecture and implementation gaps

### A11. USDC launch economics and migration liveness are undefined — High

“10 wSOL-equivalent” does not define a USDC integer threshold without an exchange-rate source, observation time and decimal convention. The program compares every quote to `10_000_000_000` raw units; that is 10 wSOL but 10,000 units of a six-decimal quote. The builder fixes nine quote decimals and derives the threshold from market caps rather than the declared constant. Quote mint validation checks agreement with the config, not membership in {wSOL, USDC}.

Meteora's [DBC SDK README](https://raw.githubusercontent.com/MeteoraAg/dynamic-bonding-curve-sdk/main/packages/dynamic-bonding-curve/README.md) describes migration based on config and reaching its quote threshold. It is not evidence for an automatic keeper guarantee for every new per-vault config and every quote mint. The document needs an explicit supported quote policy, exact thresholds and a verified migration service/fallback obligation.

### A12. The trust base includes external programs and asset authorities — High disclosure issue

The program signs CPI calls into other programs. If an allowed venue is upgradeable, pinning its address does not pin its behavior. Venue bugs, upgrades and fee/config administrators remain relevant. Condor's multisig does not govern those authorities. This review did not query the current deployed upgrade authorities; their identities and immutability must be established rather than assumed. [Solana documents program upgrades retaining the program address](https://solana.com/docs/programs/deploying).

Likewise, a mint's freeze or permanent-delegate authority is independent of treasury ownership. A Token-2022 permanent delegate can transfer or burn tokens in accounts holding that mint; see [Solana's extension documentation](https://solana.com/docs/tokens/extensions/permanent-delegate). Transfer hooks and transfer fees also need an explicit asset-admission policy. “No key can move any treasury token” cannot hold for arbitrary assets with external authorities.

A private creator who installs another party's unrestricted delegate trusts that delegate as well as the program upgrade authority. “Trusts nobody” is inaccurate even before tokenization.

### A13. A config hash is not enforcement of trading intent — High disclosure issue

`execute` never checks the strategy hash or whether an instruction was generated by the committed strategy. Anyone with creator/delegate authority can submit any allowed operation. A malicious Condor operator can therefore use different parameters despite a correct hash commitment.

The honest crank's hash check is useful integrity protection, but its security statement is narrower: it refuses accidental/stale config mismatches when it follows its code. Additionally, create still supplies zero repoHash/commit and the engine loads current local strategy files by slug. This does not implement a public code pin by commit.

### A14. Gas replenishment contradicts the tokenized recipient rule — Medium

The crank tops up the delegate from treasury. Gateway's `fund-delegate` constructs a System transfer to the delegate key; tokenized wrapping routes this through `execute`, whose System policy permits only transfers into treasury-owned token accounts. The top-up therefore fails after tokenization. Replacing a delegate may also leave it unfunded.

Either fund operational gas externally or define tightly bounded, accountable reimbursement as an explicit exception to the no-external-payment rule. A general signer exemption is not that policy.

### A15. Other inherited integration gaps remain — Medium

- `_collect` expects `dbc_config`/`config`, but the decoded vault shape supplies neither; permissionless collection being possible does not mean the crank actually invokes it.
- Gateway derives the migrated pool using the default 100 bps fee config instead of the vault's selected fixed option, and exposes a derived address before proving migration.
- Condor's redeem build omits Gateway's required `poolTokenVault` field.
- The public quote/retained ATAs needed for redemption may be absent; ATA creation must be part of the lifecycle/build contract.
- The architecture uses `authority_bump` and `vault_authority` in places where the rewrite uses `treasury_bump` and `treasury`.

These are secondary to the custody design findings, but §11 correctly says the tokenized half has not been exercised; the opening “as implemented” must not imply that these promises have been demonstrated end to end.

## Recommended change to the architecture's claims

Replace the unconditional guarantee in §§1/9 with a statement along these lines, **after fixing the direct bypasses**:

> After tokenization, the program restricts direct asset transfers and permits specified trading operations. Creators and delegates remain trusted against malicious trading and adverse counterparties; these restrictions do not guarantee preservation of value. Holders also rely on supported venues and assets, their relevant authorities, the protocol upgrade authority, and the administrator's liquidation/finalization duties. Redemption becomes permissionless after the vault has reached a correctly finalized state.

If that is not the intended product, the remaining work is a change to the execution and liquidation architecture, not stronger wording around recipient checks.

The specification should state invariants separately for (1) token custody, (2) LP principal ownership, (3) fee/claim beneficiaries, (4) external spending authorities, (5) execution value bounds, and (6) exit availability. Account ownership alone proves none of the other five.

## Required adversarial acceptance tests

Before treating the tokenized protocol as implemented, run these against the deployed fork program and actual venue versions:

| Test | Required result |
|---|---|
| Swap output to a keypair-backed token account signing the transaction | Rejected despite its signature |
| Private Approve / close-authority / venue-permission setup, then tokenize | Unsafe inherited authority cannot survive |
| Existing foreign-owned position; fresh signing position; separate fee beneficiary | Principal and fee claims cannot escape treasury policy |
| Venue operator change and lock creation | Only expressly supported capabilities accepted |
| Controlled thin pool / self-LP counterparty / worthless asset | Rejected by independently defined value constraints, or explicitly disclosed as an accepted manager risk |
| Native SOL, noncanonical quote ATA, DLMM position and NFT position at finalize | Accounted for; no terminal stranded principal |
| Administrator omits accounts, sends a one-unit pot, or disappears | Specified rejection/recovery behavior |
| Junk-token donation before wind-down | Does not hold legitimate exits hostage |
| WindDown → close → swap → wrap → finalize | Actually executable; new exposure forbidden during liquidation |
| Forged pool reserve and pool/retained account alias | Rejected on chain |
| Two-holder redemption with late seed, late retained tokens and an unfilled curve | Defined, verified treatment of all outstanding claims |
| Custom config with wrong locks, authorities, allocation, quote or threshold | Rejected before launch |
| Claim fees from a strategy-funded position on the vault's own pair | Paid to treasury, never misclassified as creator income |
| Delegate out of gas; non-default migration fee option; six-decimal quote | Working and correctly bounded operational paths |

The six local reproductions establish that the current structural checker is too permissive. They do not replace this lifecycle and economic testing.

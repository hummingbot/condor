# Condor Vaults 2 implementation review

Reviewed September 18, 2026 against `docs/plans/CONDOR_VAULTS_2_PLAN.md`, the current Condor working tree, local Gateway source at `/Users/feng/fengtality/gateway`, and the app at `http://127.0.0.1:8088`.

Verdict: the private-vault browsing and wallet infrastructure exists, but the tokenized lifecycle does not yet meet the plan. There are fund-safety defects and several disconnected execution paths. M3/M6 should not be considered complete.

The working tree and HEAD changed during this review; the final observed HEAD was `5badb03d`. Existing edits were left untouched. No trades, signatures, withdrawals, lifecycle changes, deployments, or exploit transactions were submitted. Source findings below describe reachable code paths; exploit outcomes were not executed on chain.

## Highest-priority findings

### 1. Tokenization preserves a runner-controlled private delegate — P1, fund safety

Source: `programs/condor_vaults/src/instructions/tokenize.rs:123`, `instructions/install_delegate.rs:90`.

Private runners can install their own delegate. Tokenize checks the pin and launch terms, but neither requires administrator approval of that existing delegate nor replaces it. The administrator co-signature only applies to later delegate installs. A runner can install a key they own while private, tokenize, and retain unrestricted spending access after holders fund the vault. Gateway's build-tokenize also builds only the tokenize instruction. This defeats the plan's central transition guarantee.

Require an approved custody transition at tokenization, enforced by the program, including verification/replacement of the existing Swig role. An HTTP restriction cannot enforce this.

### 2. Redemption accepts an arbitrary same-mint account as the pool balance — P1, fund safety

Source: `programs/condor_vaults/src/instructions/redeem.rs:119` and `:153`.

`pool_token_vault` is only checked for a matching mint, not its pool address or authority. Its balance is then subtracted from supply. A caller can substitute another holder account or even the treasury account, which can be counted twice. Example: supply 1,000, treasury 400, real pool balance 100. The correct denominator is 500; supplying treasury as the pool makes it 200, so burning 100 pays half the pot instead of one fifth. No account-distinctness check rejects this.

Derive and validate the actual DBC/DAMM pool token vault before using its balance, with the correct migration phase and fee configuration.

### 3. Private wind-down strands native SOL; reproduced in the running app — P1, fund safety

Source: `programs/condor_vaults/src/instructions/wind_down.rs:141`, Gateway `src/vaults/vault.routes.ts:1010`, and the crank's `_wind_down`.

Finalize checks SPL accounts but not the funds-owner's native SOL. Gateway deliberately leaves private native SOL in the wallet, then the crank finalizes and removes the delegate. `install_delegate` refuses Redeemable vaults; `redeem` refuses private vaults. The remaining SOL has no exit under the current instructions.

Live read-only holdings on the surfpool fork:

| Vault | State | Delegate | Native SOL remaining |
|---|---|---|---:|
| `4qem91MZwCVeDUmd94pjWTaCq6USjcAoik8Wnv7V4H45` | Redeemable/private | none | 2.00089088 |
| `8P869pd3yEqTvR1PfbvRXvtxj5kJpdCu56HM6CpKjqnH` | Redeemable/private | none | 1.00089088 |
| `F3UU5dD9krSUQzJYB4kqa4mKG4UpR4ycV4jqD5sX4KRi` | Redeemable/private | none | 2.50089088 |

Total: **5.50267264 SOL**, on the fork. Finalization must wait for a safe private exit, including native balances, before revoking the only spending authority. The current UI still describes these vaults as freely withdrawable.

### 4. An open position NFT passes the finalize dust check — P1, fund safety

Source: `programs/condor_vaults/src/instructions/wind_down.rs:151`; `state.rs:94`.

The code comments say the position NFT stops finalization, but the actual check accepts any token amount <= `WIND_DOWN_DUST`, which is 1,000 raw units. An NFT balance of 1 passes. Even a complete account list can therefore finalize while an LP position remains, removing the delegate needed to close it. Position detection must be explicit; a fungible dust threshold cannot cover NFTs.

### 5. Wind-down never closes positions or converts non-quote assets — P1

Source: `condor/vaults_crank.py:327`; `condor/agents/engine.py:268`.

The crank stops the engine, calls sweep-to-pot, then finalize. Engine.stop stops the agent task; it does not close executors or liquidate assets. Gateway's sweep-to-pot moves quote only. No close, conversion, or closing-fee sweep exists in this path. Vaults with non-quote balances stay stuck; position NFTs may instead slip through finding 4. Implement and verify the complete close → accrue/burn fees → convert → sweep → finalize sequence.

## Launch and redemption integration

### 6. Default launch config violates the mandatory 10 SOL threshold — P1, reproduced locally

Source: Gateway `src/vaults/launch-config.ts:115`; Condor `frontend/src/pages/VaultDetail.tsx` launch defaults; program `instructions/tokenize.rs:240`.

The builder declares `MIGRATION_QUOTE_THRESHOLD_SOL = 10` but never uses it. `buildCurveWithMarketCap` derives the threshold from the supplied market caps. Executing the actual installed SDK with the builder's exact parameters produced:

- start 10, end 100, migration fee 50%: **27,305,411,899 lamports**;
- start 20, end 200, migration fee 50%: **54,610,823,798 lamports**.

The program requires 10,000,000,000. Therefore the default config may be created successfully but its subsequent tokenize instruction fails `LaunchTermsMismatch`. Constrain/solve the curve economics around the fixed threshold and verify the encoded result before asking for a signature.

### 7. `issue_bps` does not control the sale or treasury allocation — P1

Source: `programs/condor_vaults/src/instructions/tokenize.rs:132`, `:195`; Gateway `src/vaults/launch-config.ts:124`.

The program range-checks and stores `issue_bps`, but does not bind it to the DBC curve or supply allocation. The config builder has no issuance input and sets `leftover: 0`. Changing the UI's issue percentage changes a displayed record field without changing the curve. The promised retained fraction is not enforced. The allocation must be part of config construction and validated against `issue_bps` on chain.

### 8. Launch terms omit the permanent liquidity lock and other promised constants — P1

Source: `programs/condor_vaults/src/instructions/tokenize.rs:234`; `dbc.rs:131`.

The on-chain parser/check does not read or verify partner/creator permanent-lock percentages, token decimals, immutable-authority option, exact billion-token supply, or required curve fee. It also compares config quote to the supplied quote account, without binding it to `vault.quote_mint` or requiring wrapped SOL. Gateway chooses intended defaults, but permissionless callers can provide their own DBC config. In particular, the permanent-lock guarantee cannot rely on the UI builder. Validate every promised constant from the actual config.

### 9. The holder's redeem build omits a required Gateway field — P1

Source: `condor/web/routes/vaults.py:773`; Gateway `src/vaults/schemas.ts:214`, `vault.routes.ts:717`.

Condor sends walletAddress, swigAccount and amount. Gateway requires `poolTokenVault` and uses it without deriving a default. Requests from the Redeem card therefore fail schema validation before building a transaction. Derive the canonical pool vault server-side and validate it on chain as in finding 2. Also ensure required holder/treasury ATAs exist; the current builder only adds the redeem instruction.

### 10. Migrated pool derivation ignores the selected fee option — P1

Source: Gateway `src/vaults/vault-program.ts:169`, `:296`.

`vaultDammPool` always uses the 100 bps config even though tokenization accepts and records other fixed options. Those vaults get the wrong pool address. Moreover, `dammPool` is derived as soon as a mint exists, without confirming migration or pool existence. Use the vault's actual fee option and explicit migration status.

## Crank and strategy commitment

### 11. Seed and leftover collection are never reached with the decoded vault shape — P1, reproduced locally

Source: `condor/vaults_crank.py:310`; Gateway `src/vaults/vault-program.ts:270`.

`_collect` returns unless `dbc_config` or `config` is present. Gateway's decoded Vault includes dbcPool but neither field. The stored launch config is not passed into this method. A mock using the real decoded fields produced **zero execute calls**. Resolve the config from the pool/chain and prove the post-migration seed and treasury reach the vault. The crank also contains no fork migration invocation despite the plan assigning migration to it on the fork.

### 12. Graduated sweeps still use the bonding curve — P1, reproduced locally

Source: `condor/vaults_crank.py:574`.

`pass_once` converts Gateway fields to snake_case. `sweep_once` checks only camelCase `dammPool`; therefore a `damm_pool` value never selects buy_on_market. A mock graduated vault produced **0 market calls, 1 curve call**. Fix both the naming and the phase/existence issue described in finding 10.

### 13. Failed buys lose accrual; failed burns are never retried — P1, partially reproduced locally

Source: `condor/vaults_crank.py:279`, `:577`, `:588`, `:609`.

The pending balance is removed before the buy. A caught buy failure does not restore it, and the executor is already marked accounted for. With 0.1 quote fees at 50%, a simulated transient failure left **pending=0**, and the next pass made **zero retries**. The documented crash tradeoff has become the behavior of every ordinary quote/simulation failure.

A failed burn has no outstanding-burn ledger or retry path either: subsequent calls burn only their newly purchased amount. `record_sweep` even records the bought quantity as `burned` before the burn happens. Persist transaction progress and reconcile ambiguous outcomes; retain confirmed-unspent accrual and purchased-but-unburned amounts separately.

### 14. Strategy code is not pinned to a public commit or scanned — P1

Source: `condor/web/routes/vaults.py:306`, `_agent_ref`, `_scan_config`; `condor/vaults_crank.py:448`.

Create pins all-zero repoHash and commit. The crank loads the current local AgentStore/StrategyStore by slug. The scan checks three config shapes, not the public agent folder. Therefore a local strategy edit can change the executed code without a new on-chain version or code review. Config hashing is implemented, but it is not the complete strategy commitment promised by the plan.

### 15. An existing engine can retain a superseded config — P1

Source: `condor/vaults_crank.py:436`.

`_ensure_engine` returns for any running engine without comparing its config/version with the now-validated record. If publish, confirm and scan complete between crank passes, the current record passes checks while the existing engine continues with its old config. Restart or reload when the signed execution identity changes, independently of whether a mismatch was observed during an intermediate pass.

## Running product vs plan

Read-only Chromium checks covered the vault listing; Running and Redeemable detail pages; Portfolio, Agent, Activity and Token tabs; New Vault; global Portfolio; DEX; Settings/Gateway; and a 390×844 viewport.

- **Working:** all six chain vaults list, phase/state/delegate are visible, missing signing wallets receive a connect prompt, holdings API reports the vault's own funds-owner, global Portfolio has a separate Vaults section, and Gateway shows **`surfpool fork · 127.0.0.1:8899`**.
- **P2 — wrong financial labels:** Summary labels `fee_bps` **“Manager fee on profits”** and `issue_bps` **“Issued to the manager”** (`VaultDetail.tsx:372`). The plan defines them as LP fee buyback/burn share and circulating/max supply at launch. These labels describe materially different economics.
- **P2 — Activity is a placeholder:** `VaultDetail.tsx:564` renders a promise about runs/positions/sweeps/burns and a created timestamp, with no history query or rows. Verified in the running page.
- **P2 — token facts gated behind a signing wallet:** Agent and Token are both inside WalletGate (`VaultDetail.tsx:238`), including the tokenized read-only mint/pool/trade view. Public facts should remain visible, with signing gates on actions.
- **P2 — missing planned metrics/actions:** no vault NAV-per-token, market-cap comparison, trailing burn yield, runner income claim control, or delegate replacement control is implemented in the detail page. A total wallet valuation, where available, is not the specified NAV calculation.
- **P2 — private Redeemable actions:** the Redeem card condition is `finished && canSign`, without checking tokenization (`VaultDetail.tsx:223`); Withdraw is shown for any private vault even after its delegate has been removed. These controls cannot succeed in those states.
- **P2 — mobile overflow:** a 390px viewport has `document.documentElement.scrollWidth = 1099`; the global header remains a desktop-wide row (`components/layout/AppShell.tsx:106`). Screenshot: `/tmp/condor-vault-mobile-review.png`.
- **Plan drift:** the current UI uses five tabs (Summary, Portfolio, Activity, Agent, Token), rather than the four documented tabs. This is primarily a documentation/design discrepancy, unlike the functional defects above.
- No tokenized vault existed in the six live rows. Token purchase, graduation, treasury LP, burn and two-holder redemption were not validated end-to-end in this review. No wallet was connected or attached by the review.

## Validation

- `pytest -q tests/test_vault_config.py tests/test_vault_sweep.py tests/test_vaults_crank.py tests/test_web_vaults_listing.py`: **37 passed**.
- `npm test -- src/lib/vault/canonical.test.ts`: **6 passed**.
- Local SDK computation reproduced the invalid launch threshold.
- Isolated mocks reproduced missing collections, wrong sweep venue, and lost retry accrual. Script: `/tmp/condor_vault_logic_review.py`.
- Live authenticated read-only holdings confirmed the finalized private balances in finding 3.

These tests cover useful local invariants but do not establish the lifecycle guarantees. The plan's behavioral acceptance checks still need a complete fork run, including adversarial tokenization/redemption/finalization cases, two concurrent vaults, treasury LP, and two-holder redemption.

The plan itself retains stale text: the opening fixed-80% summary conflicts with the 20–80%/50%-default design, and M4's verification still describes three signatures/resumable steps despite D5's single transaction. Update those while recording the actual completion status.

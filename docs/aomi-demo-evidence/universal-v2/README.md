# Universal execution V2 — verified UI flows

All three venues completed entry and exit through Condor, Aomi preparation and the same Hummingbot executor. Six independently fetched local-chain receipts match the UI confirmations, reviewed transaction plans and final wallet balances.

| Venue | Entry | Exit | Final position |
| --- | --- | --- | --- |
| Jupiter Lend | 2 USDC | 1.999998 USDC | Zero shares |
| Kamino Earn — Steakhouse USDC | 2 USDC | 1.998991 USDC | Zero staked and unstaked shares |
| PumpSwap SOL/USDC | 2 USDC + 0.019627079 SOL | 1.999999 USDC + 0.019627078 SOL before network fee | Zero LP tokens |

Each transaction paid a 0.000005 SOL network fee. Account creation also required funding; some was refunded during withdrawal. Final balances reconcile exactly to all six receipts: 99.998988 USDC and 9.988562559 SOL, from 100 USDC and 10 SOL. These small test allocations demonstrate execution and recovery, not profit.

The UI also demonstrated a spending-limit refusal before submission. Incomplete evidence prevents confirmation. Unknown submission outcomes now remain explicitly unconfirmed and warn against retrying until reconciled.

These were real transactions on an isolated local Solana mirror using a substituted test wallet. Aomi uses explicit official-SDK protocol recipes. The demonstrated benefit is avoiding a dedicated Hummingbot connector for each venue; this does not prove automatic support for arbitrary new protocols or production wallet-provider signing. The selected Kamino vault dates from April 2025.

## Evidence

- [Combined receipt, executor and final-position reconciliation](verified-ui-round-trips.json)
- [Browser spending-limit and simulation proofs](ui-asset-budget-proof.json)
- [Jupiter entry](ui-jupiter-deposit-receipt.json) and [exit](ui-jupiter-withdraw-receipt.json)
- [Kamino entry](ui-kamino-deposit-receipt.json) and [exit](ui-kamino-withdraw-receipt.json)
- [PumpSwap entry](ui-pumpswap-deposit-receipt.json) and [exit](ui-pumpswap-withdraw-receipt.json)

## Shipping status

UI execution proof is complete. Code is published for review in [API #230](https://github.com/hummingbot/hummingbot-api/pull/230), [Condor #232](https://github.com/hummingbot/condor/pull/232), [backend #1070](https://github.com/aomi-labs/product-mono/pull/1070), and [SDK app #117](https://github.com/aomi-labs/aomi-sdk/pull/117). The Python client is published as [v0.1.3](https://github.com/aomi-labs/aomi-python/tree/v0.1.3). Final CI checks, hosted app activation and the new recording/video publication remain outstanding. This directory is execution evidence and a draft narration, not a completed video. Earlier failed environment/preflight attempts are preserved in the progress log and are excluded from this successful take.

## Narration and connector scope

[Draft narration V2](narration-v2.md) and [research](research.md). The [fresh connector check](connector-recheck.json) inspected upstream Gateway `f090f4ab7a8159b85fca2f3d4467970a5231cf5f` on 12 September 2026 (Asia/Shanghai). Jupiter Lend proposal #672 and PumpSwap request #570 remain open; PumpAmm PR #575 is closed without merge. Existing Jupiter swap routing is separate from lending and direct LP actions. These claims concern upstream connectors, not every custom integration or fork.

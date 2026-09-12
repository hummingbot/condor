# V2 recording verification — 12 September 2026

The 2:30 video shows real Condor UI actions through Aomi preparation/simulation and Hummingbot's shared onchain executor. Six local Solana transactions were independently verified, including their reviewed plan hashes, receipts, wallet movements, and final positions.

| Venue | Entry | Exit | Final position |
| --- | --- | --- | --- |
| Jupiter Lend | 2 USDC | 1.999997 USDC | Zero shares |
| Kamino Earn, Steakhouse USDC | 2 USDC | 1.998991 USDC | Zero staked and unstaked shares |
| PumpSwap SOL/USDC | 2 USDC + 0.019769713 SOL | 1.999999 USDC + 0.019769712 SOL before network fee | Zero LP shares |

Each transaction incurred a 5,000 lamport network fee. Account funding and refunds are separately reflected in receipt deltas. From 100 USDC and 10 SOL, final balances were 99.998987 USDC and 9.988562559 SOL. All six receipts exactly reconcile to those totals. A subsequent 2 USDC preview with a 1.9 USDC limit was refused, with confirmation disabled, commit_attempted=false and no transaction hashes; final balances remained unchanged.

## Scope and failures

This is a local mainnet mirror and an explicitly substituted test wallet. Aomi recipes use official SDKs. It is not hosted production activation, production Para signing, zero-shot arbitrary-protocol support, or an economically profitable allocation example. The selected vault dates from April 2025. Upstream connector claims are scoped to the inventory/research linked in the parent directory.

One initial Kamino preview failed with InvalidProgramExecutable before commit. Both program accounts and ELF hashes matched the local mirror and upstream on later inspection; a fresh preview passed without code or runtime change. Initial account prefetch data was not retained, so its precise cause remains unproven. See kamino-preview-diagnostic.md.

Several market/preparation reads failed because concurrent requests collided with Aomi's account operation admission. A controlled unsigned probe reproduced HTTP409 operation_in_flight alongside a successful concurrent read. The API now retries only this explicit pre-dispatch refusal with a bounded delay and returns a sanitized busy response on exhaustion. It never retries timeouts, 5xx responses, other conflicts, or transaction submissions. Condor no longer refreshes prepared market/catalog/position reads on focus or reconnect. Targeted validation: 23 Python tests, 6 UI tests, live concurrent reads passed, frontend production build passed. The refusal footage uses the corrected code. Failed attempts are preserved outside this published successful cut; no failed or ambiguous submission was automatically resubmitted.

## Artifact checks

- H.264, 1600×1200, 25fps, exactly 150 seconds; full decode completed without errors.
- No embedded audio; narration-v2.md is the supplied voiceover script.
- Intro/outro are editorial cards. Execution segments are actual UI capture; cuts and playback speeds are declared in markers.json. No synthetic UI or altered balances.
- Raw capture hashes and source/binary identities are in recording-manifest.json. Backend binary preceded the semantically equivalent Clippy-only change at8513f88c0; backend CI on8513f88c0 passed.
- Receipt/guard/position verification was rerun after the refusal and passed. This verifies an unchanged wallet, not merely a UI success message.
- The temporary recording browser was closed after capture. No personal Chrome window was closed.

The code remains in open PRs for review. Hosted app activation and production deployment are separate and were not performed.

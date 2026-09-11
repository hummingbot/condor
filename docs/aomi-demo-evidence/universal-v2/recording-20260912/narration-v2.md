# Narration V2 — Hummingbot × Aomi: universal execution

Voiceover script for the 2:30 V2 cut. The video contains no embedded audio. Execution footage is actual Condor UI capture from the verified local-chain take on 12 September 2026.

## 0:00–0:23 — The connector backlog

An on-chain opportunity can be live before your trading system knows how to use it.

Hummingbot's connector backlog makes that concrete: Jupiter Lend is proposed, Kamino is deferred, and PumpSwap liquidity support is requested.

Each venue brings another integration to build and maintain. Aomi's proposition is universal execution: one integration that lets Hummingbot agents act across protocols.

## 0:23–0:58 — Jupiter Lend

Start with Jupiter Lend. Hummingbot already supports Jupiter swaps. Supplying assets to Jupiter Lend is a separate operation.

In Condor, the operator asks to supply a bounded amount of USDC. Aomi reads the market, prepares the deposit, and simulates it. The preview shows wallet movements and the estimated network fee.

The operator confirms. Hummingbot tracks the executor through completion, and the resulting position is checked against on-chain state.

## 0:58–1:32 — A real Vault X

Now switch to a Kamino Earn vault.

Take Steakhouse USDC: a real curated vault with its own address, allocations, and fees. The operator selects the vault. Aomi reads its configuration and prepares the deposit through the same executor.

We check the shares received, then withdraw and verify the assets returned.

A different protocol and vault address, without adding a dedicated Kamino connector to Hummingbot.

## 1:32–2:02 — Another operation: PumpSwap liquidity

Next, PumpSwap. This time the operator wants to provide liquidity to a specific pool.

Aomi prepares the two-asset deposit, previews the amounts and LP tokens, and applies the operator's limits. After confirmation, we verify the liquidity position. Removing liquidity follows the same execution workflow.

Three venues. Lending, vault deposits, and liquidity provision. One Aomi Executor integration in Hummingbot.

## 2:02–2:30 — The value

Across these examples, Hummingbot manages the agent and executor lifecycle. Aomi reads protocol state, constructs the calls, and simulates them through a shared execution path.

The operator reviews the exact calls and sets wallet spending limits. If the simulation exceeds those limits, execution is refused before submission.

That is the value of universal execution: expanding what a Hummingbot agent can do without turning every new venue into another Hummingbot connector project.

## Recording notes

The video uses editorial introduction/end cards and actual UI captures with idle time removed and playback accelerated. It shows a local Solana mirror with an explicitly substituted test signer, not production Para signing. Aomi uses explicit official-SDK recipes; this does not demonstrate arbitrary-protocol support or replace Hummingbot market-data/strategy connectors.

All six receipts were fetched independently and reconciled to final balances. Jupiter returned 1.999997 USDC; Kamino returned 1.998991 USDC after its applicable withdrawal charge; PumpSwap returned 1.999999 USDC and 0.019769712 SOL before network fees. All three positions ended at zero. These small test allocations do not demonstrate profit.

Entry/exit footage uses API 1134e3c's parent ed827c0 and Condor ba9d8612's parent 7c2e8874. A subsequent preparation-only correction handles explicit operation-in-flight admission refusals and removes background focus refreshes; the spending-limit refusal was recorded with that correction. It does not change submitted protocol instructions. Failed read/preflight attempts are excluded from the edited cut and retained in the verification notes.

The 2 USDC action with a 1.9 USDC limit was refused before commit. The refusal did not change wallet balances. Network fees are distinct from account funding, protocol charges, and signer-provider charges.

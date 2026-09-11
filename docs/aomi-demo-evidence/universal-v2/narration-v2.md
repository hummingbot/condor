# Narration V2 — Hummingbot × Aomi: universal execution

Draft for a future recording, approximately 2:30. This is the intended demo, not a claim that the existing Aave recording demonstrates these venues. Timings are editorial targets.

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

## Recording requirements

- Every present-tense execution claim above requires new footage and independent chain-state verification before publication. Existing Aave footage is not evidence for these flows.
- Show the same Hummingbot executor implementation across all three venues. Aomi uses explicit protocol recipes built with official SDKs; this is not an absence of protocol-specific work. The claimed reduction is dedicated Hummingbot connectors per venue.
- Resolve actual market/vault/pool addresses and supported assets during preparation. Do not invent a named curated vault or claim a deployment is new without checking its creation history.
- The demonstrated Steakhouse USDC vault was created on April 2, 2025. Present it as an existing curated vault, not a new deployment. The preparation workflow accepts other valid market addresses within supported program families; this scene does not prove zero-shot support for an arbitrary newly written protocol.
- Show deposits and exits, share/LP accounting, and an over-limit refusal. Kamino farm-staked shares must be accounted for when applicable. Withdrawal availability and fees must be reflected.
- Label the actual network, fork or validator environment, and signer in the footage. Do not inherit the prior Base-fork label for Solana scenes.
- No guaranteed returns, immediate withdrawals, exclusive-simulation claim, or claim that universal transaction execution replaces all order-book, market-data, and strategy integrations.

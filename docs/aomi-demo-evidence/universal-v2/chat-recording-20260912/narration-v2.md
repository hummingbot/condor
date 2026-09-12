# Narration V2 — Hummingbot × Aomi, in chat (2:42)

## 0:00–0:08 — The selling point

What happens when a venue does not have a Hummingbot connector? In this demo,
the operator does not leave the agent chat and no venue-specific Condor screen
is added.

## 0:08–0:29 — One execution surface

The user asks for Jupiter Lend. Aomi discovers the supported protocol recipe,
reads the market and constructs the exact Solana instructions. Hummingbot keeps
the familiar executor lifecycle: preview, limits, human confirmation and
receipt. This demo adds one universal execution surface instead of three
venue-specific Condor connectors.

## 0:29–1:09 — Preview, confirm, verify

The agent previews a two-USDC Jupiter Lend supply on a local mirror. It shows
the expected USDC debit, lending shares, total SOL debit and network fee. All
three operator limits pass. The user confirms the reviewed plan, and only then
does the same prepared action move to execution.

The result is confirmed on the mirror: exactly two USDC leave the wallet,
1.887166 lending shares arrive, and the network fee is five thousand lamports.
The updated position is read back from chain. A separate receipt check confirms
the same transaction and amounts.

## 1:09–1:48 — The same workflow for Kamino Earn

Next the user asks about Kamino Earn. There is no new dashboard workflow and no
new Condor connector. Aomi inspects the Steakhouse USDC vault and prepares its
vault and farm instructions. The simulation shows two USDC entering, roughly
1.889587 shares returning, and the extra first-deposit account-funding cost.
The preview stays uncommitted because the user asked only to inspect it.

## 1:48–2:32 — The same workflow for PumpSwap

Then the user asks for PumpSwap liquidity. Aomi understands that this is a
two-asset pool action rather than lending. It calculates the proportional SOL
leg for a two-USDC quote contribution, constructs the pool instructions and
simulates the resulting LP shares. The agent surfaces the total wallet debit,
the fee ceiling and impermanent-loss risk before asking for approval. Again,
nothing is submitted because this request is preview-only.

## 2:32–2:42 — The counter-offer

That is the counter-offer to connector-by-connector integration: keep
Hummingbot's agent, approvals and executor controls, while Aomi supplies the
protocol-specific discovery, construction and simulation behind one reusable
execution interface.

## Recording notes

The footage is an actual Condor chat using Aomi Executor tools. Idle waits were
removed and the retained footage is shown at two-times speed. Jupiter execution
is verified by its local-chain receipt and resulting position. Kamino Earn and
PumpSwap are verified simulations and were intentionally not submitted. The
environment is a local Solana mirror with a test wallet; this does not
demonstrate production signing or live-network execution. No audio is embedded;
this file is the supplied voiceover script.

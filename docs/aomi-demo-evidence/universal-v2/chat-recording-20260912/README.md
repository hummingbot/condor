# Agentic chat V2 — Jupiter Lend, Kamino Earn and PumpSwap

This is the chat version of the Hummingbot × Aomi demo. It demonstrates one
agent-facing execution interface across three Solana venues, without adding a
venue-specific Condor dashboard or three venue-specific Condor connectors.

## Deliverables

- `hummingbot-aomi-chat-v2.mp4` — 2:42 shareable H.264 video, 1600×1000, silent
- `narration-v2.md` — timed voiceover script
- `evidence/` — allowlisted executor, receipt and fresh-mirror evidence

Video SHA-256:
`e4877dd5edb6c8b94ccabe8efece014c2ac54440f5f6d8c20add6013b0b6edfc`

## What is real

| Venue | Chat result | Submission | Verification |
|---|---|---:|---|
| Jupiter Lend | Supply 2 USDC | Yes | Confirmed receipt, exact 2,000,000 raw USDC debit, 1,887,166 raw shares received, 5,000-lamport fee |
| Kamino Earn | Deposit 2 USDC | No | Passing terminal simulation with fee and balance guards |
| PumpSwap | Add liquidity with 2 USDC plus proportional SOL | No | Passing terminal simulation with fee and balance guards |

All activity uses a local Solana mirror and a fork-only test wallet. The
recording does not claim production signing or live-network execution.

## Try it locally

The demo services and browser are intentionally left running. Open
`http://127.0.0.1:18092`, select **Aomi On-chain Trader**, and confirm the server
chip says **Solana V2 test mirror**.

The current mirror wallet has the confirmed Jupiter Lend position from the
recording. Useful prompts:

1. `Show my Jupiter Lend position on this local mirror and preview withdrawing all wallet shares. Do not execute.`
2. `Inspect Kamino Earn and preview depositing 2 USDC. Cap USDC at 2,000,000 raw, total native debit at 30,000,000 lamports, and network fee at 10,000 lamports. Do not execute.`
3. `Inspect PumpSwap and preview adding liquidity with 2 USDC as the quote amount. Cap USDC at 2,000,000 raw, total native debit at 30,000,000 lamports, and network fee at 10,000 lamports. Do not execute.`

For an execution, review the terminal simulation, type a clear confirmation in
chat, then use the green **Approve** button. Prepared plans are short-lived, so
confirm promptly; if one expires, review the newly prepared plan before
approving it.

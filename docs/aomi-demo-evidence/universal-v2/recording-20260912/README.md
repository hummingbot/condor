# Hummingbot × Aomi — universal execution V2

[Watch/download the 2:30 video](hummingbot-aomi-universal-v2.mp4) · [Narration](narration-v2.md) · [Verification and limitations](verification.md)

Three venues, six verified transactions through the same Aomi Executor integration: Jupiter Lend supply/withdraw, Kamino Earn vault entry/exit, and PumpSwap liquidity add/remove. All three positions ended at zero on the local Solana mirror. A 2 USDC action exceeded its 1.9 USDC limit and was refused before commit.

The video is silent; the voiceover script is supplied separately. Footage is actual Condor UI capture with editorial cards, idle time removed, and playback accelerated. It uses explicit Aomi official-SDK recipes and a substituted test signer; it does not demonstrate arbitrary protocols or production Para signing.

[Six-receipt reconciliation](verified-ui-round-trips.json) · [Spending-limit refusal](ui-asset-budget-proof.json) · [Edit markers](markers.json) · [Recording identities and hashes](recording-manifest.json)

The preparation concurrency correction was tested and pushed after entry/exit capture. The refusal was captured with the corrected UI and API. See the verification notes for earlier failed read/preflight attempts and the exact source versions.

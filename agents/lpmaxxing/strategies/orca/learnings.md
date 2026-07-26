# Orca LP Agent Learnings

Durable lessons belong here only after a completed dry run or live run proves them.

## Policy

- Record at most one or two actionable lessons per completed session.
- Do not store secrets, wallet keys, raw private balances, transaction dumps, or temporary emotions.
- Prefer lessons about Orca API field reliability, Gateway pool-info behavior, LP executor accounting, preset fit, and audit quality.

## Durable Lessons

No session-proven lessons yet.

## Execution Notes
- [2026-06-22 14:59] **Superseded history:** orca_pool_scan can return SOL-quoted candidates even when session notes request USDC-only pools. Live policy now requires canonical USDC as token B and gives notes no quote authority.
- [2026-06-22 15:16] **Superseded history:** Invalid SCAN_PROFILE values must be treated as no-trade and block scanning. That free-text input was removed; structured session `risk_profile` is now the sole live authority.
- [2026-06-22 15:21] **Superseded history:** Numeric NOTES that conflict with structured LP risk settings must block scanning and opening until clarified. Notes now have no authority over live profiles, gates, presets, ranges, or quote identity.
- [2026-06-22 16:21] Centered Orca LP opens can be blocked when direct `manage_gateway_swaps` is unavailable and the wallet holds only quote inventory; use `pre_lp_rebalance` through `manage_routines` before holding.
- [2026-06-23 05:24] A submitted pre_lp_rebalance swap may not appear in the immediate refreshed portfolio, so LP creation must wait for balance confirmation.
- [2026-06-23 10:36] Confirmed swap history is not spendable inventory; `pre_lp_rebalance` should use only current wallet base balance when deciding `no_swap_needed`.
- [2026-06-23 11:08] pre_lp_rebalance can recover a usable Jupiter quote with fallback_trading_pair after symbol routing returns unusable cost.
- [2026-06-23 11:14] **Superseded history:** If a candidate passed `SCAN_PROFILE` gates and `wide` is allowed live, do not invent a separate sub-$50k TVL or first-live-tick hold rule. The old authority and preset are retired; the durable rule remains not to invent extra gates after deterministic policy passes.
- [2026-06-24 07:33] send_notification fails with CONDOR_CHAT_ID not configured, so Telegram alerts are unavailable until chat routing is set.
- [2026-07-26 15:11] lp_position_report rejects session-level limit fields in call-time config; pass only supported per-position policy fields.

## Market Observations
- [2026-06-22 16:09] meme_scout can surface USDC-quoted meme pools with sub-$50k TVL despite strong 24h volume.
- [2026-06-24 07:25] **Superseded history:** risk_on_volatile with a USDC-only quote filter can still return no-trade when no Orca pool clears the scan hard gates and score cutoff. The old profile and score cutoff are retired; the durable observation is that a valid scan can honestly return no-trade.

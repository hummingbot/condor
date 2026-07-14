# Learnings

## Market Observations
- [2026-07-13 23:18] [trend_position] GeckoTerminal trending on Solana is currently dominated by pump.fun tokens (mints ending in "pump"). These ARE routable — do not filter them out. (Earlier ticks wrongly concluded they were unroutable; that was a Gateway bug, now fixed — see Execution Notes 23:35.)
- [2026-07-13 23:52] [trend_position] ANSEM (9cRCn...pump) closed at time_limit (600s) with ~0.0000 SOL realized PnL — entered a stable trend but momentum stalled; time-limit exit is the correct outcome for a flat position.
- [2026-07-14 00:04] [trend_position] FEBU (4ko5t...pump) closed ~0.0000 SOL realized — entered on strong m5 +14.3% / h1 +3.54% but momentum did not sustain through 600s; second consecutive time-limit flat exit suggests 600s may be too short to capture the move OR m5 spike was already exhausted at entry.
- [2026-07-14 00:52] [trend_position] ANSEM (9cRCn...pump) closed at time_limit a second time — consistently fails to reach TP within 600s despite reasonable h1 momentum; may be too large/stable a coin for 3% TP in 10 min window.
- [2026-07-14 00:54] [trend_position] FEBU (4ko5t...pump) closed 6th total at ~-4.5% uPnL — likely SL; despite strong h1/m5 rebound immediately after, re-entry excluded per stop-out rule. FEBU shows pattern of quick reversal post-close.
- [2026-07-14 00:58] [trend_position] LEVI (6baGyq...pump) reached TP (+3%) within 600s on its 2nd entry (tick #22), confirming sustained h1 momentum at +35% can support multiple sequential TP exits.

## Execution Notes
- [2026-07-13 23:35] [trend_position] Gateway NOW ROUTES pump.fun / Token-2022 mints via jupiter/router. The earlier "Token not found" (HTTP 400) was a Gateway bug: solana.getToken() called getMint() with the legacy TOKEN_PROGRAM_ID, which throws on Token-2022 mints (most modern pump.fun launches), so the mint resolved to null before Jupiter was ever called. Fixed by making getToken program-aware (reads the mint owner, passes TOKEN_2022_PROGRAM_ID when needed). Verified: SOL→ANSEM (9cRCn...pump) quotes fine. Enter pump.fun candidates normally — base_token = the mint address, connector omitted (default is jupiter/router).
- [2026-07-13 23:16] [trend_position] Wallet address for Condor/Gateway on this account: 82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5 (confirmed from all historical executors).
- [2026-07-13 23:16] [trend_position] Correct position executor schema: executor_type="position", config={chain_network, wallet_address, base_token (mint), quote_token, amount_quote, take_profit_pct, stop_loss_pct, time_limit_s, slippage_pct}. "position_executor" is NOT a valid type.

## Retired Insights

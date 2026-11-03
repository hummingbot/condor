---
name: diagnose_solana_token_decimal_mismatch
description: Find and explain why a Solana token shows the wrong amount or price in
  the portfolio
when_to_use: User says a token amount or value in the portfolio looks wrong — too
  large, too small, or inconsistent with what their wallet shows
created: '2026-09-01T20:33:20Z'
source: reflection
---

1. Fetch the portfolio overview and note the suspect token's raw amount and value_usd.
2. Call run_code to query the gateway for that token's metadata, especially `decimals`.
3. Divide the displayed amount by 10^(expected_decimals - actual_applied_decimals) to find the real amount.
4. Cross-check: real_amount × market_price should equal a plausible value_usd.
5. Compare the price the gateway/portfolio shows against an external source (Phantom, DEX UI).
6. Report: which field is wrong (amount, price, or both), the mismatch factor, and the likely root cause (wrong decimal config in the Solana connector or gateway token registry).
7. Offer to fix via gateway config update if the decimal is misconfigured.

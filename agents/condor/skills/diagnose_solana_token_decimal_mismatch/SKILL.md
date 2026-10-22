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
2. Read the token's registered `decimals` with
   `manage_gateway_config(resource_type="tokens", action="list", ...)` — the
   registry Gateway actually prices from — the listing reports `decimals` per
   token. A `run_code` snippet poking at gateway
   internals is not the route: there is no `GatewayRouter.get_tokens`, so the
   playbook's old step sent agents hunting for a method that does not exist
   (CO-5). **This tool is mounted on the full (server-owner) seat only** — if
   your seat does not have it, say so and ask the operator for the value rather
   than improvising another read.
3. Divide the displayed amount by 10^(expected_decimals - actual_applied_decimals) to find the real amount.
4. Cross-check: real_amount × market_price should equal a plausible value_usd.
5. Cross-check the PRICE against a source you can actually reach:
   `get_prices`, `explore_geckoterminal`, or `explore_dex_pools` for the pool
   itself. Do **not** plan on Phantom or a DEX web UI: this runtime has no
   browser, so that step could never be carried out and left the diagnosis
   hanging on evidence nobody could fetch (CO-5).
6. Report: which field is wrong (amount, price, or both), the mismatch factor, and the likely root cause (wrong decimal config in the Solana connector or gateway token registry).
7. A fix means editing the Gateway token registry, which is an **owner-seat
   write**. The tool's token actions are `add`, `delete` and `save` — there is
   no `update` (CO-5): correct a registered token by `add`-ing it again with the
   right `decimals` (same symbol and address), or `delete` then `add` if the
   backend rejects a duplicate, and `save` to persist. Offer it if you hold that
   seat; otherwise hand the operator the exact value to change and say which
   seat can change it.

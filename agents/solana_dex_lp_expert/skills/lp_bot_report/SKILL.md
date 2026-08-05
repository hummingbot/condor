---
name: lp_bot_report
description: Summarize LP strategy status — slots held, PnL, fees, exits, wallet rent
  headroom, and free capacity.
when_to_use: When the user asks for the status of the LP strategy, or to summarize
  a tick — slots held, PnL, fees, exits, and free capacity.
created: '2026-07-20T23:27:13Z'
source: agent:solana_dex_lp_expert
---

# LP Bot Report — status summary

Assemble a concise status of the LP slots.

## Gather
- `get_portfolio_overview(include_lp_positions=True)` → wallet balances + live LP positions (real-time fees, token amounts).
- `manage_executors(action="search", executor_types=["lp_executor"])` → running + recently closed slots.
- Optionally `search_history` for realized PnL of closed slots this session.

## Report format
Lead line: `Slots: <open>/<slots> open | quote=<quote_asset> | net PnL (session): <quote>`.

Per open slot:
`<pair> @ <venue> | state=<IN_RANGE/OUT_OF_RANGE> | range [lo–hi] (price P) | value=<quote> | fees=<quote> | uPnL=<±%>`

Then:
- **Exits this session:** pair, reason (TP/SL/abandoned), realized PnL, fees, duration.
- **Free slots:** count + top-ranked candidate waiting (from `pool_ranking`).
- **Wallet:** SOL free vs `min_wallet_sol_reserve` (rent headroom), quote available for new slots.
- **Flags:** any FAILED opens, thin-TVL warnings, or SOL-too-low-to-open conditions.

Keep it scannable — key: value lines, numbers in `quote_asset`.

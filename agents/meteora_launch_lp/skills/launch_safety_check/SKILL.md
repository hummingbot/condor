---
name: launch_safety_check
description: Gate a freshly-graduated Meteora DAMM v2 token before providing liquidity —
  sellability (honeypot), objective on-chain safety (mint/freeze authority, holder
  concentration, LP lock), and real post-graduation demand. Reject on any failure.
when_to_use: Before every early-LP entry on a launch graduation (and periodically on held
  launch positions). Run it after easya_graduation_monitor surfaces a candidate and before
  any add_liquidity.
created: '2026-08-05T00:00:00Z'
source: agent:meteora_launch_lp
---

# Launch Safety Check

Never LP a graduation that fails ANY gate below. Most launch tokens go to zero; the fee income has
to outrun IL + dump, and one honeypot or rug wipes the position. Reject fast and move on — there is
always another graduation.

## Gate order (cheapest / most-disqualifying first)

### 1. Sellability (honeypot) — do this FIRST, it's the killer
A token you can buy but can't sell is a total loss. Round-trip a quote through the pool:
- `manage_amm(action="quote_swap", connector="meteora", network="solana-mainnet-beta", pool_address=<pool>, base_token=<base_mint>, side="SELL", amount=<small>)`
- and `... side="BUY", amount=<small>`

Both must return a sane quote. If the **SELL** quote errors, reverts, or returns ~0 out / absurd price
impact → **honeypot, reject.** (A transfer-fee/tax token shows as a large one-directional impact — treat
high asymmetric impact as a red flag too.)

### 2. Objective on-chain + static gates — one routine call
```
manage_routines(action="run", name="launch_safety_check",
  agent="meteora_launch_lp",
  config={"pool_address": <pool>, "rpc_url": <private RPC>, "require_verified": false,
          "require_mint_renounced": true, "require_freeze_disabled": true,
          "max_top10_holder_pct": 60, "min_tvl_usd": 10000})
```
Returns a PASS/FAIL verdict over: **mint authority renounced** (no infinite dilution), **freeze
authority disabled** (can't freeze your tokens — a honeypot vector), **top-10 holder concentration**
(≤ 60% — else a whale can dump on you), **TVL floor**, and optionally **verified** / **LP lock**.
Any FAIL → reject. (Use a private `rpc_url`; the public endpoint rate-limits the holder query.)

### 3. Real post-graduation demand — don't LP the dump
The pump-graduate-**dump** pattern means early full-range LP eats the drawdown. So:
- **Skip the graduation candle.** Wait until the pool is past its first hours and price/volume have
  stabilized (`easya_graduation_monitor` reports `Age(h)`; prefer age past your `min_stabilize_hours`).
- Require **rising/steady 24h volume** and buys not one-sided sells (the monitor's `Vol24h` +
  `FeeYield`; a collapsing volume or a one-way sell wall → skip).

## Decision
Enter **only if all three pass**. On any failure, journal the reason (token, gate, value) and skip —
these rejections are signal for future picks. Re-run gate 1 (sellability) periodically on held
positions: if a token *becomes* unsellable, exit immediately at any price you can still get.

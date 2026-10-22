---
name: lp_range_config
description: Build a valid lp_executor config — side (1/2/3), base/quote amounts from
  base_pct, and bounds clamped to venue bin/tick width caps.
when_to_use: When constructing the exact LP Executor config for a slot — choosing
  side (1/2/3), base/quote amounts from base_pct, and lower/upper price bounds that
  respect the venue's bin/tick width cap.
created: '2026-07-20T23:26:59Z'
source: agent:solana_dex_lp_expert
---

# LP Range Config — side, amounts, bounds

Turn a chosen pool + `capital_per_slot` (in `quote_asset`) + `base_pct` into a valid `lp_executor` config. Current pool price = `P`.

## Side + amounts from base_pct
| base_pct | side | base_amount | quote_amount | range vs P | swap first? |
|---|---|---|---|---|---|
| `0` | `1` BUY | 0 | `capital` | **below** P | no |
| `100` | `2` SELL | acquired base | 0 | **above** P | yes: quote→base for full slot |
| `0<β<100` | `3` RANGE | base worth `capital·β/100` | `capital·(1−β/100)` | **asymmetric** around P — see below | swap the base shortfall only |

- Swaps use `swap_provider="jupiter/router"` (or an order_executor market buy of base).
- Always `keep_position=false` → exit swaps back to `quote_asset` so PnL/TP/SL are in quote terms.

## Bounds — width then CLAMP to venue cap
1. Half-width `w`: if `range_width_pct` set, use it; if `auto`, derive from OHLCV — e.g. `w ≈ k · ATR%` over `ranking_window` (k≈1–2). Tighter = denser fees but exits range sooner.
2. Provisional bounds:
   - RANGE (β middle): **place `P` asymmetrically, NOT centred.** Even (Spot)
     liquidity spreads uniformly across the range, so a centred range delivers a
     50/50 split whatever `base_pct` said. Give the **memecoin side `base_pct`%
     of the total width `W`** and the quote side the remainder. Which side that
     is **flips with the venue's price convention** — the loop playbook works
     this out per venue and is the authority on it; follow it rather than
     reasoning from scratch here (LP-9: this skill used to say "centered", which
     silently ignored `base_pct`).
   - BUY (β=0): `upper=P·(1−ε)`, `lower=P·(1−ε−2w)` (range below P).
   - SELL (β=100): `lower=P·(1+ε)`, `upper=P·(1+ε+2w)` (range above P).
3. **Clamp to the venue cap** (this prevents `SIMULATION_FAILED` / reallocate errors).
   Pull `bin_step` / `tick_spacing` from `get_pool_info` **per pool** — it varies
   pool to pool and it is what decides the cap:
   - **Meteora:** `bins = ln(Pu/Pl) / ln(1 + bin_step/10000)` must be **< 69**.
     Shrink to ~60 for headroom. `bin_step=4` ⇒ total width ≲ 2.7%.
   - **Orca / Raydium:** `spacings = ln(Pu/Pl) / (ln(1.0001) × tick_spacing)` must
     be **≤ 120**. Two opens at the *identical* 20.8% width landed on opposite
     sides of this purely on spacing: `tick_spacing=16` → 118 spacings, opened
     fine; `tick_spacing=8` → 237, `SIMULATION_FAILED` with no funds moved and
     the slot left empty.
   When the count exceeds the limit, shrink `W` until it fits — keeping `P`
   bracketed and the `base_pct` split intact — then open.
4. Meteora only: `extra_params={"strategyType":0}` (0=Spot uniform, 1=Curve concentrated, 2=Bid-Ask). Default Spot.

## Autonomous exit limits — always set both
`lower_limit_price` and `upper_limit_price` are **mandatory on every open**, placed
`autonomous_exit_margin_pct` outside the range. They live in the executor and fire
without a tick, which is what manages the slot when the loop is not running — and
they can close it **inside** your `out_of_range_max_sec` grace window if price
moves straight through one (LP-7). See the loop playbook for the exact expressions.

## Validate before create
- `capital_per_slot` ≥ venue minimum position size (else skip pool).
- `capital_per_slot` is bounded by **`min(total_amount_quote, usable balance) ÷ slots`** —
  the session's budget, not the wallet (LP-2).
- Enough SOL for rent (~0.057 SOL Meteora) + fees beyond `min_wallet_sol_reserve`.
  That reserve is **not enforced by the risk engine** — it is carried and reported
  as unenforced, so honouring it is this skill's job (LP-3).
- If open FAILS with reallocate/simulation error → range too wide → shrink bounds and retry once.

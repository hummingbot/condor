---
name: lp_range_config
description: Build a valid lp_executor config — side (1/2/3), base/quote amounts from
  base_pct, and bounds clamped to the venue's granularity cap (bin_step / tick_spacing).
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
| `0<β<100` | `3` RANGE | base worth `capital·β/100` | `capital·(1−β/100)` | **centered** on P | swap the base shortfall only |

- Swaps use `swap_provider="jupiter/router"` (or an order_executor market buy of base).
- Always `keep_position=false` → exit swaps back to `quote_asset` so PnL/TP/SL are in quote terms.

## Bounds — width, then CLAMP to the venue's granularity
1. Half-width `w`: if `range_width_pct` set, use it; if `auto`, derive from OHLCV — e.g. `w ≈ k · ATR%` over `ranking_window` (k≈1–2). Tighter = denser fees but exits range sooner.
2. Provisional bounds:
   - RANGE (β middle): `lower=P·(1−w)`, `upper=P·(1+w)`.
   - BUY (β=0): `upper=P·(1−ε)`, `lower=P·(1−ε−2w)` (range below P).
   - SELL (β=100): `lower=P·(1+ε)`, `upper=P·(1+ε+2w)` (range above P).
3. **CLAMP — `W_max` is a CEILING on the width, derived from the pool's granularity, and it BINDS.** Width in *percent* is meaningless on its own; only the bin/spacing count decides whether the open lands. Read `bin_step` (Meteora) / `tick_spacing` (Orca, Raydium) from `get_pool_info` **per pool** — never assume. Then require:

   `ln(upper/lower) ≤ ln((1+W_max)/(1−W_max))`

   and shrink the bounds proportionally about `P` if over — the band **stays on its side of `P`**: BUY entirely below, SELL entirely above, RANGE bracketing it. A one-sided band is never widened or shifted across `P`, and the `base_pct` split stays intact. The `ln` form is valid for the asymmetric `base_pct` placement above — `W_max` is the *symmetric-equivalent* half-width. `W_max` is computed **one unit under** the hard cap, so it always opens.

   - **Meteora** — `bins = ln(upper/lower) / ln(1+bin_step/10000)` must be **< 69**; table computed at **68 bins**, one bin under the cap:

| `bin_step` | 1 | 2 | 4 | 5 | 10 | 16 | 20 | 25 | 50 | 80 | 100 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **`W_max`** | 0.34% | 0.68% | 1.36% | 1.70% | 3.40% | 5.43% | 6.78% | 8.47% | 16.80% | 26.45% | 32.60% |

   - **Orca / Raydium** — `spacings = ln(upper/lower) / (ln(1.0001) × tick_spacing)` must be **≤ 120**; table computed at **119 spacings**, one spacing under the cap. The values are exact ceilings: read them as written, never round the last decimal up, or the clamp lands back on the cap:

| `tick_spacing` | 1 | 2 | 4 | 8 | 10 | 16 | 32 | 64 |
|---|---|---|---|---|---|---|---|---|
| **`W_max`** | 0.595% | 1.190% | 2.379% | 4.756% | 5.943% | 9.491% | 18.812% | 36.339% |

   - **Off-table granularity — solve it in one line, never guess a percent:** `r = (1+bin_step/10000)**68` (Meteora) or `r = 1.0001**(119×tick_spacing)` (Orca/Raydium), then `W_max = (r−1)/(r+1)`.
   - **A "4% floor" is NOT a floor when `W_max < 4%`** (Meteora `bin_step ≤ 10`, Orca/Raydium `tick_spacing ≤ 4` at a 20% target): such a pool physically cannot hold that band — narrow to `W_max` and open, or skip the pool. Widening past `W_max` fails the open.
4. Meteora only: `extra_params={"strategyType":0}` (0=Spot uniform, 1=Curve concentrated, 2=Bid-Ask). Default Spot.

## Validate before create
- `capital_per_slot` ≥ venue minimum position size (else skip pool).
- Enough SOL for rent (~0.057 SOL Meteora) + fees beyond `min_wallet_sol_reserve`.
- If open FAILS with reallocate/simulation error → range too wide → recheck `W_max` against the pool's `bin_step`/`tick_spacing`, shrink, retry once.

## Why this is table-driven
The old form ("shrink bounds until < ~60") was a follow-up step a tick could skip — and it did: an Orca open at 20.8% on `tick_spacing=8` = 237 spacings → `SIMULATION_FAILED`, slot left empty. On Meteora a `bin_step=10` pool caps `W_max` at 3.40%, *below* the usual 4% floor, so a percent-first clamp is guaranteed to over-reach. Live anchor (2026-09-22): NEAR-USDC `bin_step=20` → `W_max=6.78%`, band running 65/69 bins at W=6.39% (94% of the cap).

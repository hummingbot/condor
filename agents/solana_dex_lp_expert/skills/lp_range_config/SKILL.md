---
name: lp_range_config
description: Build a valid lp_executor config — side (1/2/3), base/quote amounts from
  base_pct, and bounds clamped to venue bin/tick width caps. Also covers the
  lp_rebalancer CONTROLLER, whose amount and width fields mean different things.
when_to_use: When constructing the exact LP Executor config for a slot — choosing
  side (1/2/3), base/quote amounts from base_pct, and lower/upper price bounds that
  respect the venue's bin/tick width cap. Also when configuring the lp_rebalancer
  controller, which takes one total instead of two amounts.
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

## Bounds — width then CLAMP to venue cap
1. Half-width `w`: if `range_width_pct` set, use it; if `auto`, derive from OHLCV — e.g. `w ≈ k · ATR%` over `ranking_window` (k≈1–2). Tighter = denser fees but exits range sooner.
2. Provisional bounds:
   - RANGE (β middle): `lower=P·(1−w)`, `upper=P·(1+w)`.
   - BUY (β=0): `upper=P·(1−ε)`, `lower=P·(1−ε−2w)` (range below P).
   - SELL (β=100): `lower=P·(1+ε)`, `upper=P·(1+ε+2w)` (range above P).
3. **Clamp to the venue cap** (this prevents `SIMULATION_FAILED` / reallocate errors):
   - **Meteora:** bins `≈ ln(upper/lower)/ln(1+bin_step/10000)` must be **< 69**. If over, shrink bounds until < ~60 (leave headroom). `bin_step=4` ⇒ total width ≲ 2.7%.
   - **Orca / Raydium:** width bounded by `tick_spacing`; smaller spacing ⇒ tighter cap. Pull `tick_spacing` from pool-info and keep the tick count within the connector's per-position limit.
4. Meteora only: `extra_params={"strategyType":0}` (0=Spot uniform, 1=Curve concentrated, 2=Bid-Ask). Default Spot.

---

## lp_rebalancer CONTROLLER — different fields, different meanings

The controller does **not** take `base_amount`/`quote_amount`. Do not carry the table
above into it. Verified against `lp_rebalancer.py` (`_calculate_amounts`,
`_calculate_price_bounds`).

### `total_amount_quote` is the WHOLE position, quote-denominated
It is the total value of both sides, and the controller does the split. It is always in
quote units even for the base side, which it converts at the current price `P`.

| side | base_amt | quote_amt |
|---|---|---|
| `3` RANGE | `(total/2) / P` | `total/2` — hard **50/50**, offset ignored |
| `1` BUY / `2` SELL, `offset ≥ 0` | single-sided: whichever side, gets the **full** `total` |
| `1` BUY / `2` SELL, `offset < 0` | proportional to where `P` sits in the range |

**To deploy a base holding of `B` tokens on RANGE, set `total_amount_quote = 2·B·P`** — half
of it funds the base side. Sizing it to `B·P` deploys only half your base.

### Two more fields that don't mean what the executor's do
- **`position_width_pct` is FULL width, not half.** RANGE bounds are
  `P·(1 ± width/200)`, so `5` gives ±2.5%. The `w` in this skill's Bounds section is a
  half-width. Still clamp to the venue bin cap — that math is unchanged.
- **`position_offset_pct` is ignored entirely when `side=3` RANGE.** It touches neither
  the amounts nor the bounds (RANGE is always centered on `P`). A small negative offset
  "to stay in range" is a no-op; RANGE is already in range. Offset only does anything on
  BUY/SELL, where its **sign selects the mode**: `≥ 0` single-sided out-of-range,
  `< 0` in-range needing both tokens.
- Default `position_width_pct` is `0.5` — that is 0.5%, i.e. ±0.25%. Very tight. Set it.

### Size to ~95% of a base holding, or enable autoswap
`base_amt` is recomputed from live `P` at open. Sizing `total_amount_quote` to exactly
`2·B·P` means any dip in `P` raises the base requirement above what you hold and the open
fails with `autoswap=false`. Leave headroom or set `autoswap=true`.

### Worked example — 78.678 ANSEM (P≈0.2623) + 33.85 USDC, pool bin_step 20
```
total_amount_quote  = 39            # -> 75.0 ANSEM + 19.50 USDC, ~5% base headroom
                                    #    (2*78.678*0.2623 = 41.3 would deploy all of it, no headroom)
side                = 3             # RANGE
position_width_pct  = 10            # -> +/-5%, = 50 bins at bin_step 20, under the 69 cap
position_offset_pct = <ignored on RANGE>
autoswap            = false         # both tokens already held
```

## Validate before create
- `capital_per_slot` ≥ venue minimum position size (else skip pool).
- Enough SOL for rent (~0.057 SOL Meteora) + fees beyond `min_wallet_sol_reserve`.
- If open FAILS with reallocate/simulation error → range too wide → shrink bounds and retry once.

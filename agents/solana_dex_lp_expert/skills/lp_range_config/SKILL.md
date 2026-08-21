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

## Validate before create
- `capital_per_slot` ≥ venue minimum position size (else skip pool).
- Enough SOL for rent (~0.057 SOL Meteora) + fees beyond `min_wallet_sol_reserve`.
- If open FAILS with reallocate/simulation error → range too wide → shrink bounds and retry once.

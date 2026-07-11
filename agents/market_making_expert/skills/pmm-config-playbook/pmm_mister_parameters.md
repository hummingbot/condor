# pmm_mister — full parameter reference (canonical)

Defaults verified against the live controller template
(`manage_controllers(action="describe"/config template)` on 2026-07-11).
This file is the ONE source for pmm_mister parameter knowledge — the old
copies in AGENT.md and strategy.md drifted (e.g. total_amount_quote 1000 vs
100) and were removed.

Every config MUST include `controller_type: "generic"` and
`controller_name: "pmm_mister"`.

## All parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| controller_type | str | "generic" | Always "generic" |
| controller_name | str | "pmm_mister" | Always "pmm_mister" |
| connector_name | str | — | Exchange connector; `_perpetual` suffix for futures |
| trading_pair | str | — | BASE-QUOTE; prefer liquid pairs |
| total_amount_quote | Decimal | 100 | Total quote capital for the controller |
| portfolio_allocation | Decimal | 0.1 | Fraction actively quoted per iteration |
| target_base_pct | Decimal | 0.5 | Target position as % of allocation |
| min_base_pct | Decimal | 0.3 | Below → only accumulate side quotes |
| max_base_pct | Decimal | 0.7 | Above → only reduce side quotes |
| buy_spreads | str | "0.0005" | Comma-separated bid offsets (0.001 = 0.1%) |
| sell_spreads | str | "0.0005" | Comma-separated ask offsets |
| buy_amounts_pct | str | "1" | Per-level weight ratios (match buy_spreads count) |
| sell_amounts_pct | str | "1" | Per-level weight ratios |
| executor_refresh_time | int | 30 | Seconds before replacing unfilled orders |
| buy_cooldown_time | int | 60 | Wait after a buy fill before a new buy |
| sell_cooldown_time | int | 60 | Wait after a sell fill before a new sell |
| buy_position_effectivization_time | int | 120 | Per-fill TP lifetime before hold mode |
| sell_position_effectivization_time | int | 120 | Same, sell side |
| price_distance_tolerance | Decimal | 0.0005 | Min gap between orders at a level |
| refresh_tolerance | Decimal | 0.0005 | Mid move required to refresh quotes |
| tolerance_scaling | Decimal | 1.2 | Tolerance multiplier per stacked executor |
| leverage | int | 20 | Perps only; keep LOW for MM (see guidance) |
| position_mode | str | "ONEWAY" | Always ONEWAY for pmm_mister |
| position_side | str | "BUY" | "BUY"/LONG accumulates longs; "SELL"/SHORT |
| take_profit | Decimal | 0.0001 | Per-fill TP offset — MUST clear fees (below) |
| take_profit_order_type | int | 3 | 1=MARKET, 2=LIMIT, 3=LIMIT_MAKER (keep 3) |
| open_order_type | int | 3 | Keep 3 (post-only, earns maker fees) |
| max_active_executors_by_level | int | 4 | Hanging executors cap per level |
| tick_mode | bool | false | Per-tick updates (CPU/API heavy) |
| position_profit_protection | bool | false | Blocks reductions at unfavorable prices |
| min_skew | Decimal | 1.0 | Skew floor (1.0 = none; 2.0 = heavy side ≥2× wider) |
| global_take_profit | Decimal | 0.03 | Global TP threshold (3%) |
| global_stop_loss | Decimal | 0.05 | Global SL threshold (5%) |
| global_tp_enabled | bool | false | Enable global TP |
| global_sl_enabled | bool | false | Enable global SL |
| global_tp_activation_from | str | "min_base" | "always" / "min_base" / "target_base" |
| global_sl_activation_from | str | "target_base" | "target_base" / "max_base" |
| global_pnl_reference | str | "position" | "position" or "portfolio" |
| manual_kill_switch | bool | false | Halt quoting (does NOT close positions) |
| initial_positions | list | [] | Seed with base you already hold |

## How the controller works

**Position bands**: tracks `current_base_pct` = position_value /
total_amount_quote. Below min_base_pct → only the accumulation side quotes
(buys for LONG); above max_base_pct → only the reduction side; between →
both sides, skewed.

**Skew**: buy/sell amounts multiplied by a skew factor. LONG mode:
buy_skew = (max − current)/(max − min), sell_skew = (current − min)/(max −
min) — small position quotes buys harder, large position quotes sells
harder. min_skew floors it.

**Executor lifecycle**: place order → fill → "hanging" with a per-fill
LIMIT_MAKER TP → after effectivization_time the TP is cancelled and the
position transitions to hold mode (managed only by global TP/SL) → after
cooldown a new order can place. Unfilled orders refresh after
executor_refresh_time.

**Multi-level**: each spread level runs independently (own cooldown,
distance check, max_active_executors cap).

**Global TP/SL**: two-phase close — stop all executors (keep_position),
then a market close executor.

## Sizing and safety guidance

- `order_size = total_amount_quote × portfolio_allocation ÷ levels` — verify
  it clears the venue minimum notional (~$5–10) or orders fail.
- Exposure ceiling ≈ levels × per-level size × max_active_executors_by_level
  — this cap is your real directional exposure control.
- `take_profit` must clear round-trip maker fees: perp ≥ 0.0008, spot ≥
  0.0020 (see the shared `executor-mechanics` fee table).
- Leverage: template default is 20 — for MM keep ≤5 on alts, ≤10 on majors;
  spot always 1.
- Constraint: `0 ≤ min_base_pct < target_base_pct < max_base_pct ≤ 1`.

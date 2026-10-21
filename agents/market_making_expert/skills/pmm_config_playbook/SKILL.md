---
name: pmm_config_playbook
description: Ready-to-deploy pmm_mister config profiles (aggressive / balanced / conservative)
  — full parameter coverage including spreads, effectivization times, tolerance, order
  types, skew, and global TP/SL.
when_to_use: When you need a starting pmm_mister controller config and want a vetted
  template instead of hand-tuning every parameter — pick a profile by regime, fetch
  its template, then adapt the connector/pair/amount.
source: builtin
---

# pmm_mister Config Playbook

Three vetted `pmm_mister` profiles, one per risk posture. Each profile lives in a
**companion file** — fetch only the one you need so the others never load into
context:

## Where a number comes from (MM-6)

Four sources used to state `pmm_mister`'s settings and all four disagreed — on
allocation, spreads, refresh, cooldowns, take profit, executors per level,
leverage and profit protection — so the behaviour depended on which text the
model happened to read. One order now:

1. **The controller class** (`controllers/pmm_mister/pmm_mister.py`) is the only
   authority on a *default*. It is what runs when a field is omitted; AGENT.md's
   per-field "default:" annotations document it and nothing else.
2. **These profiles** are *overrides*, and they match
   `controllers/pmm_mister/sample_configs/{profile}.yml` field for field. Change
   one and change the other.
3. **`total_amount_quote` comes from neither.** It is the session's budget, read
   from `[CURRENT CONFIG]` — a profile that names its own capital would either
   contradict the loop or breach the risk gate's position limit.

So: pick a profile for the *shape* of the quoting, take the capital from the
session, and leave anything the profile does not name at the class default.

```
manage_skill(action="read_file", name="pmm_config_playbook", file="config_aggressive.md")
```

## Pick a profile by regime

| Regime                                  | Profile          | File                        |
|-----------------------------------------|------------------|-----------------------------|\n| Quiet / low-vol ranging (ADX < 18)      | **Aggressive**   | `config_aggressive.md`      |
| Ranging / normal (ADX < 25)             | **Balanced**     | `config_balanced.md`        |
| Volatile / trending / uncertain         | **Conservative** | `config_conservative.md`    |

- **Aggressive** — tight spreads, fast refresh, short effectivization (60s),
  wide inventory bands, high allocation. Maximizes fill rate in calm markets.
  Most inventory/PnL risk.
- **Balanced** — the default. Moderate spreads, 120s effectivization, standard
  tolerances. Good steady-state when regime is unclear.
- **Conservative** — wide spreads, slow refresh, long effectivization (300s),
  tight inventory bands, strong skew enforcement (min_skew=0.3 — a SIZE floor,
  where LOWER means stronger), low
  allocation/leverage, both global TP and SL active. Capital preservation in
  chop/vol.

## How to use a template

1. Read the chosen companion file. It contains a full `config_data` block for
   `manage_controllers(action="upsert", target="config")`.
2. **Always adapt** these to the actual operation before deploying:
   - `connector_name`, `trading_pair`
   - `total_amount_quote` (respect the strategy's risk limit)
   - `leverage` (never above the strategy's cap; templates default low)
3. Deploy via the normal flow (`manage_controllers` upsert → `manage_bots` deploy).
   Live retunes go through `manage_bots(action="update_config", confirm_override=true)`.

## Key parameter reference

Parameters most commonly tuned in real operations:

**Inventory & allocation**
- `portfolio_allocation` — fraction of `total_amount_quote` actively deployed per iteration
- `target_base_pct` / `min_base_pct` / `max_base_pct` — inventory band; skew
  kicks in when base drifts outside min/max
- `min_skew` — the floor on the order-SIZE skew multiplier — not a spread setting. Each side's
multiplier is `max(min(skew, 1), min_skew)`, so `1.0` disables skew (both are always
1), a value **below** 1 (0.3–0.5) enables it and lets the overweight side's orders
shrink toward that floor, and a value **above** 1 pins both multipliers at `min_skew`
— multiplying EVERY order by it and disabling skew entirely. Never set it above 1
(MM-2, hummingbot/condor#283): a playbook shipping 2.0 turned a ~12.5 USDT order into
~24.7 while providing no inventory protection at all.
- `max_active_executors_by_level` — max concurrent open executors per level;
  controls total directional exposure (fills × allocation per level)

**Order timing**
- `executor_refresh_time` — how often (seconds) the controller checks and potentially
  replaces open orders; lower = tighter to mid price but more rate limit usage
- `buy/sell_cooldown_time` — after a fill, how long to wait before placing a new order
  on that side; lower = faster re-entry, more risk of accumulating at similar prices
- `buy/sell_position_effectivization_time` — how long (seconds) the per-fill
  LIMIT_MAKER TP order stays on the book after a fill; when this expires, the TP
  order is **removed** and the position transitions to "hold" mode managed only by
  the global SL/TP layer. Lower = TP has less time to fill → positions accumulate
  into hold faster. Higher = TP order stays on book longer → more chances of hitting TP.

**Spread & refresh tolerance**
- `price_distance_tolerance` — minimum price gap required between open orders at
  the same level; prevents stacking orders too close together
- `refresh_tolerance` — minimum mid-price move required to trigger a quote
  refresh/replacement; lower = more responsive, higher cancel/replace churn
- `tolerance_scaling` — multiplier applied to tolerance values as the number of
  active executors grows; prevents cancel-loops when multiple orders are open at
  the same level

**Order types** (3 = LIMIT_MAKER / post-only, 2 = LIMIT, 1 = MARKET)
- `open_order_type` — order type for entry orders (always use 3 unless exchange rejects)
- `take_profit_order_type` — order type for TP orders (always 3)

**Per-fill risk**
- `take_profit` — offset from fill price where the LIMIT_MAKER TP order is placed;
  must be > round-trip fees to be profitable

**Global portfolio guardrails (position hold phase)**
- `global_tp_enabled` / `global_take_profit` — when total held position gains this %, close and restart MM
- `global_sl_enabled` / `global_stop_loss` — when total held position loses this %, close and restart MM
- `global_sl_activation_from` — inventory threshold from which global SL activates
  (`"min_base"` = when base is light, `"target_base"` = from neutral)
- `global_tp_activation_from` — inventory threshold from which global TP activates
- `global_pnl_reference` — PnL basis: `"position"` (unrealized open PnL) or
  `"portfolio"` (total portfolio value)
- `position_profit_protection` — blocks inventory reduction at unfavorable prices

**Other**
- `tick_mode` — if true, controller runs only on candle ticks; false = continuous (default)

The full regime→param decision logic lives in the `pmm_mister_operator` loop.
These templates are starting points — every deploy still goes through normal
risk/confirmation controls.

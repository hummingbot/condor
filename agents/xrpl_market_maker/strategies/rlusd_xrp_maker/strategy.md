---
name: RLUSD XRP Maker
description: Quotes both sides of an XRPL CLOB pair against a CEX reference price,
  sized to reserves and bounded by the AMM fee ceiling.
agent_key: null
skills:
- xrpl_mm_deploy
default_config:
  frequency_sec: 300
  total_amount_quote: 100
  execution_mode: dry_run
  bot_name: rlusd-xrp-maker
  xrpl_pair: RLUSD-XRP
  reference_connector: bitget_perpetual
  reference_pair: XRP-USDT
  levels_per_side: 3
  executor_refresh_time: 30
  skip_rebalance: true
  adverse_k: 1.0
  use_vol_clock: true
  inventory_target_pct: 50
  inventory_band_pct: 15
  hedge_enabled: false
  hedge_connector: bitget_perpetual
  hedge_pair: XRP-USDT
  risk_limits:
    max_position_size_quote: 150
    max_open_executors: 8
    max_drawdown_pct: 10
    shutdown_drawdown_pct: 20
default_trading_context: ''
created_by: 0
created_at: '2026-07-28T00:00:00Z'
---

# RLUSD XRP Maker

Quote both sides of an XRPL CLOB pair off a CEX reference, sized to reserves, under the
AMM fee ceiling. Read all runtime values from `[CURRENT CONFIG]`.

## Launch

- `frequency_sec` = LLM thinking cadence; `executor_refresh_time` = quote exposure window.
  Planner floor uses the latter in controller mode — never pass `frequency_sec` as
  `requote_interval_sec`.
- `bot_name` set (`rlusd-xrp-maker`) ⇒ controller mode. Stable name; do not clear it to
  "pick" executor mode.
- First tick, if no bot by that name: deploy `pmm_simple` per `xrpl_mm_deploy` Phase 3.
  Fall back to executors only on real failure — then clear `bot_name` to `''` and journal why.

## Startup guard — notSynced race condition

The XRPL connector takes a few seconds to sync after bot startup. A `notSynced` / HTTP 500
error from the XRPL connector on tick #1 or tick #2 is **transient, not fatal**.

- If any XRPL call (book, balances, order status) returns a `notSynced` or 500 error:
  **HOLD this tick entirely** — do not attempt deploy, do not cancel orders, do not journal
  a failure. Log "XRPL notSynced — holding tick, will retry next tick."
- Retry on tick #2. If the error persists beyond tick #3, treat it as a real failure and
  journal `category="execution"`.
- Reference feed errors (CEX) are NOT transient — treat those as hard stops immediately.

## Each tick

**1. Plan**

```
manage_routines(action="run", name="xrpl_mm_quote_planner",
                agent="xrpl_market_maker",
                config={"xrpl_pair": "<from config>",
                        "reference_connector": "<from config>",
                        "reference_pair": "<from config>",
                        "tick_interval_sec": <frequency_sec>,
                        "requote_interval_sec": <executor_refresh_time in controller mode,
                                                 else frequency_sec>,
                        "levels_per_side": <from config>,
                        "total_amount_quote": <from config>,
                        "adverse_k": <from config>,
                        "use_vol_clock": <from config>})
```

If the planner returns an XRPL book error tagged as `notSynced` or `status: ERROR` on tick
#1/#2, apply the startup guard above (HOLD, retry next tick).

**2. Viability** — `viable: false` (with correct requote interval) → stop quoting
(controller: `manual_kill_switch: true`; executor: cancel). HOLD. Do not tighten below floor.

**3. Book** — empty/unavailable XRPL book → HOLD. Large `divergence_vs_reference_bps` →
treat as stale; act only if it persists across two ticks. Kill switch if controller mid
has drifted.

**4. Inventory** — base share vs `inventory_target_pct` ± `inventory_band_pct` from
positions + `get_portfolio_overview()`.

**5. One action**

*Controller (`bot_name` set) — tune only; bot quotes:*

| Condition | Action |
|---|---|
| notSynced error (tick #1/#2) | **HOLD** — startup guard, retry next tick |
| `viable: false` or book unavailable | **STOP** — `manual_kill_switch: true` |
| Viable, previously killed | **RESUME** — kill switch false, spreads refreshed |
| Viable, plan changed | **RETUNE** — push spreads to both config stores |
| Viable, unchanged | **HOLD** |
| Inventory outside band | **SKEW** — asymmetric spreads (never `skip_rebalance: false`) |
| `hedge_enabled`, delta outside band | **HEDGE** |
| Hedge on, one leg missing | **FIX LEG PARITY** — only action this tick |

*Executor (`bot_name` empty, after recorded controller failure):*

| Condition | Action |
|---|---|
| notSynced error (tick #1/#2) | **HOLD** — startup guard, retry next tick |
| `viable: false` or book unavailable | **HOLD** — cancel, journal |
| Viable, no offers | **QUOTE** — LIMIT ladder |
| Viable, reference moved > ½ spread | **REQUOTE** |
| Viable, stable | **HOLD** |
| Inventory outside band | **SKEW** — lean quotes, never cross |
| Hedge cases | same as controller |

**6. Journal** one action. Execution failures → `category="execution"`.

## Sizing

- Free balance only (1 XRP base + 0.2 per offer reserved).
- `per_level_notional = total_amount_quote / (levels_per_side × 2)`.
- Cap total quoted notional at `max_position_size_quote`.
- Widen when uncertain; never tighten below the floor.

## Guardrails

- No XRPL candles. No `place_order`. Fair value from `reference_connector` only.
- Spreads = planner `controller_spreads` (fractions). Quote sizes = planner
  `controller_total_amount_quote` (XRP on RLUSD-XRP). Keep `skip_rebalance: true`.
- Controller retunes update **both** stores. Executors pass `controller_id="{agent_id}"`.
- Declare `max_global_drawdown_quote` on every deploy.

## Errors

| Tell | Action |
|---|---|
| `notSynced` / 500 on tick #1 or #2 | **HOLD** — transient startup race; retry next tick |
| `notSynced` / 500 on tick #3+ | Journal `category="execution"`, notify user |
| `tecUNFUNDED_OFFER` | Resize vs free balance, retry once |
| `tecNO_LINE` / `tecPATH_DRY` | Notify user — do not retry blindly |
| Accepted but absent from book | Check balances before replacing |
| Reference feed down | HOLD / cancel — never quote blind |

On create failure: re-fetch schema, fix, retry once, journal. No retry loops in a tick.

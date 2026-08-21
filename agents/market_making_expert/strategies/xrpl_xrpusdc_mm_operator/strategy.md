---
name: XRPL XRP/USDC MM Operator
description: Agentic market-making loop for XRP-USDC on the XRPL DEX — regime-aware
  spread calibration using GeckoTerminal OHLCV, pmm_mister deployment and lifecycle
  management, inventory balance tracking.
agent_key: null
skills: []
default_config:
  frequency_sec: 600
  total_amount_quote: 100
  execution_mode: loop
  connector_name: xrpl
  trading_pair: XRP-USDC
  risk_limits:
    max_position_size_quote: 350
    max_open_executors: 6
default_trading_context: ''
created_by: 456181693
created_at: '2026-08-07T17:23:55.746886+00:00'
---

# XRPL XRP/USDC Market Making Strategy

## Objective
Autonomously market make for XRP-USDC on the XRPL DEX using the pmm_mister controller. Earn maker spread on both sides of the book. Keep inventory balanced between XRP and USDC. Adapt spreads to the current volatility regime, derived from GeckoTerminal OHLCV (XRPL does NOT support Hummingbot's native candle feed).

---

## Data Sources (fetch each tick in this order)

1. **Current price**
   `get_market_data(data_type="prices", connector_name="xrpl", trading_pairs=["XRP-USDC"])`

2. **OHLCV / volatility** — GeckoTerminal workaround for missing candle feed
   - First tick (or if pool address unknown): discover with
     `explore_geckoterminal(action="top_pools", network="xrpl")` → find the XRP/USDC pool, save its address to memory.
   - Every tick: `explore_geckoterminal(action="ohlcv", network="xrpl", pool_address="<saved>", timeframe="1h", limit=24)`

3. **Portfolio snapshot**
   `get_portfolio_overview(connector_names=["xrpl"], include_balances=True, include_perp_positions=False, include_lp_positions=False)`

4. **Active controllers**
   `manage_controllers(action="list")` — check if `xrpl_xrp_usdc_mm` config exists and which bot holds it.

---

## Regime Classification (from last 24 × 1h candles)

Compute:
- `volatility = std(close) / mean(close)` over the 24-candle window
- `trend = (last_close - first_close) / first_close`

| Regime | Condition |
|--------|-----------|
| **quiet** | volatility < 0.005 |
| **ranging** | 0.005 ≤ volatility < 0.015 |
| **volatile** | 0.015 ≤ volatility < 0.03 |
| **extreme** | volatility ≥ 0.03 |
| **trending_up** | \|trend\| > 0.01 AND trend > 0 |
| **trending_down** | \|trend\| > 0.01 AND trend < 0 |

If GeckoTerminal data is unavailable, default to `ranging`.

---

## Spread & TP Parameter Table

| Regime | buy_spreads | sell_spreads | take_profit |
|--------|------------|--------------|-------------|
| quiet | "0.0010,0.0020" | "0.0010,0.0020" | 0.0012 |
| ranging | "0.0020,0.0040" | "0.0020,0.0040" | 0.0022 |
| volatile | "0.0040,0.0080" | "0.0040,0.0080" | 0.0045 |
| extreme | PAUSE — set manual_kill_switch=True | — | — |
| trending_up | "0.0020,0.0040" | "0.0040,0.0080" | 0.0022 |
| trending_down | "0.0040,0.0080" | "0.0020,0.0040" | 0.0022 |

**Fee rule:** XRPL round-trip maker cost ≈ 0.10% (network transaction drops + effective spread cost). `take_profit` must always exceed 0.0010 (0.10%). The values above are safe minimums.

**Inventory skew adjustment:**
- If XRP% of portfolio > 65 %: multiply buy_spreads by 1.5; set target_base_pct=0.40
- If XRP% of portfolio < 35 %: multiply sell_spreads by 1.5; set target_base_pct=0.60
- Otherwise: target_base_pct=0.50

---

## pmm_mister Controller Config Schema

Config name: `xrpl_xrp_usdc_mm`
Controller: `generic/pmm_mister`

```json
{
  "controller_name": "generic/pmm_mister",
  "controller_type": "generic",
  "connector_name": "xrpl",
  "trading_pair": "XRP-USDC",
  "total_amount_quote": <strategy total_amount_quote>,
  "leverage": 1,
  "position_mode": "ONEWAY",
  "portfolio_allocation": 0.05,
  "max_active_executors_by_level": 3,
  "buy_spreads": "<regime-derived>",
  "sell_spreads": "<regime-derived>",
  "buy_amounts_pct": "1,1",
  "sell_amounts_pct": "1,1",
  "open_order_type": "LIMIT_MAKER",
  "target_base_pct": "<inventory-adjusted>",
  "min_base_pct": 0.30,
  "max_base_pct": 0.70,
  "min_skew": 1.0,
  "executor_refresh_time": 30,
  "buy_cooldown_time": 30,
  "sell_cooldown_time": 30,
  "buy_position_effectivization_time": 600,
  "sell_position_effectivization_time": 600,
  "take_profit": "<regime-derived>",
  "take_profit_order_type": "LIMIT_MAKER",
  "price_distance_tolerance": 0.0005,
  "refresh_tolerance": 0.0005,
  "tolerance_scaling": 1.2,
  "global_take_profit": 0.03,
  "global_stop_loss": 0.05,
  "position_profit_protection": true,
  "manual_kill_switch": false,
  "tick_mode": false
}
```

**XRPL note on order types:** XRPL offers are naturally maker orders (they post to the on-chain book). If `LIMIT_MAKER` returns an error from the connector, fall back to `LIMIT` and journal the change.

---

## Decision Logic (every tick)

1. Fetch all four data sources.
2. Classify regime; compute target spreads, TP, and inventory-adjusted target_base_pct.
3. Check if config `xrpl_xrp_usdc_mm` exists:
   - **Does not exist** → upsert it:
     `manage_controllers(action="upsert", target="config", config_name="xrpl_xrp_usdc_mm", config_data={...})`
   - **Exists** → compare current spreads/TP to computed targets. Update if any value drifts > 20% from target:
     `manage_controllers(action="upsert", target="config", config_name="xrpl_xrp_usdc_mm", config_data={...})`
4. Check if bot `xrpl-xrp-usdc-mm` is running (`manage_bots(action="status")`):
   - **Not running** → deploy:
     `manage_bots(action="deploy", bot_name="xrpl-xrp-usdc-mm", controllers_config=["xrpl_xrp_usdc_mm"])`
   - **Running** → fetch error logs (`manage_bots(action="logs", bot_name="xrpl-xrp-usdc-mm", log_type="error")`). If errors, journal them; if recoverable (e.g. stale order), wait; if fatal, stop and redeploy.
5. If regime is **extreme**, set `manual_kill_switch=True` in the config update and journal the reason. Do not redeploy until regime exits extreme.

---

## Risk Rules
- Never exceed XRP position worth > `total_amount_quote × 0.70` in USDC value.
- Max open executors: `risk_limits.max_open_executors` (passed at launch).
- After `global_stop_loss` triggers: skip re-deploy for 2 full ticks.
- If volatility exceeds 3 %, always set `manual_kill_switch=True` even if regime classification says volatile (not extreme).
- Never update config mid-fill (check logs for active fills before any config change).

---

## Error Recovery
On a failed `manage_controllers` upsert or `manage_bots` deploy:
1. Re-fetch the fresh schema: `manage_controllers(action="describe", controller_name="generic/pmm_mister")`
2. Identify the offending field from the error message; fix it.
3. Retry once.
4. If still failing, journal the error with full detail and hold for 1 tick before retrying.

---

## Journaling (each tick)
Write a `state` entry with:
- price, regime, volatility (%)
- buy_spreads / sell_spreads applied
- portfolio XRP%
- bot status (running / stopped / error)
- action taken: hold | update_config | deploy | kill_switch | redeploy

Write a `learning` entry only when a new XRPL-specific pattern is discovered (e.g. order type incompatibility, min order size constraint, pool address).


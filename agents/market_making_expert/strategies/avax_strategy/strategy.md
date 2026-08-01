---
name: AVAX Strategy
description: pmm_dynamic AVAX-USD on Hyperliquid — NATR/MACD spreads, 12bps TP, monitor
  & retune by regime
agent_key: null
skills:
- pmm_mister_deploy
- pmm_config_playbook
- mm_bot_report
- capital_allocation
default_config:
  bot_name: bot_avax_dynamic_v2-20260801-034110-20260801-034112
  config_name: mm_avax_dynamic_v2
  controller_name: pmm_dynamic
  connector_name: hyperliquid_perpetual
  trading_pair: AVAX-USD
  total_amount_quote: 400
  take_profit: 0.0012
  leverage: 4
  frequency_sec: 300
default_trading_context: Hyperliquid AVAX-USD pmm_dynamic (NOT pmm_mister). Volatility-scaled
  spreads via NATR + MACD mid shift. TP=12bps. Free margin is tight — do not raise
  total_amount_quote without checking withdrawable.
created_by: 0
created_at: '2026-08-01T03:29:30.950183+00:00'
---

# AVAX Strategy — pmm_dynamic on Hyperliquid

## Goal
Market-make AVAX-USD on `hyperliquid_perpetual` with **pmm_dynamic** (MACD mid-shift +
NATR spread multiplier + triple-barrier risk). Prefer maker fills with a viable TP over
inventory accumulation.

> **Controller note (2026-08-01):** An earlier deploy incorrectly used `pmm_mister`.
> That bot was stopped/archived and replaced with `pmm_dynamic` as intended.

## Live artifacts
- **Controller:** `pmm_dynamic` (`controller_type: market_making`)
- **Controller config:** `mm_avax_dynamic_v2`
- **Bot name pattern:** `bot_avax_dynamic_v2-*` (discover live name via status)
- **Connector / pair:** `hyperliquid_perpetual` / `AVAX-USD`
- **Capital:** `total_amount_quote=400` (margin-safe; free margin often tight)
- **Image:** `condor/hummingbot:hyperliquid-cancel-fix` only (never raw API deploy)

## Baseline config (do not silently reset)
| Param | Value | Why |
|---|---|---|
| candles_connector / candles_trading_pair | hyperliquid_perpetual / AVAX-USD | **Required** — blank/absent breaks CandlesConfig (see pmm_dynamic gotcha) |
| interval | 3m | NATR/MACD candle frame |
| macd_fast / slow / signal | 21 / 42 / 9 | Mid-price shift |
| natr_length | 14 | Spread multiplier (spreads are in NATR units) |
| buy/sell_spreads | 0.4, 0.55, 0.85 | Multiples of NATR (not raw bps) |
| buy/sell_amounts_pct | 0.5, 0.3, 0.2 | Front-loaded ladder |
| take_profit | 0.0012 (12 bps) | > ~3 bps HL round-trip maker fees |
| take_profit_order_type | LIMIT | Maker-style TP |
| stop_loss | 0.0035 (35 bps) | Per-executor hard stop |
| time_limit | 600s | Executor max age |
| time_limit_order_type | LIMIT_MAKER | Prefer maker exit on timeout |
| time_limit_maker_timeout | 20s | Then fall back if maker rest fails |
| executor_refresh_time | 20 | Quote refresh |
| cooldown_time | 15 | Re-entry gate after fill |
| leverage | 4 | Conservative MM leverage |
| position_rebalance_threshold_pct | 0.08 | Inventory rebalance trigger |
| total_amount_quote | 400 | Do not raise without free margin check |

## Regime playbook
Re-check AVAX 1h candles + funding each tick:

- **Quiet / ranging:** keep spreads; NATR already tightens quotes automatically
- **Volatile:** NATR widens spreads; if still thrashing, pause (`manual_kill_switch=true`) or widen base spreads to `0.6,0.9,1.3`
- **Trending:** MACD shifts mid; if inventory drifts hard, raise `position_rebalance_threshold_pct` temporarily or pause

Always update **both** saved config and live bot when retuning.

## Inventory rules
1. Ground truth = Hyperliquid `clearinghouseState` / `frontendOpenOrders` — not dashboard alone
2. Existing long inventory is seeded via `reconcile_initial_positions` on deploy
3. Free margin is often single-digit USD — do not stack more bots without checking withdrawable
4. With 2 bots on this host, `rate_limits_share_pct` should be ~50 each (edit conf_client.yml + docker restart, staggered)

## Each tick
1. Confirm bot running; note volume / close_type_counts
2. Spot-check HL open orders for AVAX
3. If 429s: confirm `rate_limits_share_pct` ~50 and stagger restarts
4. Journal material changes

## Hard constraints
- Deploy/stop only via Condor wrappers (`deploy_bot` / `manage_bot_execution`) — never raw hummingbot-api deploy/stop
- After any stop: verify `frontendOpenOrders` for leftover AVAX orders and cancel
- Do not trust `/trading/orders/active` or executors table
- Always set `candles_connector` + `candles_trading_pair` explicitly on pmm_dynamic configs

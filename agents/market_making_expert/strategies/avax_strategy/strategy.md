---
name: AVAX Strategy
description: pmm_mister AVAX-USD on Hyperliquid — 12bps TP, mild inventory skew, monitor
  & retune by regime
agent_key: null
skills:
- pmm_mister_deploy
- pmm_config_playbook
- mm_bot_report
- capital_allocation
default_config:
  bot_name: bot_avax_mister_v2-20260801-032849-20260801-032853
  config_name: mm_avax_mister_v2
  connector_name: hyperliquid_perpetual
  trading_pair: AVAX-USD
  total_amount_quote: 400
  take_profit: 0.0012
  min_skew: 0.35
  leverage: 4
  frequency_sec: 300
default_trading_context: Hyperliquid AVAX-USD pmm_mister. Prefer TP round-trips over
  accumulation. TP=12bps, min_skew=0.35. Free margin is tight — do not raise total_amount_quote
  without checking withdrawable.
created_by: 0
created_at: '2026-08-01T03:29:30.950183+00:00'
---

# AVAX Strategy — pmm_mister on Hyperliquid

## Goal
Market-make AVAX-USD on `hyperliquid_perpetual` with **pmm_mister**. Optimize for **round-trip fills (TP)** over inventory accumulation. Prefer volume + small edge over directional bags.

## Live artifacts
- **Controller config:** `mm_avax_mister_v2`
- **Bot name pattern:** `bot_avax_mister_v2-*` (discover live name via `manage_bots(action="status")`)
- **Connector / pair:** `hyperliquid_perpetual` / `AVAX-USD`
- **Capital:** `total_amount_quote=400` (margin-safe; free margin was tight at deploy)
- **Image:** `condor/hummingbot:hyperliquid-cancel-fix` only (never raw API deploy)

## Baseline config (do not silently reset)
| Param | Value | Why |
|---|---|---|
| take_profit | 0.0012 (12 bps) | > ~3 bps HL round-trip maker fees; prior dialed-in TP |
| min_skew | 0.35 | Mild inventory size taper (floor on buy/sell size skew) |
| position_profit_protection | false | Avoid one-way freeze when slightly underwater |
| buy/sell_spreads | 0.0003, 0.0006, 0.001 | Quiet/ranging-appropriate ladder |
| open/TP order type | LIMIT_MAKER | Maker only |
| effectivization | 120s | Time for per-fill TP before hold |
| global_tp_enabled / global_take_profit | true / 0.02 | Realize hold-phase gains |
| global_sl_enabled / global_stop_loss | true / 0.015 | Hard backstop on held position |
| leverage | 4 | Conservative MM leverage |
| max_active_executors_by_level | 3 | Cap stacked exposure |
| portfolio_allocation | 1.0 | Full budget per quote cycle at this small size |

## Regime playbook
Re-check AVAX 1h candles + funding each tick (or at least every few ticks):

- **Quiet / ranging** (ADX < 18, ATR compressing): keep or **tighten** inner spreads slightly; maintain min_skew 0.35
- **Volatile** (ATR expanding, large candles): **widen** both spreads ~1.5–2× or `manual_kill_switch=true`
- **Trending up:** mild skew — slightly wider `sell_spreads`, slightly tighter `buy_spreads`; optionally raise `target_base_pct` toward 0.55–0.60
- **Trending down:** opposite; lower `target_base_pct` toward 0.40–0.45

Always update **both** saved config (`manage_controllers` upsert) and live bot (`manage_bots update_config` with `confirm_override=true`).

## Inventory rules
1. Ground truth = Hyperliquid `clearinghouseState` / `frontendOpenOrders` — not dashboard PnL alone
2. If base_pct near `max_base_pct` or bot stops buying: raise band or lower target (see pmm_mister_deploy inventory levers)
3. If skewed long with funding positive and expensive: skew to sell; consider pausing buys
4. Never leave `global_tp_enabled` / `global_sl_enabled` false while global levels are set

## Each tick
1. `manage_bots(action="status")` — confirm bot running; note custom_info position / base_pct
2. Spot-check HL open orders for AVAX and fills quality (TP vs hold)
3. If 429s / rate-limit errors: lower `rate_limits_share_pct` in container `conf_client.yml` (~50 with 2 bots) and **docker restart** only that bot (stagger if multi-bot)
4. If accumulation dominates TP: raise `take_profit` only if still filling, or shorten effectivization is wrong direction — prefer **raise min_skew taper** (lower min_skew toward 0.25) or widen spreads on heavy side via inventory band
5. Journal material changes (param edits, regime shifts, incidents)

## Hard constraints
- Deploy/stop only via Condor wrappers (`deploy_bot` / `manage_bot_execution`) — never raw hummingbot-api deploy/stop
- After any stop: verify `frontendOpenOrders` for leftover AVAX orders and cancel
- Do not trust `/trading/orders/active` or executors table
- pmm_mister `positions_summary` / global_pnl undercounts POSITION_HOLD folds — use HL + custom_info.position_amount


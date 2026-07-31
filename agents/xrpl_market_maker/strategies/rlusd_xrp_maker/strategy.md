---
name: RLUSD XRP Maker
description: Quotes both sides of an XRPL CLOB pair against a CEX reference price, sized
  to reserves and bounded by the AMM fee ceiling.
agent_key: null
skills:
- xrpl_mm_feasibility
- xrpl_mm_deploy
default_config:
  frequency_sec: 300
  total_amount_quote: 100
  execution_mode: dry_run
  bot_name: ''
  xrpl_pair: RLUSD-XRP
  reference_connector: bitget_perpetual
  reference_pair: XRP-USDT
  levels_per_side: 3
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
created_at: '2026-07-28T00:00:00Z'
---

# RLUSD XRP Maker

You quote both sides of an XRPL CLOB pair, priced off a CEX reference, sized to respect
XRPL reserves, and bounded above by the AMM fee you must undercut to win order flow.

## Configuration at launch

Read every runtime value from `[CURRENT CONFIG]` — nothing below is hardcoded:

`xrpl_pair` · `reference_connector` · `reference_pair` · `levels_per_side` ·
`total_amount_quote` · `adverse_k` · `use_vol_clock` · `inventory_target_pct` · `inventory_band_pct` ·
`hedge_enabled` · `frequency_sec` · `bot_name`

**`bot_name` selects the execution path — but controller mode is the default to try.**
Non-empty → controller mode (tune the bot, never place individual orders). Empty → on the
*first* tick of a run, attempt a controller deploy per `xrpl_mm_deploy` Phase 3 (a
`pmm_simple` config — not `pmm_dynamic`) before ever placing an executor order. Only switch to
executor mode, and journal why, if that attempt hits a real failure: a schema rejection, a
deploy/status error, or the bot placing no orders on-ledger within a few ticks. Once a tick
has recorded that verdict, keep using it for the rest of the run instead of retrying the
controller deploy every tick — re-attempt only after the next Hummingbot/connector upgrade,
or if explicitly asked to re-check.

## Each tick

**1. Plan the quotes.** Always first — it is the deterministic basis for everything else:

```
manage_routines(action="run", name="xrpl_mm_quote_planner",
                strategy_id="xrpl_market_maker.rlusd_xrp_maker",
                config={"xrpl_pair": "<from config>",
                        "reference_connector": "<from config>",
                        "reference_pair": "<from config>",
                        "tick_interval_sec": <frequency_sec>,
                        "levels_per_side": <from config>,
                        "total_amount_quote": <from config>,
                        "adverse_k": <from config>,
                        "use_vol_clock": <from config>})
```

The `SPREAD FLOOR` block exposes the volatility-clock adjustment step by step —
`realized_vol_raw` → `hour_mult_lookback` → `vol_deseasonalized` → `hour_mult_forward` →
`vol_adjusted`. Read these when a floor looks surprising; a high `hour_mult_forward` near
13:00–15:00 UTC (US open) legitimately widens the floor and may make quoting unviable for a
few hours. That is correct behaviour, not a fault.

**2. Check viability before anything else.** If the routine reports `viable: false`, the
adverse-selection floor has met the AMM fee ceiling. **Cancel resting offers and HOLD.**
Journal the reason. Do not tighten below the floor to force fills — that buys volume with
adverse selection. This is a normal, expected outcome, not a failure.

**3. Check the book is real.** If the `XRPL BOOK` block reports empty, unavailable, or an
error → **HOLD**. Never quote blind. If `divergence_vs_reference_bps` is large, treat it
as stale data rather than opportunity: re-read state, and only act if it persists across
two ticks.

**4. Read inventory.** From `[CORE DATA - positions]` and `get_portfolio_overview()`,
compute current base-asset share of the pair's total value. Compare to
`inventory_target_pct` ± `inventory_band_pct`.

**5. Decide.** Exactly one action per tick:

| Condition | Action |
|---|---|
| `viable: false`, or book unavailable | **HOLD** — cancel resting offers, journal why |
| Viable, no offers resting | **QUOTE** — place ladder at the suggested spread |
| Viable, offers resting, reference moved > ½ spread | **REQUOTE** — cancel and replace |
| Viable, offers resting, reference stable | **HOLD** — leave them working |
| Inventory outside band | **SKEW** — lean quotes toward rebalancing (never cross the spread) |
| `hedge_enabled` and net delta outside band | **HEDGE** — adjust the `bitget_perpetual` leg |
| Hedge enabled but one leg missing | **FIX LEG PARITY** — the only action this tick |

**6. Journal** one action entry. Execution failures go in as `category="execution"`.

## Sizing rules

- Size against **free** balance. Reserved XRP (1 base + 0.2 per open offer) is not spendable.
- `per_level_notional = total_amount_quote / (levels_per_side × 2)`.
- Never let total quoted notional exceed `max_position_size_quote`.
- Widen, never tighten, when uncertain. A missed fill costs nothing; an adversely selected
  fill costs real money.

## Inventory management

You **cannot** market-flatten on XRPL — LIMIT orders only. Inventory is managed by
*leaning quotes*: when long the base asset, tighten the ask and widen the bid. Never cross
the spread to rebalance; you would be paying the spread you exist to earn.

If inventory breaches the band and leaning has not corrected it within several ticks,
notify the user rather than escalating aggression.

## Guardrails

- **Never** call `get_market_data(data_type="candles", connector_name="xrpl")` — the
  connector has no candles feed. XRPL OHLCV comes from `explore_geckoterminal`.
- **Never** call `place_order`. Executors or controllers only.
- **Never** derive fair value from on-ledger data alone — that is a stale price and
  guarantees adverse selection. Fair value comes from `reference_connector`.
- **Never** quote wider than the AMM fee ceiling; pathfinding routes that flow to the AMM
  and you simply will not fill.
- In controller mode, update **both** config stores — `manage_controllers(upsert)` for the
  saved template *and* `manage_bots(update_config)` for the live bot.
- Pass `controller_id="{agent_id}"` as a top-level argument to `manage_executors`.
- Declare `max_global_drawdown_quote` on every bot deploy.

## Error recovery

| Tell | Meaning | Action |
|---|---|---|
| `tecUNFUNDED_OFFER` | Sizing ignored reserved XRP | Recompute against free balance, retry once |
| `tecNO_LINE`, `tecPATH_DRY` | Trustline missing or limit too low | Notify user — do not retry blindly |
| Offer accepted but absent from book | Likely crossed and filled instantly | Check balances *before* replacing, or you will double up |
| Reference feed unavailable | No fair value | HOLD and cancel — never quote off a stale reference |

On any create failure: fetch the schema, fix the fields, retry **once**, then journal the
learning. Do not loop retries within a tick.

## Rollout

Ships as `execution_mode: dry_run`. Progress to `run_once`, then `loop` with a short
`max_ticks`, only once the execution path is settled for this run — a controller deploy
confirmed placing offers on-ledger, or a recorded controller failure that puts you in
executor mode.

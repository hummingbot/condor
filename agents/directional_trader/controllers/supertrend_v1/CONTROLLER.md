---
type: directional_trading
description: Supertrend trend-follower; enters with the trend when close is within percentage_threshold of the band. Market entry, triple barrier exits.
---

# supertrend_v1

## What it does

`supertrend_v1` is a trend-following, taker-entry directional controller. It computes a
Supertrend (ATR bands around hl2) on one candle feed, reads the trend direction, and emits a
**long signal while the trend is up** and a **short signal while the trend is down**, but only
when the close is **close to the Supertrend line** (within `percentage_threshold`). In plain
words: "trade in the direction of the trend, and only when price has pulled back near the
trailing band", which is a buy-the-dip-in-an-uptrend / sell-the-rip-in-a-downtrend entry,
not a breakout or flip entry.

Each signal opens one `PositionExecutor` (market order in, triple barrier out). The controller
never closes positions itself: a trend flip does **not** exit an open trade. Exits come only
from stop loss, take profit, trailing stop or time limit.

**Category: `directional_trading`.** Classes: `SuperTrendConfig(DirectionalTradingControllerConfigBase)`
and `SuperTrend(DirectionalTradingControllerBase)`. The Hummingbot API imports it from
`controllers/directional_trading/supertrend_v1.py`, every config carries
`controller_type: directional_trading`, and it is synced as `directional_trading`.

## Signal logic, step by step

All in `SuperTrend.update_processed_data()` (vectorized pandas, no loops in the controller):

1. Pull candles: `market_data_provider.get_candles_df(candles_connector, candles_trading_pair,
   interval, max_records=length + 10)`.
2. `df.ta.supertrend(length=length, multiplier=multiplier, append=True)` (pandas_ta). This adds:
   - `SUPERT_{length}_{multiplier}`: the active band (the lower band in an uptrend, the upper band in a downtrend).
   - `SUPERTd_{length}_{multiplier}`: direction, `1` up / `-1` down (NaN for the first `length` rows).
   - `SUPERTl_...` / `SUPERTs_...`: long and short band columns.
   The bands are `hl2 +/- multiplier * ATR(length)` with ATR smoothed by RMA; the direction flips
   when close crosses the previous bar's opposite band, and the bands ratchet (the lower band
   never moves down in an uptrend, the upper band never moves up in a downtrend).
   Column names use the float repr of `multiplier`, e.g. `SUPERT_20_4.0`.
3. `percentage_distance = |close - SUPERT| / close`, the distance from price to the active band.
4. Conditions:
   - long: `SUPERTd == 1` and `percentage_distance < percentage_threshold`
   - short: `SUPERTd == -1` and `percentage_distance < percentage_threshold`
5. `signal` column = `1` / `-1` / `0`; `processed_data["signal"]` = last row, and
   `processed_data["features"]` = the whole frame (used by the backtester and `to_format_status`).

It is a **level** signal, not an event: it stays at +1 on every bar where price sits near the
band in an uptrend. Entries are therefore rate-limited only by `cooldown_time` and
`max_executors_per_side` (see sizing below), not by "one trade per pullback".

### The threshold is the whole filter

The band distance is roughly `multiplier * ATR / price`. If `percentage_threshold` is larger
than the typical distance, the filter does nothing and the controller is effectively "always in
the trend direction". Measured on 1500 recent Binance perpetual candles (Sep 2026):

| setup | median distance | 25th pct | bars with a signal |
|---|---|---|---|
| ETH-USDT 3m, 20 / 4.0, threshold 0.01 (the code defaults) | 0.24% | 0.15% | ~97% |
| BTC-USDT 15m, 20 / 3.0, threshold 0.003 | 0.58% | 0.37% | ~18% |
| SOL-USDT 5m, 14 / 3.0, threshold 0.003 | 0.54% | 0.36% | ~18% |
| ETH-USDT 3m, 10 / 2.0, threshold 0.0015 | 0.13% | 0.08% | ~59% |

Rule of thumb: set `percentage_threshold` near the 25th-50th percentile of `percentage_distance`
for your pair/interval/multiplier. Check it in a research routine or in the backtest's
`processed_data.features` before trusting a config. Whenever you change `multiplier`, `length`
or `interval`, re-tune the threshold: they are coupled.

## Market data and warm-up

- One candle feed: `candles_connector` / `candles_trading_pair` / `interval`. If
  `candles_connector` or `candles_trading_pair` is given as empty or null, the
  `mode="before"` validators copy `connector_name` / `trading_pair`. A key that is left
  out entirely is NOT filled (pydantic does not run validators on missing fields), so
  always set both explicitly.
- The feed is declared by `get_candles_config()`; there is no `candles_config` field to set.
- `max_records = length + 10` (not configurable). Live, the controller computes Supertrend on
  just the last `length + 10` candles.

**Live vs backtest warning (important).** Supertrend is path dependent (RMA-smoothed ATR plus
ratcheting bands plus a direction that is carried forward). With only `length + 10` candles the
live direction often disagrees with the direction computed over a long history. The backtester
computes the indicator once over the whole backtest window (plus a `max_records` buffer), so it
sees the "long history" version. On the last 200 bars of recent Binance data, the live window
disagreed with the full-history direction on 26-130 of 200 bars depending on the setup (worse
with longer `length` and larger `multiplier`). Expect live signals to differ from backtest
signals; when you compare live against a backtest, look here first. The fix would be a larger
`max_records` in the code (e.g. `length * 10`), which means editing the controller in your
folder and syncing it.

The backtester merges the signal frame onto the backtest resolution with a backward `merge_asof`
on candle open timestamps, so when `interval` is coarser than the backtesting resolution the
signal of a still-forming candle can be used. Treat very good short-interval results with
suspicion.

## Executors, barriers and sizing

Inherited from `DirectionalTradingControllerBase`:

- `create_actions_proposal()`: when `signal != 0` and `can_create_executor(signal)`, create one
  `PositionExecutor` at the current mid price.
- `can_create_executor()`: allowed when active executors on that side `< max_executors_per_side`
  **and** `now - newest active executor's timestamp on that side > cooldown_time`. The cooldown
  is measured from the last *active* executor on that side; once they are all closed, the next
  signal can fire immediately.
- `stop_actions_proposal()` returns nothing: no signal-based exits, no early stop on trend flip.
  In HEDGE mode a flip can leave a long and a short open together.
- Amount per executor = `total_amount_quote / mid_price / max_executors_per_side` (base units).
  `total_amount_quote` is the notional of all executors on one side together, not the margin;
  margin used is about notional / `leverage`. Both sides can be full at once in HEDGE mode.
- Triple barrier (`triple_barrier_config`): open order is always MARKET; stop loss and time
  limit close with MARKET; take profit uses `take_profit_order_type`.
  - `stop_loss`, `take_profit` are fractions of **net PnL** (fees included), e.g. `0.02` = 2%.
    They are unleveraged price moves, not ROE: with 10x leverage, 0.01 is a 10% margin loss.
  - With `take_profit_order_type: LIMIT` a resting limit take profit is placed at
    entry * (1 +/- take_profit) once inside activation bounds (maker fee on the exit);
    with `MARKET` it closes when net PnL reaches `take_profit`.
  - `time_limit` seconds after the executor was created, close at market.
  - `trailing_stop`: once net PnL exceeds `activation_price`, a stop trails at
    `pnl - trailing_delta` and closes on retrace.
- `leverage` is sent on each executor; `position_mode` is a config field used by the bot to set
  the account mode (HEDGE or ONEWAY).

## Parameter reference

Updatable = marked `is_updatable` in the code (can be changed on a running bot via
`manage_bots(action="update_config")`).

| field | type | default | meaning / tuning | updatable |
|---|---|---|---|---|
| `controller_name` | str | `supertrend_v1` | must be exactly this | no |
| `controller_type` | str | `directional_trading` | must be `directional_trading` | no |
| `connector_name` | str | `binance_perpetual` | exchange to trade on | no |
| `trading_pair` | str | `WLD-USDT` | pair to trade; always set it | no |
| `candles_connector` | str or null | = `connector_name` | candle source; normally the same exchange | no |
| `candles_trading_pair` | str or null | = `trading_pair` | candle pair; must be the traded pair (or a tight proxy) | no |
| `interval` | str | `3m` | candle interval (`1m`,`3m`,`5m`,`15m`,`1h`,...). Longer = fewer, cleaner trends | no |
| `length` | int | 20 | ATR period of the Supertrend. Higher = smoother, slower flips | no |
| `multiplier` | float | 4.0 | ATR band width. Higher = fewer flips and band further from price (raise threshold with it) | no |
| `percentage_threshold` | float | 0.01 | max distance close-to-band for an entry, as a fraction. The main entry filter; see the table above | no |
| `total_amount_quote` | decimal | 100 | quote notional per side, split across `max_executors_per_side` | yes |
| `max_executors_per_side` | int | 2 | concurrent executors per side; also divides the size | yes |
| `cooldown_time` | int (s) | 300 | min seconds since the newest active executor on that side. Set to at least one or two candles | yes |
| `leverage` | int | 20 | per-executor leverage; use 1 on spot | no |
| `position_mode` | enum | `HEDGE` | `HEDGE` or `ONEWAY` | no |
| `stop_loss` | decimal or null | 0.03 | net PnL fraction, > 0 | yes |
| `take_profit` | decimal or null | 0.02 | net PnL fraction, > 0 | yes |
| `time_limit` | int (s) or null | 2700 | max holding time, > 0 | yes |
| `take_profit_order_type` | enum | `LIMIT` | `LIMIT` or `MARKET` | yes |
| `trailing_stop` | obj or null | null | `"activation,delta"` string, e.g. `0.015,0.003`, or a mapping | yes |
| `manual_kill_switch` | bool | false | true stops the controller and all its executors (v2_with_controllers script); false restarts it | yes |
| `initial_positions` | list | [] | pre-existing positions to adopt; leave empty | no |
| `id` | str | required | do not put it in samples; Condor names uploads `supertrend_v1__<style>` | no |

## Config gotchas

- The config class forbids unknown fields (`extra="forbid"`): a typo is a validation error.
- Numbers as YAML numbers (`0.02`, `3600`), not strings. `stop_loss: ""` becomes null (barrier
  disabled). Barriers must be > 0 if set.
- `trailing_stop` as a string needs both parts: `0.012,0.004`. `"0.01"` alone fails. Use `null`
  to disable.
- Enums: `HEDGE` / `ONEWAY`, not `PositionMode.HEDGE` (rejected). `take_profit_order_type` accepts
  `LIMIT` / `MARKET` (the `OrderType.` prefix is stripped); null becomes MARKET.
- `multiplier` becomes part of a column name: `3` and `3.0` are both fine (coerced to float).
- Spot: use `connector_name: binance`, `leverage: 1`. Short signals on spot sell base you must
  already hold; on spot this controller is effectively long-only unless you hold inventory.

## Regimes

- **Works:** persistent trends with orderly pullbacks (strong BTC/SOL legs on 15m-1h). Entries
  near the band give a tight distance to the flip level, so the stop loss can sit close to it.
- **Fails:** ranges and chop. The direction flips often; each flip plus the level signal opens
  a new trade in the new direction, and stale trades from the old direction are not closed
  (only barriers close them). Fast settings on 1m-3m pay taker fees on every entry.
- **Tuning per regime:**
  - Choppy: raise `multiplier` (3-4) and `interval`, lower `percentage_threshold`, `max_executors_per_side: 1`, longer `cooldown_time`.
  - Strong trend: allow `max_executors_per_side: 2-3` for stacking on pullbacks, a trailing stop instead of a tight take profit, longer `time_limit`.
  - High volatility: widen `stop_loss` in step with the ATR distance and reduce `leverage`.
- Size the stop loss relative to the band distance: a stop tighter than the typical
  `percentage_distance` gets hit by noise before the trend fails.

## Sample styles

| style | market | idea | use when |
|---|---|---|---|
| `conservative` | BTC-USDT perp, 15m, 20 / 3.0, threshold 0.003 | one position per side, 3x, SL 2% / TP 3%, 12h limit, 1h cooldown | first deployments, trending but unclear markets, small budgets |
| `balanced` | SOL-USDT perp, 5m, 14 / 3.0, threshold 0.003 | up to 2 entries per side, 5x, SL 1.5% / TP 2%, trailing 1.2% / 0.4%, 4h limit | clear intraday trends on a liquid alt |
| `aggressive` | ETH-USDT perp, 3m, 10 / 2.0, threshold 0.001 | up to 3 entries per side, 10x, SL 1% / TP 1.2%, trailing 0.8% / 0.3%, 1h limit, 5m cooldown | short, high-activity experiments; must be backtested with realistic fees first |

All three use `binance_perpetual` in HEDGE mode and modest `total_amount_quote` (300-500).
They are starting points: copy one, change the pair and budget, re-check the threshold against
the pair's own band distance, and upload it under a new config name.

## Deploy checklist

1. `manage_agent_controllers(action="status", name="supertrend_v1")`: `in_sync` proceed,
   `missing` then `sync`, `drift` stop and follow the drift procedure.
2. Pick a style, adapt pair / `total_amount_quote` / threshold, `upload_config` under a new name.
3. Backtest over at least a few weeks including a range, with `trade_cost` set to the real taker
   fee. Inspect how often `signal != 0` in `processed_data`; ~90%+ means the threshold is not
   filtering.
4. Check the account: perpetual position mode matches `position_mode`, leverage allowed,
   enough margin for both sides at once.
5. Deploy small; after a day, re-run the backtest over the live window and compare trade by
   trade. Remember the short live warm-up (`length + 10` candles) can make live signals differ.

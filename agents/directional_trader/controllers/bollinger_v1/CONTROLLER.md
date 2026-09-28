---
type: directional_trading
description: Bollinger %B mean reversion on perps; long below lower band, short above upper; triple-barrier PositionExecutors, market entry
---

# bollinger_v1

## What it is

A **mean-reversion** directional controller. On every tick it computes Bollinger
Bands on one candle series and reads the last value of **%B** (pandas_ta `BBP`):
where the close sits inside the band, 0 = on the lower band, 1 = on the upper band,
0.5 = on the middle SMA. When %B drops below `bb_long_threshold` it opens a long,
when it rises above `bb_short_threshold` it opens a short. Each trade is a
`PositionExecutor` with a market entry and a triple barrier (stop loss, take profit,
time limit, optional trailing stop). There is no exit on the opposite signal: the
barriers are the only way out.

**Category: `directional_trading`.** `BollingerV1Controller` extends
`DirectionalTradingControllerBase` and its config extends
`DirectionalTradingControllerConfigBase` (which sets `controller_type:
directional_trading`). The server imports it from `controllers/directional_trading/`,
so sync it and upload its configs as `directional_trading`. The frontmatter says so
explicitly.

**Source version.** This file is the upstream Hummingbot copy
(`hummingbot/controllers/directional_trading/bollinger_v1.py`): pydantic v2
`field_validator`s, candles declared in `get_candles_config()`, and the pandas_ta 0.4
`bbands(lower_std=, upper_std=)` signature with `BBP_{len}_{std}_{std}` column names.
The older pydantic-v1 copy that used to live in the hummingbot-api repo
(`controller_name = "bollinger_v1"` without an annotation, `ClientFieldData`,
`candles_config` field) **does not import** on the current API image (pydantic 2.13,
pandas-ta 0.4.71b0). If `status` reports drift against such a copy, the server side is
the broken one.

## Signal logic (`update_processed_data`)

1. `get_candles_df(candles_connector, candles_trading_pair, interval, max_records=bb_length)`.
2. `df.ta.bbands(length=bb_length, lower_std=bb_std, upper_std=bb_std, append=True)`
   adds `BBL_/BBM_/BBU_/BBB_/BBP_{bb_length}_{bb_std}_{bb_std}`.
   - `BBM` = SMA(close, bb_length); `BBU/BBL` = BBM +/- bb_std * stdev(close, bb_length).
   - `BBP` (%B) = (close - BBL) / (BBU - BBL).
3. Vectorized signal column:
   - `signal = 1` where `BBP < bb_long_threshold` (close below the long threshold, default the lower band);
   - `signal = -1` where `BBP > bb_short_threshold` (default above the upper band);
   - `0` otherwise. The short assignment runs second, so if the thresholds overlap
     (long > short) the short wins on the overlapping zone.
4. `processed_data["signal"] = df["signal"].iloc[-1]`; `processed_data["features"] = df`.

Only the **last row** drives live decisions. NaN %B (not enough history) compares
False, so it yields 0.

What the base class does with the signal (`DirectionalTradingControllerBase`):
- `create_actions_proposal`: if `signal != 0` and `can_create_executor(signal)`, open one
  executor on that side at the current mid price.
- `can_create_executor`: active executors on that side < `max_executors_per_side` **and**
  now - (timestamp of the newest active executor on that side) > `cooldown_time`.
  The cooldown counts from the newest *active* executor; once all on that side have
  closed, the next signal fires immediately.
- `stop_actions_proposal` returns nothing: no early close on signal flip, no stop when the
  price returns to the middle band. Mean reversion "to the mean" is only captured if
  `take_profit` roughly matches the distance to the middle band.

Consequence: while %B stays beyond the threshold the signal stays on, so the controller
stacks up to `max_executors_per_side` positions spaced by `cooldown_time` into a move.
That is averaging into a falling (or rising) market. Size `max_executors_per_side` with
that in mind.

## Market data and warm-up

- Candles: one feed, `CandlesConfig(connector=candles_connector,
  trading_pair=candles_trading_pair, interval=interval, max_records=bb_length)`.
- Live: `max_records == bb_length`, so exactly one full window is fetched and only the last
  row has a valid %B. That is enough for the live decision. The last candle is the one
  still forming, so the signal is evaluated intra-candle and can switch on and off
  within a bar.
- Backtest: the data provider fetches `bb_length` candles of buffer before `start`, but
  `get_candles_df` then filters to `timestamp >= start`, so the buffer is discarded. The
  first `bb_length` candles of the window produce NaN %B and are dropped
  (`prepare_market_data` does `dropna`). With `bb_length: 100` on `15m` that is the first
  ~25 hours of the backtest with no trades. Pick `start` accordingly.
- Backtest lookahead: features are merged onto the backtesting-resolution candles with
  `merge_asof(direction="backward")` on the candle **open** timestamp. If
  `backtesting_resolution` is finer than `interval` (e.g. 1m on 15m candles), the rows
  inside a candle already see that candle's final close, so the backtest is optimistic.
  Set `backtesting_resolution` equal to `interval` for an honest result, or treat a
  finer-resolution result as an upper bound.
- `candles_connector` must support candles on that pair and interval (binance,
  binance_perpetual, bybit, okx, and so on). The signal can come from a different
  venue or pair than the one traded (e.g. spot candles, perp execution).

## Executors, sizing and barriers

- Executor: `PositionExecutorConfig(connector_name, trading_pair, side, entry_price=mid,
  amount, triple_barrier_config, leverage)`.
- Amount per executor (base units) = `total_amount_quote / mid_price /
  max_executors_per_side`. So `total_amount_quote` is the **notional** at full stack (both
  sides can each reach it in HEDGE mode). It is not multiplied by leverage; margin used
  is roughly notional / leverage.
- `triple_barrier_config` (fixed by the base config):
  - `open_order_type`: MARKET (taker entry, always).
  - `take_profit` at `take_profit` (fraction, 0.02 = 2%) with `take_profit_order_type`
    (LIMIT by default: a resting maker exit order).
  - `stop_loss` at `stop_loss`, MARKET.
  - `time_limit` in seconds, MARKET close.
  - `trailing_stop` (optional): activates once the executor's **net PnL pct** exceeds
    `activation_price` (despite the name, it is a PnL fraction, not a price), then closes
    if net PnL falls `trailing_delta` below the running peak.
  - All barriers are percentages of entry and must be > 0 (or null to disable).
- Leverage and position mode are applied by the bot script (`v2_with_controllers`) only
  on perpetual connectors: `set_leverage(leverage, trading_pair)` and
  `set_position_mode(position_mode)`.

## Parameter reference

Updatable = marked `is_updatable` in the code, so it can be changed on a running
controller without redeploying. Controller-specific fields are not updatable.

| Field | Type | Default | Meaning | Tuning notes |
|---|---|---|---|---|
| `controller_name` | str | `bollinger_v1` | Must equal the folder/file name | Do not change |
| `controller_type` | str | `directional_trading` | Server category | Always `directional_trading` |
| `id` | str | required | Saved-config id | Omit in samples; Condor sets `bollinger_v1__<style>` on upload |
| `connector_name` | str | `binance_perpetual` | Execution venue | Perps recommended (see gotchas) |
| `trading_pair` | str | `WLD-USDT` | Traded pair | Liquid pairs only; taker entries |
| `candles_connector` | str | None | Candle source venue | **Set it explicitly** (see gotchas) |
| `candles_trading_pair` | str | None | Candle source pair | **Set it explicitly**; normally = `trading_pair` |
| `interval` | str | `3m` | Candle interval | 1m..1h; longer = fewer, cleaner signals |
| `bb_length` | int | 100 | SMA/stdev window, also `max_records` | 20 = classic fast; 100 = default, slower bands |
| `bb_std` | float | 2.0 | Band width in stdevs | 1.5-3.0; higher = rarer, more extreme entries |
| `bb_long_threshold` | float | 0.0 | Long when %B < this | 0 = below lower band; 0.1-0.2 trades more; < 0 waits for deeper stretch |
| `bb_short_threshold` | float | 1.0 | Short when %B > this | 1 = above upper band; 0.8-0.9 trades more; keep > long threshold |
| `total_amount_quote` | Decimal | 100 | Notional per side at full stack | Updatable |
| `max_executors_per_side` | int | 2 | Max concurrent executors per side | Updatable; also divides the size |
| `cooldown_time` | int (s) | 300 | Min gap after the newest active executor on a side | Updatable; must be > 0 |
| `leverage` | int | 20 | Perp leverage | Keep low (2-10); use 1 for spot |
| `position_mode` | enum | `HEDGE` | `HEDGE` or `ONEWAY` | HEDGE lets longs and shorts coexist |
| `stop_loss` | Decimal | 0.03 | SL fraction | Updatable; null disables |
| `take_profit` | Decimal | 0.02 | TP fraction | Updatable; aim near the band-to-middle distance |
| `time_limit` | int (s) | 2700 | Max holding time | Updatable; scale with `interval` |
| `take_profit_order_type` | enum | `LIMIT` | TP order type | Updatable; `LIMIT`, `LIMIT_MAKER` or `MARKET` |
| `trailing_stop` | obj | null | `{activation_price, trailing_delta}` as PnL fractions | Updatable |
| `manual_kill_switch` | bool | false | Stops new executors | Updatable; this is the real on/off state |
| `initial_positions` | list | [] | Pre-existing positions to adopt | Rarely used |

## Config gotchas

- **`candles_connector` / `candles_trading_pair` do not default in practice.** The
  fallback validators are `mode="before"` without `validate_default`, so they only run
  when the key is present. Omitting the key leaves `None`, and the controller then
  fails to build its `CandlesConfig`. Write them out, or pass `""` to trigger the
  fallback to `connector_name` / `trading_pair`. Every sample writes them explicitly.
- `extra="forbid"`: any unknown key (typo, a field from another controller such as
  `macd_fast`) rejects the whole config.
- Enums as names: `position_mode: HEDGE` / `ONEWAY`; `take_profit_order_type: LIMIT` /
  `MARKET` / `LIMIT_MAKER` (case-insensitive; the integer value `2` also parses as
  LIMIT, and older saved configs use it).
- `trailing_stop` accepts a YAML mapping `{activation_price: 0.015, trailing_delta:
  0.003}`, the string `"0.015,0.003"`, `""` or `null` (disabled).
- `stop_loss`, `take_profit` and `time_limit` must be > 0 or null; `""` means null.
- Numbers as YAML numbers. `bb_std: 2` is fine: it becomes 2.0 and the `BBP_100_2.0_2.0`
  column lookup still matches.
- Keep `bb_long_threshold < bb_short_threshold`. If they overlap, every bar in the overlap
  is a short signal.
- **Spot:** `leverage` and `position_mode` are ignored on non-perp connectors. A short
  signal on spot is a market SELL of base you must already hold, and the executor then
  buys back. Treat spot as long-only in practice (set `bb_short_threshold` very high,
  e.g. 100, to disable shorts) and set `leverage: 1`.
- **ONEWAY on perps:** an opposite-side executor nets against an open position instead
  of opening a separate one, which confuses per-executor PnL and barriers. Use HEDGE and
  make sure the account's position mode matches.

## Regimes

- **Works:** range-bound, mean-reverting markets. Chop around a flat SMA, intraday
  ranges on majors, low-trend weekends. Band touches get faded back toward the middle.
- **Fails:** trends and breakouts. A strong trend "walks the band": %B stays below 0
  (or above 1) for hours, the controller stacks `max_executors_per_side` positions
  against the trend and each ends on stop loss or time limit. Volatility expansions
  after a squeeze (band width `BBB` rising fast) are the classic loss.
- Tuning per regime:
  - Calm range: `bb_std` 2.0, thresholds 0/1, TP near the middle-band distance, SL about
    1.5-2x TP.
  - Noisy chop: shorter `bb_length` (20-50), thresholds 0.1/0.9, short `time_limit`, a
    trailing stop to bank quick snaps.
  - Trending or high-vol: widen `bb_std` to 2.5-3.0, `max_executors_per_side: 1`, long
    cooldown, or pause the controller (`manual_kill_switch: true`). This controller has no
    trend filter. If the research phase shows a trend regime, prefer a trend-following
    controller (supertrend_v1, macd_bb_v1).
- Risks: stacking into a move, taker fees on every entry and every SL/TL exit (backtest
  with a realistic `trade_cost`, 0.0004-0.0006 for binance perp taker), leverage
  liquidation if `stop_loss` > ~1/leverage.

## Sample styles

| Style | Market | Purpose |
|---|---|---|
| `conservative` | BTC-USDT perp, 15m, len 100, 2.5 std | Rare, deep fades; 1 position per side, 1h cooldown, 3x, 12h time limit |
| `balanced` | SOL-USDT perp, 5m, len 100, 2.0 std | Textbook bands; 2 per side, 15 min cooldown, 5x, 4h limit, trailing stop |
| `aggressive` | ETH-USDT perp, 3m, len 50, 1.8 std, thresholds 0.1/0.9 | Frequent trades in chop; 3 per side, 5 min cooldown, 10x, tight barriers |

All use `binance_perpetual`, HEDGE mode, and candles from the traded market.
`total_amount_quote` is modest (300-500); scale it after the backtest and a small live
run.

## Deploy checklist

1. `manage_agent_controllers(action="status", name="bollinger_v1")`: `in_sync` means
   proceed; `missing` means `sync`; `drift` means stop and follow the controller_sources
   skill (an old pydantic-v1 server copy is a likely cause).
2. `sync` if needed (category `directional_trading`).
3. `upload_config` for a style. It is saved as `bollinger_v1__<style>`. Adjust the pair,
   `total_amount_quote` and barriers to the research spec first.
4. Backtest before live: a window of at least 2-4 weeks plus `bb_length * interval` for
   warm-up, `backtesting_resolution` equal to `interval`, realistic `trade_cost`.
   Check win rate against the TP/SL ratio, max drawdown, and the number of SL or
   time-limit closes clustered in trends. Run an out-of-sample window before promoting
   (see the `backtesting` playbook).
5. Deploy small on perps with HEDGE mode set on the account, then compare live against a
   backtest over the live period (`deploy_and_monitor` playbook).

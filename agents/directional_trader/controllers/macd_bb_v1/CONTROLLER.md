---
type: directional_trading
description: Mean-reversion taker. Long below lower BB with MACD<0 and histogram>0 (short mirrored); PositionExecutor with triple barrier.
---

# macd_bb_v1

## What it does

`macd_bb_v1` is a **mean-reversion entry with momentum confirmation**. It buys when price has
stretched below the lower Bollinger Band *and* MACD shows that the down-move is losing steam
(MACD line still below zero, but the histogram has turned positive). It sells short in the
mirror situation: price above the upper band, MACD above zero, histogram turned negative.

Every entry is a market order wrapped in a `PositionExecutor` whose exit is managed by a
triple barrier (stop loss, take profit, time limit, optional trailing stop). The controller
never closes a position on an opposite signal; exits come only from the barriers.

**Category: `directional_trading`.** The config class extends
`DirectionalTradingControllerConfigBase` and the controller extends
`DirectionalTradingControllerBase`, so the Hummingbot API stores and imports it from
`controllers/directional_trading/`. Every config must carry
`controller_type: directional_trading`.

## Signal logic (`update_processed_data`)

Each controller tick (about once per second live; once over the whole window in a backtest):

1. Pull candles: `market_data_provider.get_candles_df(candles_connector, candles_trading_pair,
   interval, max_records)`.
2. Bollinger Bands: `df.ta.bbands(length=bb_length, lower_std=bb_std, upper_std=bb_std)`.
   The controller reads `BBP_{bb_length}_{bb_std}_{bb_std}` (percent-B):
   `bbp = (close - lower) / (upper - lower)`. `bbp = 0` is the lower band, `1` the upper band,
   `0.5` the middle (SMA). Values below 0 or above 1 mean price is outside the bands.
3. MACD: `df.ta.macd(fast=macd_fast, slow=macd_slow, signal=macd_signal)`, reading
   `MACD_f_s_sig` (fast EMA minus slow EMA) and `MACDh_f_s_sig` (MACD minus its signal EMA).
4. Conditions, evaluated vectorized on every row:
   - **long (+1)**: `bbp < bb_long_threshold` AND `macdh > 0` AND `macd < 0`
   - **short (-1)**: `bbp > bb_short_threshold` AND `macdh < 0` AND `macd > 0`
   - otherwise `0`
5. `processed_data["signal"]` = the last row's signal; `processed_data["features"]` = the full
   dataframe (this is what the backtester merges onto its resolution candles).

Important properties of the signal:
- It is a **state, not a crossover**. The long condition stays `+1` on every bar where all three
  hold, so a single stretched move can produce several consecutive signal bars. What limits
  repeated entries is `max_executors_per_side` and `cooldown_time`, not the signal itself.
- The three conditions rarely line up at the defaults (bbp outside the bands while the MACD
  histogram has already flipped). Expect **few trades** with `bb_long_threshold: 0.0` /
  `bb_short_threshold: 1.0`, especially on long intervals. Moving the thresholds inside the
  bands (e.g. 0.1 / 0.9 or 0.2 / 0.8) is the main lever for more trades.
- `macd < 0` for longs means it only buys dips that happen *within* a short-term downtrend
  (fast EMA below slow EMA). It will not buy a pullback inside an uptrend.

## Market data and warm-up

- One candle feed: `CandlesConfig(connector=candles_connector, trading_pair=candles_trading_pair,
  interval=interval, max_records=max_records)` from `get_candles_config()`.
- `max_records = max(macd_slow, macd_fast, macd_signal, bb_length) + 20`. With the defaults
  (bb_length 100) that is 120 candles.
- Indicator warm-up: BB needs `bb_length` bars; MACD needs roughly `macd_slow + macd_signal`
  bars. Earlier rows are NaN and produce signal 0.
- Live, the indicators are recomputed on only `max_records` candles. MACD is EMA-based, so on a
  120-candle window it is not fully converged and can differ slightly from the backtest, which
  computes over the whole window. Small live-vs-backtest drift in signal timing is expected;
  it is larger when `macd_slow` is close to `max_records` (i.e. when `bb_length` is small).
- Backtest warm-up: the backtest data provider drops candles before `start`, so the first
  `bb_length * interval` of every backtest window has no signal (100 x 15m = 25 hours for the
  conservative style). Make windows long enough.
- The candle `interval` is independent of the backtest `backtesting_resolution`; use a
  resolution at least as fine as `interval` (1m is the usual choice).

## Execution: executors, barriers, sizing

Inherited from `DirectionalTradingControllerBase`:

- `create_actions_proposal()`: if `signal != 0` and `can_create_executor(signal)`, it creates one
  `PositionExecutor` at the current mid price. Side = BUY for +1, SELL for -1.
- `can_create_executor(signal)` requires both:
  - active executors on that side `< max_executors_per_side`, and
  - `now - timestamp of the newest ACTIVE executor on that side > cooldown_time`.
  The cooldown only spaces **stacked** entries. When no executor on that side is active (for
  example right after a stop loss), the cooldown is already satisfied, so if the signal is still
  on, the controller re-enters on the next tick.
- `stop_actions_proposal()` returns nothing: the controller never early-closes executors.
- Sizing: `amount (base) = total_amount_quote / mid_price / max_executors_per_side`. So
  `total_amount_quote` is the **notional per side** when all slots are filled, not margin.
  Margin used is roughly notional / `leverage`. In HEDGE mode a full long stack and a full short
  stack can coexist, so worst-case gross exposure is `2 x total_amount_quote`.
- `leverage` is passed to the executor (it sets leverage on perpetual connectors; use 1 on spot).
- Triple barrier (`triple_barrier_config` property), distances are fractions of entry price,
  measured on net PnL % (fees included):
  - open: always **MARKET** (taker controller, not configurable).
  - `stop_loss`: closes with MARKET when net PnL <= -stop_loss.
  - `take_profit`: with `take_profit_order_type: LIMIT` a resting limit close order is placed at
    `entry * (1 +/- take_profit)` as soon as the position opens (no activation bounds are set);
    with `MARKET` it closes by market once net PnL >= take_profit.
  - `time_limit` (seconds): closes with MARKET when the executor is older than this.
  - `trailing_stop`: once net PnL exceeds `activation_price`, a trigger is set at
    `pnl - trailing_delta` and ratchets up; the position closes by MARKET if PnL falls below it.
- Barrier changes (and other updatable fields) apply to **new** executors only; each executor
  copies the triple barrier at creation.

## Parameter reference

Fields marked "updatable" carry `is_updatable: True` and can be changed on a running bot with
`manage_bots(action="update_config")`. Indicator fields are not updatable; redeploy to change them.

| Field | Type | Default | Meaning / tuning |
|---|---|---|---|
| `controller_name` | str | `macd_bb_v1` | Must be exactly `macd_bb_v1`. |
| `controller_type` | str | `directional_trading` | Must be `directional_trading`. |
| `connector_name` | str | `binance_perpetual` | Exchange to trade on. Perp connectors end in `_perpetual`. |
| `trading_pair` | str | `WLD-USDT` | Pair to trade, `BASE-QUOTE`. |
| `candles_connector` | str | None -> connector_name | Candle source. **Set it explicitly** (see gotchas). |
| `candles_trading_pair` | str | None -> trading_pair | Candle pair. **Set it explicitly.** Normally equal to `trading_pair`. |
| `interval` | str | `3m` | Candle interval: `1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d`... Longer = fewer, slower signals. |
| `bb_length` | int | 100 | BB SMA window. Also drives `max_records` and warm-up. 20-50 = reactive, 100+ = regime-level stretch. |
| `bb_std` | float | 2.0 | Band width in std devs. Lower (1.5-1.8) = more signals, weaker stretch. |
| `bb_long_threshold` | float | 0.0 | Long requires `bbp < this`. 0 = below lower band; 0.1-0.2 = near it (more trades). |
| `bb_short_threshold` | float | 1.0 | Short requires `bbp > this`. 1 = above upper band; 0.8-0.9 = near it. |
| `macd_fast` | int | 21 | MACD fast EMA. Classic is 12. Must be < `macd_slow`. |
| `macd_slow` | int | 42 | MACD slow EMA. Classic is 26. |
| `macd_signal` | int | 9 | MACD signal EMA. Lower = histogram flips sooner (earlier, noisier confirmation). |
| `total_amount_quote` | Decimal | 100 | Quote notional per side, split evenly over `max_executors_per_side`. Updatable. |
| `max_executors_per_side` | int | 2 | Max concurrent active executors per side. Updatable. |
| `cooldown_time` | int (s, >0) | 300 | Min gap between stacked entries on one side. Updatable. |
| `leverage` | int | 20 | Leverage for perps; 1 for spot. The default 20 is high; override it. |
| `position_mode` | enum | `HEDGE` | `HEDGE` or `ONEWAY`. Not updatable. |
| `stop_loss` | Decimal (>0) | 0.03 | Stop distance as a fraction (0.03 = 3%). Updatable. |
| `take_profit` | Decimal (>0) | 0.02 | Take-profit distance. Updatable. |
| `time_limit` | int (s, >0) | 2700 | Max holding time in seconds. Updatable. |
| `take_profit_order_type` | enum | `LIMIT` | `LIMIT`, `LIMIT_MAKER` or `MARKET`. Updatable. |
| `trailing_stop` | object or null | null | `{activation_price, trailing_delta}` as fractions. Updatable. |
| `manual_kill_switch` | bool | false | Inherited; true stops new executors. Updatable. |
| `initial_positions` | list | [] | Inherited; leave empty. |
| `id` | str | required | Do not put it in samples; Condor sets `macd_bb_v1__<style>` on upload. |

## Config gotchas

- **Always set `candles_connector` and `candles_trading_pair`.** Their fallback validators run in
  `mode="before"` without `validate_default`, so they only fire when the field is present. If
  the key is omitted, the value stays `None` and the candle feed has no connector/pair. Passing
  an empty string `""` does trigger the fallback, but explicit values are clearer.
- The config class forbids extra fields (`extra="forbid"`). An older version of this controller
  had a `candles_config` field; configs that still include it are rejected.
- Enums are written by **name**: `position_mode: HEDGE`, `take_profit_order_type: LIMIT`. The
  validator strips an `OrderType.` prefix and upper-cases the string. Existing server configs
  sometimes show `take_profit_order_type: 2` (the enum value for LIMIT); that works too, but
  names are safer. `null` for `take_profit_order_type` becomes MARKET.
- `trailing_stop` can be a YAML mapping (`activation_price: 0.015`, `trailing_delta: 0.005`),
  a string `"0.015,0.005"`, or `null`. Both values must be > 0.
- `stop_loss`, `take_profit`, `time_limit` must be > 0 if set. Writing them as `null` disables
  that barrier, which leaves positions without a stop; don't do it for `stop_loss`.
- Numbers must be YAML numbers, not quoted strings (`bb_std: 2.0`, not `"2.0"`).
- `bb_std` is a float; the BBP column name uses its float form (2 -> `2.0`), handled by pydantic
  coercion, so integers are fine in YAML.
- **Spot**: set `leverage: 1`, and note spot cannot short: SELL executors need base inventory.
  Prefer perpetual connectors for this controller, since half its signals are shorts.
- **ONEWAY mode**: long and short executors net against the same position, so an opposite
  signal while a position is open partially closes it and the barriers of both executors then
  act on a shared position. Use `HEDGE` unless the account cannot.
- The account's position mode and leverage on the exchange should match the config
  (`set_account_position_mode_and_leverage`).

## Regimes, risks and tuning

- **Works best**: range-bound or choppy markets with frequent overextensions that snap back
  (mean-reverting intraday behaviour, liquid majors in quiet periods).
- **Fails**: strong trends and breakouts. The long condition requires MACD < 0, so it buys into
  established down-moves each time the histogram briefly turns up; a sustained trend can stop it
  out repeatedly. Re-entry after a stop is immediate if the signal is still on (see cooldown note).
- **News / volatility spikes**: bands widen after the fact; the first move is often outside the
  bands with momentum still accelerating, which the histogram filter partly screens out.
- Tuning by regime:
  - Ranging: thresholds 0.1 / 0.9, `take_profit` around the distance to the middle band,
    `time_limit` a few hours, 2 executors per side.
  - Trending: thresholds at 0 / 1 or beyond (e.g. -0.1 / 1.1), longer `bb_length`, lower
    `max_executors_per_side` (1), tighter `stop_loss`, longer `cooldown_time`; or pause it.
  - High volatility: widen `stop_loss` and `take_profit` together and cut `total_amount_quote`
    and `leverage` rather than tightening stops into noise.
- Keep reward/risk honest: with LIMIT take profit and MARKET stop, the TP earns maker-side fill
  and the SL pays taker fees plus slippage. Backtest with realistic `trade_cost` (0.0004-0.0006
  for Binance perp taker).
- Parameter sweep grid to start from: `interval: [5m, 15m]`, `bb_length: [50, 100, 150]`,
  `bb_std: [1.8, 2.0, 2.2]`, `bb_long_threshold/bb_short_threshold: [0.0/1.0, 0.1/0.9]`,
  `stop_loss: [0.015, 0.025]`, `take_profit: [0.01, 0.02, 0.03]`.

## Sample styles

| Style | Market | Idea | Use when |
|---|---|---|---|
| `conservative` | BTC-USDT perp, 15m | BB 100/2.0, MACD 21/42/9, 1 executor/side, 3x, 1h cooldown, SL 2% / TP 3% (limit), trailing 1.5%/0.5%, 12h time limit | First deployment, small budget, or unclear regime. Few trades. |
| `balanced` | SOL-USDT perp, 5m | Default indicators, 2 executors/side, 5x, 15 min cooldown, SL 2.5% / TP 2%, trailing 1.2%/0.4%, 4h time limit | Default choice for a liquid alt in a ranging market. |
| `aggressive` | ETH-USDT perp, 3m | BB 50/1.8, MACD 12/26/9, thresholds 0.1/0.9, 3 executors/side, 10x, 5 min cooldown, SL 1.5% / TP 1.2%, 45 min time limit, no trailing | Choppy intraday conditions with a validated backtest. Fee-sensitive; high trade count. |

Samples are starting points. To trade a variant, read a style, change only what the situation
needs (pair, `total_amount_quote` within the user's budget, barriers), and upload it under a new
`config_name`. Keep `candles_trading_pair` equal to `trading_pair` when you change the pair.

## Deploy checklist

1. `manage_agent_controllers(action="status", name="macd_bb_v1")` -> must be `in_sync` or `missing`.
   On `drift`, follow the `controller_sources` drift procedure (older servers may hold a
   pre-pydantic-v2 copy of this file that no longer imports).
2. `sync` if missing. It is uploaded under `directional_trading`.
3. `upload_config` the style (or your variant) -> `macd_bb_v1__<style>`.
4. Backtest before live: 1m resolution, realistic `trade_cost`, a window that covers the warm-up
   plus at least a few weeks, and an out-of-sample period. Check trade count, win rate vs
   reward/risk, max drawdown and close-type mix (too many STOP_LOSS / TIME_LIMIT = wrong regime).
5. Confirm account position mode and leverage on the exchange match the config.
6. Deploy with a small `total_amount_quote`, then compare live against a backtest of the live
   period after the first day (expect minor MACD warm-up drift, flag deltas > 30%).

---
type: generic
description: "GENERIC category (ControllerBase) PMM: layered maker quotes, per-fill TP, held inventory in min/target/max band, global TP/SL (ONEWAY perps)"
---

# pmm_mister

## Category: generic (read this first)

`PMMister` extends plain `ControllerBase` and `PMMisterConfig` extends `ControllerConfigBase`.
It does **not** extend `MarketMakingControllerBase`, so even though it is a market maker its
Hummingbot API category is **`generic`**:

- the server imports it from `controllers/generic/pmm_mister.py`;
- every config must carry `controller_type: generic` (the class default is also `generic`);
- `manage_agent_controllers sync` / `upload_config` must treat it as `generic`. Uploading it
  under `market_making/` makes the server look in the wrong folder and the config will not load.

None of the `MarketMakingControllerBase` machinery (its `buy_spreads`/`sell_spreads` sizing,
`stop_loss`/`time_limit`/`trailing_stop` fields, `get_executor_config` override) applies here.
Everything below is implemented in this single file.

## What it does, in plain words

pmm_mister is a pure market maker with an inventory-management layer. On every tick it:

1. quotes one maker order per configured level on each side of the mid price;
2. when an entry fills, the order's `PositionExecutor` immediately rests a LIMIT_MAKER take
   profit at `entry * (1 +/- take_profit)`. If that TP fills, the round trip is closed and
   the spread plus TP is captured;
3. if the TP has not filled within the *effectivization time*, the controller stops the
   executor with `keep_position=True`: the TP is cancelled and the fill becomes part of
   the controller's **held position** (`positions_held`, close type `POSITION_HOLD`);
4. the size of the held position, measured as a fraction of `total_amount_quote`, is kept
   inside a band `[min_base_pct, max_base_pct]` around `target_base_pct` by switching sides
   on and off and (optionally) skewing order sizes;
5. an optional global take profit / stop loss closes the whole held position with a market
   reduce-only order once its unrealized PnL crosses a threshold.

It is long-biased by default (`position_side: BUY`: buys accumulate, sells reduce). Set
`position_side: SELL` for a short-biased maker on perps (sells accumulate, buys reduce).

## Tick flow and method map

`update_processed_data()` (framework, every tick)
- `reference_price` = connector mid price (`PriceType.MidPrice`). If the price is missing
  it falls back to the previous one, or to a hard-coded `100` on the very first tick.
- `spread_multiplier` = `1`, or `min_price_increment / mid` when `tick_mode: true`.

`determine_executor_actions()`
1. `_verify_position_mode()`: on a perpetual connector, trading is **blocked** (returns no
   actions, logs a warning) until the account's position mode equals `position_mode`.
   Spot connectors skip the check.
2. `_update_position_state()`: from `positions_held` for this connector/pair computes
   - `current_base_pct = |held amount * breakeven| / total_amount_quote`
   - `breakeven_price`, `unrealized_pnl_pct` (denominator: entry value
     `|amount| * breakeven` when `global_pnl_reference: position`, or `total_amount_quote`
     when `portfolio`)
   - `buy_skew`/`sell_skew` (see Sizing).
   Only *held* inventory counts. Fills that still have a live TP (hanging executors) are not
   in `current_base_pct` yet.
3. `_compute_executor_analysis()`: per level (`buy_0`, `buy_1`, ..., `sell_0`, ...) decides
   whether the level may place a new order, which open orders to refresh and which filled
   executors to effectivize.
4. `_check_global_tp_sl()`: the global exit state machine (below). While a global close is in
   progress, no other actions are produced.
5. `create_actions_proposal()` + `stop_actions_proposal()`.

## Quoting logic

### Price of each level
`price = mid * (1 - spread * spread_multiplier)` for buys, `mid * (1 + spread * spread_multiplier)`
for sells. `spread` is the level's entry in `buy_spreads` / `sell_spreads` (fractions,
`0.001` = 0.1%; in `tick_mode` the entries are numbers of ticks).

### When a level may place a new order (`_compute_executor_analysis`)
A level is "working" (skipped this tick) if any of these hold:
- `active_not_trading`: it already has an unfilled open order;
- `max_active_executors`: active executors on the level (open + hanging) `>= max_active_executors_by_level`;
- `cooldown`: less than `buy_cooldown_time`/`sell_cooldown_time` seconds since the most recent
  `open_order_last_update` of **any** executor on that level (active or closed). That timestamp
  moves on order creation, fills and cancels, so the cooldown also starts after a refresh cancel
  (see Gotchas);
- `price_distance`: the level already has active executors and the mid has not moved far enough
  away from them. For buys: `(lowest active entry - mid) / mid < price_distance_tolerance * tolerance_scaling^level`.
  In words, a new buy on a level is only placed once the mid has dropped at least the tolerance
  **below the lowest entry still active on that level**; sells mirror it above the highest entry.
  This stops the bot from stacking fills at the same price while hanging TPs are open.

### Position band (`_get_executable_levels`)
Of the non-working levels:
- `current_base_pct < min_base_pct`: only the **accumulation** side quotes (buys if LONG);
- `current_base_pct > max_base_pct`: only the **reduction** side quotes (sells if LONG);
- in between: both sides, unless `position_profit_protection` narrows it:
  LONG below target with mid < breakeven -> only buys; LONG above target with mid > breakeven
  -> only sells (SHORT mirrors).

With a fresh start the held position is 0, so the controller quotes **only the accumulation
side until held inventory reaches `min_base_pct`**. Set `min_base_pct` low (or 0) if you want
two-sided quoting from the start.

### Refresh (`stop_actions_proposal`)
An unfilled executor is stopped (and replaced on a later tick) when either
- its age exceeds `executor_refresh_time`, or
- `|entry - theoretical level price| / theoretical > refresh_tolerance * tolerance_scaling^level`
  (`should_refresh_executor_by_distance`).

### Effectivization
A filled executor (`is_trading`) whose fill is older than
`buy_/sell_position_effectivization_time` is stopped with `keep_position=True`: its TP order
is cancelled and the filled amount moves into `positions_held`. From then on that inventory is
only reduced by opposite-side quotes (which net against the position) or by the global TP/SL.

### Position profit protection (`position_profit_protection: true`)
Besides the band rule above, `create_actions_proposal` drops any reduction-side order whose
price would realize a loss against the held breakeven (LONG: sell price < breakeven;
SHORT: buy price > breakeven). Protects realized PnL, but in a trend against you it means
the bot never sells below breakeven and keeps holding the bag until global SL.

## Executors it creates

| Purpose | Executor | Details |
|---|---|---|
| Quote on a level | `PositionExecutor` | `entry_price` = level price, `side` BUY/SELL, `leverage`, `level_id` = `buy_N`/`sell_N`. Triple barrier: `take_profit` only; **no stop_loss, no time_limit, no trailing_stop** (hard-coded `None`). `open_order_type` and `take_profit_order_type` from config; SL/time-limit order types are MARKET but never used. |
| Global close | `OrderExecutor` | `execution_strategy=MARKET`, `position_action=CLOSE` (reduce-only), `level_id=global_close`, amount = the exchange position. |

Per-fill risk is therefore only the TP; downside is handled by the band, the price-distance
gate and the global SL.

## Sizing

```
order_quote(level) = total_amount_quote * portfolio_allocation * w_level / (sum(buy_amounts_pct) + sum(sell_amounts_pct))
order_amount       = quantize(order_quote / level_price * side_skew)
```
- Weights are normalised over **both** sides together: with 2 buy + 2 sell levels of weight 1,
  each order is `total * allocation / 4`.
- `leverage` does not change order size; it only changes margin used on perps.
- Skew (LONG): `buy_skew = (max - cur) / (max - min)`, `sell_skew = (cur - min) / (max - min)`,
  each clamped to `[min_skew, 1]` via `max(min(skew, 1), min_skew)` (SHORT swaps buy/sell).
  - `min_skew: 1.0` (the class default) **disables skew**: both multipliers are always 1.
  - `min_skew < 1` (e.g. 0.3-0.5) enables it: the overweight side shrinks down to that floor.
  - `min_skew > 1` just multiplies every order by `min_skew` (no skew at all). Avoid.
- Minimum notional: the smallest order is `order_quote * min_skew`. Keep it above the
  exchange minimum (Binance perps: about 5 USDT on SOL-USDT, 20 on ETH-USDT, 100 on BTC-USDT).
  A level whose quantized amount is 0 is skipped with a warning; one below min notional fails
  at placement.
- Rough exposure ceiling: the band caps **held** inventory near `max_base_pct * total_amount_quote`,
  but hanging executors (filled, TP still resting) add on top, up to
  `max_active_executors_by_level` fills per level. The cap is soft.

## Global TP / SL (`_check_global_tp_sl`)

Checked every tick when there is a held position and no close is in progress:
- TP fires if `global_tp_enabled`, `current_base_pct >= activation` (`always` = 0,
  `min_base` = `min_base_pct`, `target_base` = `target_base_pct`) and
  `unrealized_pnl_pct >= global_take_profit`.
- SL fires if `global_sl_enabled`, `current_base_pct >=` (`target_base_pct` or `max_base_pct`)
  and `unrealized_pnl_pct <= -global_stop_loss`.

Two phases: **stopping** (stop every active executor with `keep_position=True`, wait until none
is active) then **closing** (read the real position from the exchange connector and send a
MARKET reduce-only `OrderExecutor`). It aborts if the exchange position flipped side, if the
quantized amount is 0, or after 3 close attempts (for example below min notional). After a
successful close it suppresses re-triggering until the exchange confirms a flat position.

Important limits of the closing phase: it reads `connector._perpetual_trading.get_position(pair, BOTH)`.
- **Spot:** there is no perpetual position, so phase 2 sees 0 and ends. Executors are stopped but
  **the held spot inventory is not sold**. Global TP/SL is effectively "stop quoting, keep the
  bag" on spot.
- **HEDGE mode:** the position key is `PAIRBOTH`, never found, same outcome. Use `ONEWAY`.
- It closes the **whole account position** for the pair, including anything another bot or a
  human holds on the same account and pair. Run one pmm_mister per account per pair.
- Backtests likely cannot run phase 2 (no live connector position), so backtested global exits
  may show as executors stopped without a close. Judge the global SL by live/paper behaviour.

## Market data

No candles. The controller only needs the order book mid price (and trading rules for
`tick_mode` and quantization) of `connector_name`/`trading_pair`, registered via
`update_markets` and `initialize_rate_sources`. There is no indicator warm-up: it quotes from
the first tick with a valid mid (and position mode verified). A backtest only needs price
data for the pair.

## Parameter reference

All fields except `id`, `controller_name`, `controller_type`, `connector_name`, `trading_pair`,
`position_mode`, `position_side` and `initial_positions` are marked `is_updatable` and can be
changed on a running bot with `manage_bots(action="update_config")`.

| Field | Type | Default | Meaning | Tuning notes |
|---|---|---|---|---|
| `controller_name` | str | `pmm_mister` | Module name | Must equal the folder/file name. |
| `controller_type` | str | `generic` | API category | Always `generic`. |
| `connector_name` | str | `binance` | Exchange connector | `binance_perpetual` for perps; bare name = spot. |
| `trading_pair` | str | `BTC-USDT` | Market | Liquid pairs only. Check min notional. |
| `total_amount_quote` | Decimal | 100 (base) | Capital reference; denominator of `*_base_pct` | Inherited. Sets both order size and band size. |
| `portfolio_allocation` | Decimal | 0.1 | Fraction of total quoted per cycle across all levels | 0.05-0.2 typical. |
| `target_base_pct` | Decimal | 0.5 | Target held position / total | Used by profit protection and as an activation threshold. |
| `min_base_pct` | Decimal | 0.3 | Below it only the accumulation side quotes | Lower = two-sided sooner. |
| `max_base_pct` | Decimal | 0.7 | Above it only the reduction side quotes | Your max inventory. Keep `min < target < max` (not validated). |
| `buy_spreads` / `sell_spreads` | List[float] | `"0.0005"` | One level per entry, fraction of mid (ticks in tick_mode) | Widen with volatility. Use YAML lists. |
| `buy_amounts_pct` / `sell_amounts_pct` | List[Decimal] | `"1"` | Relative weight per level | Length must equal its spreads list; null/"" = all 1. |
| `executor_refresh_time` | int s | 30 | Max age of an unfilled quote | Lower = tracks mid closer, more churn. |
| `buy_cooldown_time` / `sell_cooldown_time` | int s | 60 | Min time since last order event on a level before a new order | Anti-accumulation; see refresh gotcha. |
| `buy_position_effectivization_time` / `sell_...` | int s | 120 | How long a per-fill TP rests before the fill becomes held inventory | Longer = more TP round trips, more hanging exposure. |
| `price_distance_tolerance` | Decimal | 0.0005 | Required move beyond the level's extreme active entry before a new order | Main guard against stacking fills in a trend. |
| `refresh_tolerance` | Decimal | 0.0005 | Replace an open order if it drifts this far from its theoretical price | Lower = more responsive, more cancels. |
| `tolerance_scaling` | Decimal | 1.2 | Both tolerances multiplied by `scaling^level` | Must be > 0. Deeper levels get looser tolerances. |
| `leverage` | int | 20 | Perp leverage passed to executors | Use 1-5 for MM; ignored on spot (set 1). |
| `position_mode` | PositionMode | ONEWAY | Must equal the account's mode or the bot never trades | Always `ONEWAY` (global close needs it). |
| `position_side` | TradeType | BUY | BUY/LONG: buys accumulate; SELL/SHORT: sells accumulate | SHORT only on perps. |
| `take_profit` | Decimal or null | 0.0001 | Per-fill TP distance from entry | Must beat round-trip fees: >= 0.0008 on Binance perps, >= 0.002 on Binance spot. The 0.0001 default loses money. |
| `take_profit_order_type` | OrderType | LIMIT_MAKER | TP order type | `null` becomes MARKET. Keep LIMIT_MAKER. |
| `open_order_type` | OrderType | LIMIT_MAKER | Entry order type | `null` becomes MARKET. Keep LIMIT_MAKER. |
| `max_active_executors_by_level` | int | 4 | Cap on open + hanging executors per level | Main cap on hanging exposure. |
| `tick_mode` | bool | false | Spreads expressed in ticks | Tolerances stay fractions. |
| `position_profit_protection` | bool | false | Never reduce below (LONG) / above (SHORT) breakeven | Safer PnL, worse in trends. |
| `min_skew` | Decimal | 1.0 | Floor of the size skew | 1.0 disables skew; 0.3-0.5 enables it. |
| `global_tp_enabled` / `global_sl_enabled` | bool | false | Arm the global exits | Off by default: arm SL explicitly. |
| `global_take_profit` / `global_stop_loss` | Decimal | 0.03 / 0.05 | Unrealized PnL thresholds | Fractions (0.05 = 5%). |
| `global_tp_activation_from` | str | `min_base` | `always`, `min_base`, `target_base` | |
| `global_sl_activation_from` | str | `target_base` | `target_base` or `max_base` | Note: SL is ignored while the position is below that threshold. |
| `global_pnl_reference` | str | `position` | `position` (PnL / entry value) or `portfolio` (PnL / total) | `portfolio` makes thresholds much harder to hit on a small position. |
| `manual_kill_switch` | bool | false | Inherited; stops the controller | |
| `initial_positions` | list | [] | Inherited; not updatable | Normally omit. |

## Config gotchas

- `controller_type: generic`, `controller_name: pmm_mister`. No `id` in sample configs.
- `extra="forbid"`: any unknown field (for example `stop_loss`, `time_limit`, `candles_config`,
  `cooldown_time`) is a validation error.
- Lists: YAML lists (`- 0.001`) or comma strings (`"0.001,0.002"`) both parse; a single scalar
  becomes a one-element list. `*_amounts_pct` length must match its spreads list.
- Enums: `position_mode` as `ONEWAY`/`HEDGE`; order types as `LIMIT_MAKER`/`LIMIT`/`MARKET`
  (name strings, case-insensitive) or their ints (3/2/1); `position_side` as `BUY`, `SELL`,
  `LONG`, `SHORT` or 1/2. Never `OrderType.LIMIT_MAKER` or `PositionMode.ONEWAY`.
- `global_*_activation_from` and `global_pnl_reference` are exact lowercase strings.
- Numbers as numbers. Strings work for most Decimal fields but keep YAML numeric.
- `min_base_pct < target_base_pct < max_base_pct` is not validated; if `max <= min` the skew
  collapses to 1 and the band logic is inconsistent.
- Perps: the account's position mode on the exchange must match `position_mode`
  (ask the user to set it if it doesn't). A mismatch silently blocks all quoting with a
  "Position mode mismatch" warning in the bot log.
- Refresh vs cooldown: a refresh cancel updates the level's `open_order_last_update`, so the
  level stays empty for the cooldown after each refresh. With refresh 30 s and cooldown 60 s a
  level quotes about 30 s out of every 90 s. Keep `cooldown <= executor_refresh_time` if you
  want continuous quoting, and rely on `price_distance_tolerance` to stop stacking fills.
- Spot: only `position_side: BUY`, `leverage: 1`; you need quote balance for buys and
  base balance for sells; global TP/SL does not liquidate spot inventory (see above).
- The first tick with no mid uses a placeholder price of 100. It is harmless for post-only
  quotes that get rejected, but watch for it on illiquid pairs.

## Regimes

| Regime | Behaviour | Tuning |
|---|---|---|
| Quiet / ranging | Best case: TPs fill inside the effectivization window, inventory oscillates in the band. | Tighter spreads, shorter refresh, `aggressive` or `balanced`. |
| Volatile but mean-reverting | Fills on both sides, larger hanging exposure. | Wider spreads, larger `price_distance_tolerance`, fewer executors per level, `conservative`. |
| Trend against the bias (downtrend for LONG) | Buys keep filling, TPs don't, inventory climbs to `max_base_pct`, then only sells; with profit protection it sells nothing below breakeven. Loss realized only by global SL. | Widen buy spreads, raise buy cooldown and distance tolerance, lower `max_base_pct`, arm global SL with `target_base` activation, or pause. |
| Trend with the bias | Sells at a profit, held inventory drains to `min_base_pct`, then only buys. Fine. | Can tighten reduction side. |
| Thin books / wide spreads | Adverse selection, orders below min notional. | Avoid. |

Main risks: unbounded per-fill downside (no per-executor SL), soft inventory cap, funding on
perps while holding, liquidation if leverage is high and the global SL is off or cannot fire.

## Sample styles

| Style | Pair | Leverage | Spreads | Band (min/target/max) | Use when |
|---|---|---|---|---|---|
| `conservative` | SOL-USDT perp | 2x | 0.15% / 0.30% | 5% / 20% / 35% | Choppy or uncertain markets, first live run. Skew on (0.3), 2 executors per level, 60 s refresh/cooldown, 5 min effectivization, profit protection, global TP 2% and SL 4%. |
| `balanced` | SOL-USDT perp | 5x | 0.08% / 0.20% | 20% / 40% / 60% | Default for a normal ranging market. Skew 0.5, 3 per level, 30 s refresh/cooldown, 3 min effectivization, global TP 3% and SL 5%. |
| `aggressive` | ETH-USDT perp | 10x | 0.04% / 0.10% | 30% / 50% / 80% | Quiet, mean-reverting tape on a deep book. 20% allocation, 5 per level, 15 s refresh/cooldown, 2 min effectivization, no profit protection, global TP 2% and SL 6%. |

All three use `total_amount_quote: 1000`, `position_side: BUY`, `ONEWAY`, LIMIT_MAKER entries and
TPs, and keep the smallest skewed order above Binance's minimum notional. To deploy on another
pair, read the style, change `trading_pair`, `total_amount_quote` and spreads, re-check min
notional, and upload under a new `config_name`.

## Deploy checklist

1. `manage_agent_controllers(action="status", name="pmm_mister")`; `missing` -> `sync`,
   `drift` -> follow the drift procedure (never overwrite blind). It syncs as **generic**.
2. Perps: set the account to `ONEWAY` and the intended leverage for the pair.
3. Pick a style, adapt pair / size / spreads, verify `take_profit` > round-trip fees and the
   smallest order > min notional, then `upload_config` under a new name.
4. Backtest on recent data for the pair (remember global exits may not close in a backtest)
   and compare fills, held inventory and PnL across styles.
5. Deploy, then check the bot log for "Position mode verified" and that levels are quoting.
6. Monitor `current_base_pct`, `unrealized_pnl_pct` and `closing_position` from the
   controller's custom info; adjust via `update_config`, never by restarting the bot.

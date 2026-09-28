---
type: market_making
description: Classic symmetric PMM around mid; one PositionExecutor per spread level with TP/SL/time-limit barriers, refreshed on a timer. No candles.
---

# pmm_simple

## What it is

`pmm_simple` is the plainest market maker in the Hummingbot V2 toolbox. Every tick it
reads the mid price, and for each configured spread level on each side it keeps exactly
one limit order resting at `mid * (1 - spread)` (buys) or `mid * (1 + spread)` (sells).
When one of those orders fills, the fill becomes a small position with its own take
profit, stop loss and time limit. When the position closes, the level is quoted again.
Unfilled quotes are cancelled and re-placed at the new mid every `executor_refresh_time`
seconds.

It has no indicators, no inventory skew, no volatility scaling and no price-tolerance
repricing. That makes it predictable and easy to reason about, and also means you are
the regime filter: the controller will keep quoting into a trend until you widen,
pause or stop it.

### Category: market_making

- Base classes: `PMMSimpleController(MarketMakingControllerBase)` and
  `PMMSimpleConfig(MarketMakingControllerConfigBase)`.
- Category is decided by the base class, so this controller lives in the
  **`market_making/`** folder on the Hummingbot API and every config carries
  `controller_type: market_making`. Upload and sync it as `market_making`.
- Do not confuse it with `pmm_mister` and `pmm_v1` in this same agent: those make
  markets too, but extend plain `ControllerBase`, so they are `generic`.

## How it works, step by step

All of the logic is in `hummingbot/strategy_v2/controllers/market_making_controller_base.py`;
`pmm_simple.py` only supplies `get_executor_config`.

1. **Reference price** (`update_processed_data`): `reference_price = MidPrice` from the
   connector's order book, `spread_multiplier = 1` (fixed; this controller never scales
   spreads).
2. **Which levels need a quote** (`get_levels_to_execute`): level ids are `buy_0`,
   `buy_1`, ... and `sell_0`, `sell_1`, ... (one per entry in `buy_spreads` /
   `sell_spreads`). A level is busy if it has an **active** executor (resting order *or*
   open position), or if its last executor closed by **STOP_LOSS** less than
   `cooldown_time` seconds ago. Every other level gets a new executor this tick.
3. **Price and size** (`get_price_and_amount`):
   - `price = reference_price * (1 - spread)` for buys, `* (1 + spread)` for sells.
   - Amount per level: the weights in `buy_amounts_pct` and `sell_amounts_pct` are
     normalised **together** (by the sum of both lists), then multiplied by
     `total_amount_quote`. So `total_amount_quote` is split across **both** sides.
     Example: 300 total, two levels per side, weights `[1, 1]` / `[1, 1]` gives 75 quote
     per order. Base amount = quote amount / order price.
4. **Executor** (`PMMSimpleController.get_executor_config`): one `PositionExecutor` per
   level, with `entry_price = price`, `side` from the level id, `leverage`, and the
   `triple_barrier_config` built from the config.
5. **Refresh** (`executors_to_refresh`): any executor that is active but **not yet
   trading** (entry order not filled) and older than `executor_refresh_time` is
   stopped. Next tick the level is empty and gets re-quoted at the fresh mid. Between
   refreshes quotes do **not** follow the price.
6. **Early stop** (`executors_to_early_stop`): returns nothing in this controller. There
   is no regime or inventory-based kill; only the barriers and the refresh close things.
7. **Spot only: position rebalance** (`check_position_rebalance`): if the connector name
   does not contain `_perpetual` and `skip_rebalance` is false, the controller computes the
   base asset its sell levels need (`sum(sell amounts in quote) / reference_price`) and
   compares it with the base it holds (`positions_held` for this connector/pair). If the
   gap exceeds `position_rebalance_threshold_pct` of the requirement, it fires a
   **market** `OrderExecutor` (level id `position_rebalance`) to buy (or sell) the
   difference. On a fresh spot start this is an immediate taker buy of roughly half of
   `total_amount_quote` worth of base. It runs at most one rebalance at a time.

### What happens after a fill (PositionExecutor + triple barrier)

`triple_barrier_config` (property on the config base) is fixed as:

| Barrier | Setting | Order type |
|---|---|---|
| Entry | the level price | `LIMIT` (hard-coded, not LIMIT_MAKER) |
| Take profit | `take_profit` from entry | `take_profit_order_type` (LIMIT by default) |
| Stop loss | `stop_loss` on net PnL % | `MARKET` (hard-coded) |
| Time limit | `time_limit` seconds | `MARKET` (hard-coded) |
| Trailing stop | `trailing_stop` | `MARKET` |

Details that matter when tuning:

- **Take profit with LIMIT/LIMIT_MAKER** is a resting order placed as soon as the entry
  fills, at `entry * (1 + tp)` for a long and `entry * (1 - tp)` for a short. With
  `MARKET` it instead waits until net PnL % reaches `tp` and then crosses the book.
- **Stop loss** fires when **net** PnL % (after fees) <= `-stop_loss`, closed at market.
- **Time limit is counted from executor creation**, not from the fill
  (`end_time = config.timestamp + time_limit`). A quote that sits 4 minutes before
  filling has 4 minutes less holding time. Keep `time_limit` comfortably larger than
  `executor_refresh_time`.
- **Trailing stop**: once net PnL % exceeds `activation_price`, a trigger is set at
  `pnl - trailing_delta` and ratchets up; a drop below it closes at market. It only
  matters if `activation_price` is below `take_profit` (otherwise the TP fills first).
- A filled level stays busy until its position closes. So the maximum number of open
  positions is `len(buy_spreads) + len(sell_spreads)`, and the maximum directional
  exposure per side is the sum of that side's order sizes.
- `cooldown_time` only applies after a **stop loss**. After a take profit or time limit
  the level is re-quoted on the next tick.

### Perpetual position handling

- `leverage` and `position_mode` are applied once at bot start (`v2_with_controllers`
  `apply_initial_setting`) for perpetual connectors only. `position_mode` is an
  account/connector-wide setting: every controller on the same connector in the bot
  should agree.
- **Use `HEDGE`** (the default). Each fill is an independent position with its own
  barriers; in HEDGE a filled buy and a filled sell co-exist as a long and a short. In
  `ONEWAY` a sell fill nets against an open long on the exchange while both executors
  still think they hold a position, which makes the barrier bookkeeping unreliable.
- `total_amount_quote` is **notional**, not margin. Margin used is about
  `total_amount_quote / leverage` when everything fills.

## Market data and warm-up

- Needs only the live order book of `connector_name` / `trading_pair` (mid price). It does
  not override `get_candles_config()`, so it opens no candle feed and has no warm-up. It starts quoting on
  the first tick.
- For backtests the engine still needs price data for the pair on that connector; use the
  backtesting resolution (e.g. `1m`) that the backtest tool asks for. Because refresh,
  fills and TP are sub-candle events, backtest results for tight spreads are optimistic.
  Treat them as a relative comparison between configs, not a PnL forecast.

## Parameter reference

"Upd" = marked `is_updatable` in the code, so it can be changed on a running bot with
`manage_bots(action="update_config")`.

| Field | Type | Default | Upd | Meaning and tuning |
|---|---|---|---|---|
| `controller_name` | str | `pmm_simple` | | Must be `pmm_simple`. |
| `controller_type` | str | `market_making` | | Must be `market_making`. |
| `id` | str | required | | Do not put it in a sample; Condor names uploads `pmm_simple__<style>`. |
| `connector_name` | str | `binance_perpetual` | | `_perpetual` suffix = perps (no rebalance, leverage applies); bare name = spot. |
| `trading_pair` | str | `WLD-USDT` | | `BASE-QUOTE`. Pick deep books; thin books mean adverse selection. |
| `total_amount_quote` | Decimal | 100 | yes | Total quote notional across **all** levels on **both** sides. Check each order clears the exchange min notional. |
| `buy_spreads` | list[float] | `0.01,0.02` | yes | Distance below mid per buy level, as a fraction (0.002 = 0.2%). One entry = one level. |
| `sell_spreads` | list[float] | `0.01,0.02` | yes | Distance above mid per sell level. Widen this side in an uptrend (you get lifted less), the buy side in a downtrend. |
| `buy_amounts_pct` | list[Decimal] or null | null (equal) | yes | Relative weights per buy level. Length must equal `buy_spreads`. Normalised together with sell weights. |
| `sell_amounts_pct` | list[Decimal] or null | null (equal) | yes | Same for sells. Unequal side totals skew inventory (e.g. `[2,2]` buys vs `[1,1]` sells). |
| `executor_refresh_time` | int (s) | 300 | yes | Age after which an **unfilled** quote is cancelled and re-placed at the new mid. Lower = quotes track price better, more cancels. 30 to 180 typical. |
| `cooldown_time` | int (s) | 15 | yes | Pause before re-quoting a level whose last position was **stopped out**. Raise it (60 to 300) to avoid re-entering a running move. |
| `leverage` | int | 20 | no | Perps only. Keep 1 to 10 for MM. Must be 1 on spot. |
| `position_mode` | enum | `HEDGE` | no | `HEDGE` or `ONEWAY`. Perps only. Use `HEDGE`. |
| `stop_loss` | Decimal or null | 0.03 | yes | Net PnL % loss that closes a filled position at market. Must be > 0 if set. |
| `take_profit` | Decimal or null | 0.02 | yes | Distance from entry for the exit. Must exceed round-trip fees. Default 2% is far too wide for MM; override it. |
| `time_limit` | int (s) or null | 2700 | yes | Max life of an executor **from creation**, closed at market. |
| `take_profit_order_type` | enum | `LIMIT` | yes | `LIMIT`, `LIMIT_MAKER` or `MARKET`. `LIMIT_MAKER` guarantees maker fees but can be rejected if it would cross. |
| `trailing_stop` | object or null | null | yes | `{activation_price: 0.0008, trailing_delta: 0.0003}` or the string `"0.0008,0.0003"`. Values are net PnL fractions. |
| `position_rebalance_threshold_pct` | Decimal | 0.05 | yes | Spot only. Tolerated gap between required and held base before a market rebalance. |
| `skip_rebalance` | bool | false | no | Spot only. `true` = never buy base automatically; you must already hold it or sell levels will fail for lack of balance. |
| `manual_kill_switch` | bool | false | yes | Inherited. `true` stops the controller creating executors. |
| `initial_positions` | list | `[]` | no | Inherited. Leave empty. |

## Config gotchas

- **Lists**: `buy_spreads`, `sell_spreads`, `*_amounts_pct` accept a YAML list
  (`[0.002, 0.004]`), a comma string (`"0.002,0.004"`) or a single number. Prefer YAML
  lists of numbers.
- **Amount list length must match its spread list** or validation fails ("The number of
  buy_amounts_pct must match the number of buy_spreads").
- **Enums**: write `HEDGE` / `ONEWAY` and `LIMIT` / `LIMIT_MAKER` / `MARKET` as bare
  names. `PositionMode.HEDGE` is rejected. (`OrderType.LIMIT` happens to be accepted for
  `take_profit_order_type` only.)
- **Numbers as numbers**, not strings. `stop_loss`, `take_profit`, `time_limit` must be
  > 0; an empty string means "disabled" (null).
- `extra="forbid"`: any unknown field (a `pmm_mister` field such as `target_base_pct`,
  `open_order_type`, `portfolio_allocation`) makes the config invalid.
- **Spot**: set `leverage: 1`; `position_mode` is ignored. TP must clear spot maker fees
  (Binance ~0.075 to 0.1% per side, so TP >= 0.25% to be safe). The rebalance is a
  **market** buy at start: account for that taker fee and the resulting base inventory
  risk.
- **Perps**: TP must clear ~0.04% (Binance maker round trip). The default `stop_loss`
  0.03 with `leverage` 20 is a 60% margin hit per position; set leverage and SL together.
- **Minimum order size**: `total_amount_quote * weight / sum(all weights)` per order must
  be above the exchange minimum notional (Binance perps ~5 to 100 USDT depending on the
  symbol; BTC-USDT perp needs ~100 USDT notional per order).

## When it works and when it fails

| Regime | Behaviour | What to do |
|---|---|---|
| Quiet / ranging | Best case. Fills on both sides, TPs close back toward mid. | Tighten spreads toward 2 to 3x round-trip fees, shorter refresh. |
| Trending | Fills only on the side against the trend (buys in a downtrend), each fill runs into SL or time limit. No skew or kill. | Widen the with-trend-fill side (buys in a downtrend), cut its weight, raise `cooldown_time`, or pause (`manual_kill_switch`). |
| Volatile / news | Quotes are stale between refreshes and get picked off; SL at market adds slippage. | Widen all spreads 2 to 3x, cut `total_amount_quote`, or stop. |
| Thin book | Wide spreads needed, sparse fills, SL/time-limit exits slip. | Prefer another pair. |

Main risks: adverse selection on stale quotes (lower `executor_refresh_time`), inventory
build-up on one side in a trend (bounded by the sum of that side's order sizes), market
exits on SL/time limit paying taker fees and slippage, and on spot the base inventory
bought by the rebalance.

Tuning rules of thumb:
- Spread of level 0 >= 2x round-trip fees; TP about equal to the level 0 spread (a fill
  plus a TP roughly round-trips back to mid).
- `stop_loss` 3 to 5x `take_profit`; `time_limit` >= 5x `executor_refresh_time`.
- Use more levels with larger outer weights to average into moves instead of one big
  tight order.
- Compare configs by backtest over the same window; re-check regime before going live.

## Sample styles

| Style | Market | Use when |
|---|---|---|
| `conservative` | binance_perpetual SOL-USDT, 300 notional, 5x, spreads 0.4%/0.8%, TP 0.4%, SL 1.5%, 45 min | Default starting point. Quiet or ranging market, low fill rate with good edge per fill. |
| `aggressive` | binance_perpetual BTC-USDT, 1000 notional (125/125/250 per side), 10x, spreads 0.05/0.1/0.2% weighted 1:1:2, TP 0.12%, SL 0.6%, 15 min, trailing stop, 30 s refresh | Deep, quiet book and you want volume. Pause at the first sign of expansion. BTC-USDT perp needs ~100 USDT per order, so do not shrink the size much. |
| `spot` | binance SOL-USDT spot, 300, no leverage, spreads 0.3%/0.6%, TP 0.35%, SL 2%, 60 min, auto rebalance | Spot account without perps. Starts with a market buy of about 150 USDT of SOL for the sell side. |

Start from a style, change only pair, `total_amount_quote` (within the user's budget) and
spreads, and upload the variant under a new config name.

## Deploy checklist

1. `manage_agent_controllers(action="status", name="pmm_simple")` must be `in_sync`; if
   `missing`, `sync`; if `drift`, follow the drift procedure (show diff, ask).
2. Check the regime and fees for the pair (see the agent's regime heuristics and fee
   table). Confirm the connector type (`_perpetual` or spot) and the account's position
   mode.
3. Read a style, adapt pair, size and spreads, and `upload_config` it under a new name
   with `controller_type: market_making`. Fix validation errors field by field.
4. Backtest the config over a recent window that matches the current regime; compare at
   least two spread settings.
5. Deploy with `manage_bots`, then watch the first fills: both sides should fill in a
   ranging market; one-sided fills plus stop losses mean a trend, so widen or pause.
6. To change spreads or sizes live, use `manage_bots(action="update_config")` on the
   updatable fields. Never restart a bot: stop, archive and redeploy instead.

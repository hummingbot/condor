---
type: generic
description: Legacy pure market making port (GENERIC, not market_making). Spot; per-level limit orders at mid +/- spread, base-unit sizing, inventory skew, price bands.
---

# pmm_v1: Pure Market Making V1

## Summary

`pmm_v1` is a port of the classic Hummingbot V1 `pure_market_making` strategy onto the
V2 controller framework. It keeps a symmetric (or asymmetric) ladder of plain limit
orders around the mid price, one order per configured spread level, cancels and replaces
them every `order_refresh_time` seconds, pauses a level for `filled_order_delay` seconds
after it fills, and optionally resizes orders to push the inventory back toward a target
base/quote ratio.

It has **no stop loss, no take profit, no time limit, no hedging, no position closing**.
A fill is simply a trade: the base (or quote) you receive stays in the account. Risk is
controlled only by spread width, order size, inventory skew and the static price band.

## Category: generic, NOT market_making

`PMMV1` extends plain `ControllerBase` and `PMMV1Config` extends `ControllerConfigBase`
(not `MarketMakingControllerBase` / `MarketMakingControllerConfigBase`). The Hummingbot API
therefore imports it from `controllers/generic/`, even though it is a market-making strategy.

- Condor category: `generic` (this file's frontmatter `type: generic`; inference from the
  base class gives the same answer).
- Every config must carry `controller_type: generic` (it is also the class default).
- Sync / upload it as **generic**. The server resolves the module as
  `controllers.<controller_type>.pmm_v1`, so a copy under `market_making/` does not match
  configs that say `controller_type: generic` (and the class default is `generic`).
- It does NOT have any of the `MarketMakingControllerConfigBase` fields
  (`buy_amounts_pct`, `executor_refresh_time`, `stop_loss`, `take_profit`, `leverage`,
  `position_mode`, `cooldown_time`, ...). The config class forbids unknown fields
  (`extra="forbid"`), so copying fields from a `pmm_simple` or `pmm_mister` config will be
  rejected at upload.

Compared with the controllers you already know:

| | pmm_v1 | pmm_simple | pmm_mister |
|---|---|---|---|
| Category | generic | market_making | generic |
| Executor | OrderExecutor (bare limit order) | PositionExecutor (triple barrier) | PositionExecutor + global TP/SL |
| Sizing | `order_amount` in BASE units per level | `total_amount_quote` x `amounts_pct` | `total_amount_quote` x `portfolio_allocation` |
| Exits | none; fills are kept as inventory | SL / TP / time limit / trailing | per-level TP, global TP/SL, breakeven |
| Inventory control | size skew toward `target_base_pct` | none built in | skew + min/max base pct |
| Venue | spot (see perpetual caveat) | spot or perp | spot or perp |

Use `pmm_v1` when the user wants classic V1 PMM behaviour (a pure spread-capture ladder
that holds inventory) or is migrating a V1 `pure_market_making` config.

## Quoting logic, step by step

Everything happens in `update_processed_data()` then `determine_executor_actions()` each
controller tick (about 1s live; each candle of the backtesting resolution in a backtest).

1. **Fill detection** (`_detect_filled_executors`). For every level that had an active
   executor on the previous tick and has none now, if a terminated executor with that
   `level_id` closed as `POSITION_HOLD` (the OrderExecutor's close type for a filled
   order), the level is locked until `now + filled_order_delay`
   (`_level_next_create_timestamps`).
2. **Reference price** (`_get_reference_price`): the connector's **mid price**
   (`PriceType.MidPrice`). No candles, no volatility, no order-book-depth pricing. If the
   price is unavailable the controller quotes nothing that tick.
3. **Inventory** (`_get_balances`): total balances of base and quote on `connector_name`
   (whole account, not an allocation). `current_base_pct = base * mid / (base * mid + quote)`.
4. **Inventory skew** (`_calculate_inventory_skew_legacy` -> `_c_calculate_bid_ask_ratios`),
   an exact port of the V1 `inventory_skew_calculator`:
   - `total_order_size = order_amount * (n_buy_levels + n_sell_levels)` (base units)
   - `range = total_order_size * inventory_range_multiplier` (base units)
   - `range_value = min(range * mid, portfolio_value * 0.5)`
   - `target_value = portfolio_value * target_base_pct`
   - Below target: `bid_ratio` interpolates linearly from 2.0 (base value at
     `target_value - range_value` or lower) to 1.0 (at target). Above target: from 1.0 (at
     target) to 0.0 (at `target_value + range_value` or higher).
   - `ask_ratio = 2.0 - bid_ratio`. Both are in [0, 2].
   - With `inventory_skew_enabled: false`, both ratios are 1.
5. **Price bands**: `price_ceiling` / `price_floor` > 0 are active, <= 0 disables them.
6. **Levels to quote** (`get_levels_to_execute`): level ids are `buy_0, buy_1, ...` and
   `sell_0, sell_1, ...`, one per entry in `buy_spreads` / `sell_spreads`. A level gets a
   new order only if it has no active executor, its post-fill delay has expired, and the
   price band allows its side (`_apply_price_band_filter`):
   - mid >= `price_ceiling` -> no buy levels (only sells);
   - mid <= `price_floor` -> no sell levels (only buys).
7. **Order creation** (`create_actions_proposal`) for each level to quote:
   - buy price  = `mid * (1 - buy_spreads[i])`, sell price = `mid * (1 + sell_spreads[i])`
   - amount = `order_amount * skew_ratio` for that side, quantized to the lot size; a level
     whose quantized amount is 0 is skipped (this is how a 0.0 skew ratio suppresses a side).
   - price is quantized (rounded down) to the tick size.
   - one `OrderExecutor` with `execution_strategy=LIMIT` is created, carrying `level_id`.
8. **Refresh** (`_executors_to_refresh`). Only executors older than `order_refresh_time`
   are candidates.
   - `order_refresh_tolerance_pct < 0` (e.g. -1): every candidate is cancelled.
   - otherwise the candidates' current prices are compared with the fresh proposal prices
     per side (`_is_within_tolerance`: sorted lists, same length required, each
     `|proposal - current| / current <= tolerance`). If **both** sides are within tolerance
     nothing is cancelled. If **either** side is out, **all** candidates (both sides) are
     cancelled.
   - Cancels use `StopExecutorAction(keep_position=True)`. A cancelled order with no fill
     closes `EARLY_STOP` and its level is re-quoted on a later tick at the new mid. A
     partially filled order closes `POSITION_HOLD` (the filled part is kept) and also
     triggers the post-fill delay.
9. When the controller is stopping (`RunnableStatus.TERMINATED`) no new actions are emitted.

Stop / start: stopping the bot cancels open orders; nothing is unwound. Base and quote
acquired by fills stay in the account.

## Market data and warm-up

- No candles. `candles_config` is not used; there is nothing to warm up.
- Needs only a live order book / mid price for `connector_name` + `trading_pair`
  (`initialize_rate_sources` is called in `__init__`).
- Quotes from the first tick once the mid price is available.

## Executors, sizing and exits

- Executor: `OrderExecutor`, `LIMIT` strategy (not `LIMIT_MAKER`). A buy placed after the
  mid moved up through it could cross the book and fill as a taker. With spreads of a few
  bps and a 1s tick this is rare but possible in fast markets.
- `position_action` is always `OPEN`, `leverage` is always 1 (not configurable).
- Sizing: `order_amount` is in **base units** per level (0.1 = 0.1 SOL), multiplied by the
  skew ratio (0 to 2x). `total_amount_quote` is overridden to 0 and **unused**; do not rely
  on it for budgeting.
- Worst-case notional resting at once:
  `order_amount * mid * (sum over levels of skew)`, up to `2 * order_amount * n_levels`
  per side when fully skewed.
- The account needs **both** assets on spot: quote to fund buys, base to fund sells. With
  no base at all, sells fail for insufficient balance (and with skew on, the sell side is
  sized to 0 anyway).
- Triple barrier: **none**. No `stop_loss`, `take_profit`, `time_limit`,
  `trailing_stop`. The only protections are the spread, the post-fill delay, the skew and
  the price band.
- Max orders: exactly one per level. Levels = `len(buy_spreads) + len(sell_spreads)`.

## Parameter reference

All fields below are the complete set the config class accepts (`extra="forbid"`).
"Updatable" means `json_schema_extra.is_updatable: True`, i.e. can be changed on a running
bot with `manage_bots(action="update_config")`.

| Field | Type | Default | Updatable | Meaning / tuning |
|---|---|---|---|---|
| `id` | str | required | no | Config id. Do not put it in sample files; Condor sets `pmm_v1__<style>` on upload. |
| `controller_name` | str | `pmm_v1` | no | Must be `pmm_v1`. |
| `controller_type` | str | `generic` | no | Must be `generic`. |
| `connector_name` | str | `binance` | no | Spot connector (e.g. `binance`, `kucoin`, `okx`). |
| `trading_pair` | str | `BTC-USDT` | no | `BASE-QUOTE`. |
| `order_amount` | Decimal | `1` | yes | Size per level in BASE units. The default of 1 means 1 BTC: always set it explicitly. Keep `order_amount * mid` above the exchange minimum notional even at low skew. |
| `buy_spreads` | list[float] | `[0.01]` | yes | One buy level per entry, as fractions of mid (0.002 = 20 bps). Accepts a YAML list or a comma string `"0.002,0.004"`. An empty value means no buy levels. |
| `sell_spreads` | list[float] | `[0.01]` | yes | Same for sells. Can differ in count and values from buys (asymmetric quoting). |
| `order_refresh_time` | int (s) | 30 | yes | Orders older than this are candidates for cancel/replace. Shorter tracks mid tighter but pays more cancels and loses queue priority. |
| `order_refresh_tolerance_pct` | Decimal | -1 | yes | Fraction. If every order on both sides is within this distance of its fresh proposal, nothing is refreshed. -1 (any negative) disables the check: refresh every cycle. Typical 0.001 to 0.003. |
| `filled_order_delay` | int (s) | 60 | yes | After a level fills, wait this long before re-quoting that level. Main defence against being run over by a trend. |
| `inventory_skew_enabled` | bool | false | yes | Resize orders to steer inventory to `target_base_pct`. Spot only. |
| `target_base_pct` | Decimal | 0.5 | yes | Target share of portfolio value held in base (0.5 = 50/50). Fraction, not percent. |
| `inventory_range_multiplier` | Decimal | 1.0 | yes | Width of the skew ramp in multiples of the total ladder size. Smaller = skew saturates sooner (stronger rebalancing); larger = gentler. |
| `price_ceiling` | Decimal | -1 | yes | Absolute price. When mid >= ceiling, buys stop (sells only). <= 0 disables. |
| `price_floor` | Decimal | -1 | yes | Absolute price. When mid <= floor, sells stop (buys only). <= 0 disables. |
| `total_amount_quote` | Decimal | 0 | yes | Inherited, overridden to 0, not used by this controller. Leave it out. |
| `manual_kill_switch` | bool | false | yes | Inherited. True stops the controller creating executors. |
| `initial_positions` | list | [] | no | Inherited; not used by this controller. Leave it out. |

## Config gotchas

- Spreads are **fractions**, not percents: `0.002` = 0.2%. `0.2` would quote 20% away.
- `order_amount` is **base units**, and `target_base_pct` is a fraction (0.5, not 50).
- Numbers as YAML numbers (`order_refresh_time: 30`, not `"30"`).
- Lists: YAML lists are preferred; the comma-string form (`"0.002,0.004"`) is also accepted
  by the `parse_spreads` validator.
- No `candles_config`, `leverage`, `position_mode`, `stop_loss`, `take_profit`,
  `executor_refresh_time`, `buy_amounts_pct` fields: any of them is a validation error.
- `controller_type` must be `generic`; `market_making` will not import.
- **Perpetuals: avoid.** The controller reads base balance from the connector; on a
  perpetual connector the base asset balance is 0, so with skew enabled `current_base_pct`
  is 0, the bid ratio goes to 2.0 and the ask ratio to 0.0: it quotes only buys and
  accumulates a long. With skew disabled it runs, but every order is `OPEN` at leverage 1
  with no exit logic, so net position drifts unmanaged. Use `pmm_simple` or `pmm_mister` for
  perps.
- **Shared accounts:** skew uses the account's total base and quote balances. Another bot
  or manual holdings in the same assets on the same account distort the skew. Give pmm_v1
  its own sub-account, or set `target_base_pct` knowing the whole balance counts.
- Price band values are absolute prices: re-check them whenever the market has moved a lot,
  a stale band silently turns the bot one-sided.

## Regimes, risks and tuning

| Regime | Behaviour | Tuning |
|---|---|---|
| Ranging / mean-reverting | Best case: both sides fill, spread captured repeatedly. | Tighter spreads (10-30 bps on majors), `filled_order_delay` 15-60s, skew on with multiplier ~1. |
| Quiet / compressed | Few fills at wide spreads. | Tighten spreads toward the natural book spread plus fees; keep tolerance on to preserve queue priority. |
| Volatile (expansion) | Adverse selection: fills right before larger moves. | Widen spreads (2-3x), add outer levels, raise `order_refresh_tolerance_pct`, longer `filled_order_delay`. |
| Trending | Worst case: one side fills repeatedly and inventory piles up on the losing side; there is no stop loss. | Pause (`manual_kill_switch: true`) or use price bands to stop buying above / selling below levels; stronger skew (multiplier 0.5); or switch to a directional controller. |

Key risks:
- **Inventory risk is unbounded** apart from skew. Skew only resizes orders (0 to 2x); it
  never sells inventory at market. A trend can leave the whole quote balance converted to
  a falling base asset.
- **Fees:** each round trip pays two fees. Spreads below roughly 2x the maker fee (e.g.
  < 5 bps on a 0.1% fee tier) lose money even without adverse selection.
- **Minimum notional:** skew can shrink an order below the exchange minimum; those orders
  are rejected. Size `order_amount` with margin.

## Backtesting notes

- Backtests are supported (OrderExecutor simulator). The reference price is the candle
  close at the backtesting resolution, and fills are simulated against candles, so queue
  position and the book spread are not modelled. Use 1m resolution.
- **Skew is inert in a backtest**: there is no connector balance, `_get_balances` returns
  0/0 and the skew ratios fall back to 1.0. Backtest results reflect the unskewed ladder.
- Filled orders become position holds (inventory), so backtest PnL is dominated by the
  mark-to-market of accumulated inventory. Look at the position-held timeseries as well as
  total PnL.
- `filled_order_delay`, refresh time and tolerance do work in the backtest.

## Sample styles

All three are Binance **spot**, skew on toward 50/50, price bands disabled.

| Style | Pair | Ladder | Refresh / tolerance | Post-fill delay | Skew range | Use when |
|---|---|---|---|---|---|---|
| `conservative` | SOL-USDT, 0.1 SOL/level | 40 / 80 bps, 2 levels per side | 60s / 0.2% | 120s | 1.0 | First deployment, unclear regime, or elevated volatility. Few fills, low adverse selection. |
| `balanced` | ETH-USDT, 0.01 ETH/level | 20 / 40 / 60 bps, 3 levels per side | 30s / 0.1% | 60s | 1.0 | Default for a liquid range-bound market. |
| `aggressive` | BTC-USDT, 0.0002 BTC/level | 10 / 20 bps, 2 levels per side | 15s / off (-1) | 15s | 0.5 (strong skew) | Quiet, tight, mean-reverting markets where fill rate matters. Stop it in a trend. |

To adapt one: read the style, change the pair and `order_amount` (base units for the new
pair, and check the minimum notional), adjust spreads to the regime, and upload under a new
`config_name`. Never edit a style in place.

## Deploy checklist

1. `manage_agent_controllers(action="status", name="pmm_v1")`: must be `in_sync`; `missing`
   -> `sync` (it goes to the **generic** folder); `drift` -> follow the controller_sources
   drift procedure, never overwrite blind.
2. Confirm the connector is **spot** and the account holds both base and quote, and that no
   other bot trades the same assets on that account (skew reads total balances).
3. Pick a style, adapt pair / `order_amount` / spreads, `upload_config` under a new name,
   with `controller_type: generic`.
4. Backtest first (1m resolution, several days covering a trend and a range). Remember skew
   is off in the backtest; judge spread capture versus inventory drawdown.
5. Deploy with a small `order_amount`. Monitor the inventory (`current_base_pct` in the
   status), fill rate and whether one side dominates. Adjust spreads, tolerance and delay
   live with `update_config` (all quoting fields are updatable).
6. In a trend: set `manual_kill_switch: true` or a price band rather than waiting for skew.

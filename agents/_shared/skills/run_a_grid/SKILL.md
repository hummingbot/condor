---
name: run_a_grid
description: How to decide a grid before running one — confirming the market is actually
  ranging, picking start/end/limit prices from data rather than round numbers, sizing levels
  against capital, and deciding what a stopped grid should do with its inventory.
when_to_use: Before running a grid — user asks for a grid bot, grid trading, or to profit
  from a range or from chop. Read this to choose the prices and the level count;
  `create_grid_executor` already documents the parameters.
source: builtin
---

# Run a grid

`create_grid_executor` teaches the call — the direction inequalities, what `limit_price`
does, why there is no stop-loss. This playbook is the judgement: **whether a grid is the
right tool here, and which prices to give it.**

A grid monetises oscillation. Its failure mode is not a bug, it is a trend: price walks
out of the range in one direction, every level fills on the wrong side, and you are left
holding inventory at an average price worse than spot. Everything below is about not
walking into that.

## 1. Confirm the market is actually ranging

Do this before anything else. A grid in a trend is a slow loss with extra steps.

```
get_candles(connector_name="binance", trading_pair="SOL-USDT", interval="1h", days=3)
```

Read the series, don't just take the min and max:

- **Ranging** — price crosses its own mid repeatedly, highs and lows cluster in a band,
  no sustained drift. This is grid territory.
- **Trending** — successive highs and lows both moving one way. Do not grid it. A
  directional view belongs in `create_position_executor`; a gradual entry belongs in
  `create_dca_executor`.
- **Breaking out of a long range** — the worst case, because the recent candles still
  look range-bound. If the most recent bars are all outside the prior band, wait.

If the user insists on a grid in a trend, at minimum align `side` with the trend
(a LONG grid in an uptrend accumulates into strength) and be explicit that `limit_price`
is the only thing standing between them and an unbounded bag.

## 2. Pick the prices

When the user gives exact prices, use them. When they don't, calculate and **always
present the numbers back for confirmation before creating the executor.**

Fetch spot first:

```
get_prices(connector_name="binance", trading_pairs=["SOL-USDT"])
```

### Strategy 1 — percentage-based (the default)

With current price `P`, and a deliberate 3:1 skew toward the side you expect:

**LONG grid (`side=1`, expecting price to rise):**
- `end_price   = P * 1.03`   — 3% above spot, the profit room
- `start_price = P * 0.99`   — 1% below spot, the entry zone
- `limit_price = start_price * 0.995` — 0.5% below the grid, the safety stop

**SHORT grid (`side=2`, expecting price to fall):**
- `start_price = P * 0.97`   — 3% below spot, the profit room
- `end_price   = P * 1.01`   — 1% above spot, the entry zone
- `limit_price = end_price * 1.005` — 0.5% above the grid, the safety stop

Both give roughly a 4% total range. Widen every number for a volatile pair — a 4% range
on something that moves 15% a day is a grid that stops on the first hour.

### Strategy 2 — historical range

Use this when the user mentions "the recent range", volatility, or wants the grid fitted
to data rather than to a percentage.

1. `get_candles(interval="1h", days=3, ...)`
2. Take the high and the low of that window.
3. **LONG:** `start_price = low`, `end_price = high`, `limit_price = low * 0.995`
4. **SHORT:** `start_price = low`, `end_price = high`, `limit_price = high * 1.005`

The range is the same either way; only `limit_price` moves to the side the grid is
protecting.

Round every price to the pair's real tick precision before presenting it. If the user
gave some prices and not others, calculate only the missing ones — do not overwrite what
they chose.

## 3. Size the levels against the capital

The level count is the intersection of two limits (see the tool docstring):
`total_amount_quote / min_order_amount_quote`, and the price range divided by
`min_spread_between_orders`. The tighter one wins.

The judgement is that **more levels is not better.** Each level is an order, and:

- Levels finer than the exchange's minimum order size silently collapse to fewer, larger
  levels than you designed.
- A dense grid on a wide range spreads capital so thin that each fill's take-profit is
  smaller than the fees crossing it.
- A sparse grid on a narrow range barely differs from a single limit order.

A workable starting shape is 10–20 levels, with each level comfortably above the venue's
minimum notional. If `total_amount_quote / 20` is below the minimum order size, the
capital is too small for the range — either narrow the range or say so rather than
shipping a grid that will place three orders.

Use `activation_bounds` on a wide grid so orders are only placed near spot: it keeps
capital uncommitted and avoids hammering rate limits with orders that cannot fill for
hours. Pair it with `order_frequency` and `max_orders_per_batch` on a venue with tight
rate limits.

Set `coerce_tp_to_step=True` when the take-profit is small relative to the grid step —
without it a level can close before the next one fills, which is the grid paying fees to
stand still.

## 4. Decide what a stopped grid does with its inventory

This is the decision that actually determines the loss, and it is `keep_position`:

- **`keep_position=False`** — the accumulated position is closed when price crosses
  `limit_price`. This is a stop-loss: the loss is realised and bounded. Choose it when
  the capital is needed elsewhere, when the thesis was purely the range, or when the
  user cannot tolerate an open drawdown.
- **`keep_position=True`** — the position is held after the grid stops. Choose it only
  when the user genuinely wants the base asset at that price and can wait. It is not
  "avoiding the loss"; it is converting a realised loss into an open one.

Say which of the two is being chosen, in words, before creating. "The grid stops below
138 and sells what it accumulated" and "the grid stops below 138 and you keep the SOL"
are very different products.

Prefer `open_order_type=3` and `take_profit_order_type=3` (LIMIT_MAKER). A grid lives on
maker fees; taking the spread on both sides of every level is usually more than the
take-profit.

**Watch `leverage`.** The backend default for grids is 20. Pass `leverage=1` explicitly
for spot-like sizing — inheriting 20 turns a $500 grid into $10,000 of exposure and a
`limit_price` that was a sensible stop into a liquidation.

## 5. After it starts

```
list_executors(executor_types=["grid_executor"])
get_executor(executor_id="<id>")
get_performance_report()
```

A grid that has placed no orders is usually a range that does not contain spot, or
`activation_bounds` narrower than the distance to the nearest level. A grid whose fills
are all on one side is the trend warning from step 1 arriving late — decide then whether
to stop it with `stop_executor` rather than waiting for `limit_price`.

## Checklist before you call

- [ ] Candles read, and the market is genuinely ranging.
- [ ] `side` set explicitly — `limit_price` does not imply direction.
- [ ] Prices satisfy the inequality for that side, and are rounded to real tick size.
- [ ] Range width matches the pair's actual volatility.
- [ ] Each level clears the venue's minimum notional.
- [ ] `leverage` set deliberately, not inherited.
- [ ] `keep_position` chosen, and stated to the user in plain words.
- [ ] All calculated prices presented and confirmed before creating.

---
name: directional_position
description: The checklist before taking a directional position — converting a USD intent
  into a base-currency amount, setting a stop where the thesis breaks rather than where the
  loss feels tolerable, and treating leverage as a liquidation distance.
when_to_use: Before opening a directional trade — user asks to go long or short, buy or sell
  with a stop and target, or take a position on a view. Read this to size and bound it;
  `create_position_executor` already documents the parameters.
source: builtin
---

# Take a directional position

`create_position_executor` teaches the call and warns about the sharpest trap. This
playbook is the checklist that should run before it.

## 1. Convert the intent into an amount — this is where money is lost

**`amount` is in BASE currency.** Almost every human instruction is in quote. "Buy $500
of BTC" is not `amount=500`; it is `amount = 500 / price`.

```
get_prices(connector_name="binance_perpetual", trading_pairs=["BTC-USDT"])
```

Then divide. Get this backwards on a high-priced asset and the order is off by four
orders of magnitude in whichever direction hurts.

Two guards worth making habit:

- **Sanity-check the product.** `amount * price` should be the USD figure the user said.
  One multiplication, and it catches every version of this mistake.
- **Never carry an amount straight from a DEX swap result into a position size** without
  reading the true balance first when the swap's quote was flagged as approximated — see
  the `approximation` note on `create_order_executor`. The number you asked for and the
  number you received are not always the same.

Note this convention is the **opposite** of `create_dca_executor` and
`create_grid_executor`, which both size in quote. Do not carry the habit between them.

## 2. Put the stop where the thesis breaks

`stop_loss` and `take_profit` are decimal fractions of entry — `0.02` is 2%.

The mistake is choosing the stop by how much loss feels acceptable. That produces a stop
inside the pair's ordinary noise, which gets hit by a move that means nothing, and then
the thesis plays out without you.

Work the other way round:

1. **Find the price that would prove the view wrong** — under the range low, back inside
   the broken level, whatever the trade is actually predicting. Use candles if you need
   the pair's typical swing:
   `get_candles(connector_name=..., trading_pair=..., interval="1h", days=7)`.
2. **That distance is the stop**, expressed as a fraction of entry.
3. **Size so that distance costs an acceptable amount.** The stop sets the risk per unit;
   the amount sets how many units. Adjust the amount, never the stop.

If the resulting position is uncomfortably small, the honest conclusion is that the
trade is too expensive at this size — not that the stop should be tightened.

## 3. Check the ratio before committing

With the stop set by structure and the target set by where price could plausibly reach:

- **Below 1:1** — you need to be right most of the time to break even. Usually a pass.
- **Around 2:1** — the ordinary working range for a directional trade.
- **Far above 3:1** — check the target is somewhere price actually trades to, not just
  a number that makes the ratio look good.

State entry, stop, target and the ratio back to the user in one line before creating.
A trade whose ratio nobody computed is a trade nobody sized.

## 4. Treat leverage as a distance to liquidation

Leverage does not change the thesis. It changes how far price can move against you
before the position is closed by the exchange rather than by your stop.

- **The liquidation price must sit beyond the stop, with room.** At high leverage the
  stop and the liquidation converge, and the exchange closes you first — which is the
  same loss with none of the control.
- **Leverage multiplies exposure, not edge.** A 5x position is five times the size, five
  times the funding cost on a perp, and five times the P&L swing per tick.
- **Set it explicitly.** Do not inherit whatever was saved as a default; read it back to
  the user in the confirmation line.

On a perp, check the position mode and leverage are what you think before sizing —
`set_account_position_mode_and_leverage`. On a pair with meaningful funding, price the
carry over the intended hold: `get_funding_rate(...)`. A 2% target and a funding cost
that eats 1% of it is a different trade than it looked.

## 5. Choose the entry and the order types

- **Market entry** (omit `entry_price`) — when being in matters more than the price.
- **Limit entry** (`entry_price` set) — when the level is the trade. Accept that it may
  never fill; a limit entry that misses is a trade that did not happen, which is a real
  outcome and not a failure.
- Exits default to MARKET, which is right: a stop that rests as a limit is a stop that
  does not execute in the move it exists for. Leave `stop_loss_order_type` alone unless
  there is a specific reason.
- Add `time_limit` when the thesis has a clock — an event, a session, a funding window.
  A directional position with no time bound quietly becomes an investment.

The trailing stop needs **both** `trailing_stop_activation_price` and
`trailing_stop_trailing_delta`; one alone does nothing.

## 6. After it opens

```
list_executors(executor_types=["position_executor"])
get_executor(executor_id="<id>")
get_performance_report()
```

Close early with `stop_executor` when the thesis breaks before the stop is reached —
the barriers are automation, not a reason to stop thinking. If `keep_position=True` is
used on the stop, the resulting spot holding shows up in `list_positions_held`, and it
is bookkeeping that has to be cleared (`clear_position_held`) if it is later closed
elsewhere.

## Checklist before you call

- [ ] `amount` is in BASE currency, and `amount * price` matches the intended USD size.
- [ ] Stop placed where the thesis breaks, not at a comfortable loss.
- [ ] Size derived from the stop distance, not the other way round.
- [ ] Reward:risk computed and stated.
- [ ] Leverage set explicitly, with the liquidation beyond the stop.
- [ ] Funding priced in if it is a perp held through funding.
- [ ] Entry, stop, target, size and leverage confirmed with the user in one line.

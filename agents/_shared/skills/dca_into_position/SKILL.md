---
name: dca_into_position
description: How to shape a DCA ladder — how far apart the levels go, how the size is
  distributed across them, and how a single shared exit across the whole ladder changes
  what the stop and target mean.
when_to_use: Before laddering into a position — user asks to DCA, average in, scale into a
  position, or buy the dips gradually. Read this to choose the levels;
  `create_dca_executor` already documents the parameters.
source: builtin
---

# DCA into a position

`create_dca_executor` teaches the call: `amounts_quote` and `prices` are parallel lists
in **quote** currency, one index per level. This playbook is what the lists should
contain.

A DCA ladder is a bet that you do not know the bottom. It trades a worse entry in the
good case for a much better average in the bad case. Two things decide whether it works:
**how far the levels are spread**, and **how the size is distributed across them.**

## 1. Establish the reference price

Every level is relative to something. Anchor on spot unless the user named a level.

```
get_prices(connector_name="binance_perpetual", trading_pairs=["BTC-USDT"])
get_candles(connector_name="binance_perpetual", trading_pair="BTC-USDT", interval="4h", days=30)
```

The candles matter more here than for most tools: they tell you how far this pair
actually falls in a normal pullback, which is exactly the spacing question.

## 2. Space the levels against real movement, not round numbers

The classic error is a ladder so tight it all fills on ordinary noise — at which point
you have paid three fees for what a single market order would have done, and you have no
capital left for the move you were laddering against.

- **Levels should span a move the pair genuinely makes.** If the pair's typical
  pullback is 8%, a ladder covering 2% is a market order in costume.
- **Levels closer together near spot, wider further out** is usually better than even
  spacing. The near levels are the likely fills; the far ones are insurance against a
  real dislocation and should sit where that dislocation would actually go.
- **Three to five levels is the workable range.** Fewer and it is not a ladder; more and
  each level is too small to matter and the deepest ones never fill.
- **The last level is a statement about your thesis.** If price reaching it would mean
  you were wrong, it should not be in the ladder — that is what `stop_loss` is for.

For a SELL ladder every sign flips: prices increase across the list, and you are
distributing an exit into strength.

## 3. Distribute the size deliberately

`amounts_quote` does not have to be flat, and flat is rarely the best choice. Pick the
shape from the conviction:

- **Flat** (`[100, 100, 100]`) — no view on where it fills. Simple, defensible, and the
  right default when you have no information.
- **Increasing** (`[100, 100, 150]`) — more capital at worse prices. Pulls the average
  entry down hard if the deep levels fill. This is the shape that makes DCA worth doing,
  and it is the one the tool's own example uses. It also concentrates risk in the case
  where price kept falling, so only use it when you would genuinely want more at that
  price.
- **Decreasing** (`[150, 100, 50]`) — most of the size near spot, a tapering tail. Use
  when you mainly want in now and the ladder is just opportunism on a dip.

Whichever you pick, `sum(amounts_quote)` is the real position size. Check it against
what the user thinks they are committing — people size the first level and forget the
ladder is three times that.

## 4. Understand that the exit is shared

The barriers (`take_profit`, `stop_loss`, `time_limit`, the trailing stop) apply to the
**position as a whole**, not per level. Two consequences worth stating out loud:

- **The percentages move against the average entry, not the first fill.** A 3%
  take-profit after two fills is 3% above the blended price. It is easy to promise a
  target that the deep fills quietly relocate.
- **A `stop_loss` can fire while levels are still unfilled.** If the stop is inside the
  ladder's own price span, the position is stopped out by the very move the ladder was
  built to buy. The stop belongs **below the deepest level** on a BUY ladder, above the
  highest on a SELL — otherwise the two mechanisms fight.

`time_limit` is the underused one. A ladder that has not filled in a week is capital
committed to a thesis that did not happen; a time limit recycles it without needing
anyone to remember.

## 5. Choose maker or taker

`mode="MAKER"` rests limit orders at your prices — cheaper, and correct for a ladder
whose whole premise is patience. `mode="TAKER"` fills at market as each level is
reached, which only makes sense when you would rather guarantee the fill than save the
spread on a fast-moving pair.

## 6. After it starts

```
list_executors(executor_types=["dca_executor"])
get_executor(executor_id="<id>")
```

The executor's record shows which levels filled and the resulting average entry — that
average, not the original plan, is what the barriers act on. Check it after the first
fills rather than assuming the plan survived contact.

## Checklist before you call

- [ ] `amounts_quote` and `prices` are the same length, in quote currency, and the
      prices run in the right direction for the side.
- [ ] Spacing covers a move the pair actually makes.
- [ ] `sum(amounts_quote)` stated to the user as the real commitment.
- [ ] Size distribution chosen deliberately, not flat by accident.
- [ ] `stop_loss`, if set, sits beyond the deepest level rather than inside the ladder.
- [ ] Target explained as a percentage on the average entry, not on the first fill.

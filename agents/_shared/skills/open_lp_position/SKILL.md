---
name: open_lp_position
description: How to decide an LP position before opening it — finding the pool, sizing the
  range against volatility, choosing single- or double-sided, and using the limit prices to
  make the position a strategy rather than a parked bag. The call itself is documented on
  `create_lp_executor`.
when_to_use: Before providing liquidity on a CLMM DEX — user asks to open an LP position,
  LP into a pool, or earn fees on a pair. Read this to pick the pool, the range and the side;
  `create_lp_executor` already documents the parameters.
source: builtin
---

# Open an LP position

`create_lp_executor` teaches the call: which field is the network, what `side` means,
what the limit prices do. This playbook is the part it cannot teach — **what to
actually choose, and why.** Read the tool's docstring for the mechanics; read this
before you commit capital.

The one thing to internalise up front: an LP position is a *conditional trade*, not a
savings account. You are agreeing to sell base as price rises through your range and buy
it as price falls. If you would not want that trade, the fees will not save you.

## 1. Find the pool

Skip to step 2 if the user gave you a pool address.

```
explore_dex_pools(action="list_pools", connector="meteora", search_term="SOL")
```

`connector` is the DEX (`meteora`, `raydium`, `orca` on Solana; `uniswap`,
`pancakeswap` on EVM). `search_term` filters by token.

**Sort by TVL, not by APY.** The default `sort_key="tvl"` is the right default and you
should usually leave it. A headline APY on a thin pool is a rounding artefact — one
day's fees over almost no liquidity — and it inverts the moment you add yours. TVL is
the number that says the pool will still be there tomorrow and that your position is
not most of it. Use `sort_key="volume"` when the user is explicitly fee-hunting, but
then read the volume/TVL ratio rather than the APY column: that ratio is what actually
generates fees.

Reject a pool whose TVL is small relative to the position being opened. Being a large
fraction of a pool means your own entry moves the price you enter at, and your exit
moves the price you leave at.

## 2. Read the pool

```
explore_dex_pools(action="get_pool_info", connector="meteora",
                  network="solana-mainnet-beta", pool_address="<addr>")
```

You need three things from this: the **current price** (every range decision is relative
to it), the **trading_pair** as the pool spells it (which token is base and which is
quote — this decides what `side` means), and the **bin step / fee tier** (how granular
the range can be).

Do not assume the pair orientation. In `BONK-SOL`, SOL is the *quote*, so "putting in
SOL" is a quote-only position (`side=1`) — the opposite of what a Solana user usually
expects.

## 3. Size the range against volatility

The range is the whole decision. Narrow earns more fees per dollar and spends more time
out of range; wide earns less and holds through moves. There is no correct answer, only
a match to how long the user wants to be left alone.

Anchor it on realised movement rather than a guess:

```
get_candles(connector_name="binance", trading_pair="SOL-USDT", interval="1h", days=7)
```

Take the recent high/low. Then:

- **Actively managed (hours to a day):** roughly ±0.5–1× the pair's daily range around
  spot. Highest fee density, but it will exit range and needs re-centring.
- **Set and forget (days to weeks):** wide enough to contain the last week's high and
  low with room to spare. Fewer fees per dollar, far less babysitting.
- **A pool you do not know well:** go wider than feels right. The cost of a too-wide
  range is opportunity; the cost of a too-narrow one is being fully converted into the
  token that just fell.

State the chosen bounds and the current price back to the user before creating anything.

## 4. Choose the side, and make the range agree

`side` is not a preference — it is a statement about which tokens you are supplying,
and the range must agree with it or the position is nonsense. The tool docstring lists
the three values; this is how to pick:

- **The user hands you only the base token** → `side=2` (SELL), range **above** spot.
  It starts out of range and converts base into quote as price rises into it. This is a
  laddered take-profit that earns fees on the way out.
- **The user hands you only the quote token** → `side=1` (BUY), range **below** spot.
  It starts out of range and converts quote into base as price falls into it. This is a
  laddered bid that earns fees on the way in.
- **The user hands you both** → `side=3` (RANGE), range around spot. Earning fees
  immediately is the point; accept that you are neutral and exposed to impermanent loss
  in both directions.

If the user says "I want to buy SOL if it dips" or "I want to sell into strength", they
have described a single-sided position and should not be talked into `side=3`.

## 5. Decide the limit prices — this is what makes it a strategy

`upper_limit_price` / `lower_limit_price` auto-close the position when price crosses
them. **Set both whenever the position has a thesis with an end.** A single-sided
position with only one limit set will sit out of range indefinitely on the unprotected
side, fully converted, earning nothing — the failure mode people describe as "my LP
turned into a bag".

Two patterns worth knowing by name:

**Sell limit** (take profit on a base holding): `side=2`, range above spot,
`upper_limit_price` a buffer above the range top. Price rises through the range
converting base to quote and paying fees, then the position closes once it clears the
top. A better fill than a plain limit order, because the conversion is continuous
rather than a single touch.

**Buy limit** (accumulate on a dip): `side=1`, range below spot, `lower_limit_price` a
buffer below the range bottom. Price falls through the range converting quote to base
and paying fees, then closes if it keeps falling — which caps how far you keep
averaging into something still dropping.

Size the buffer to the pair's noise. Too tight and ordinary wick closes you out;
roughly a fifth to a half of the range width is a reasonable starting point.

## 6. Decide what you want back

`keep_position` decides what you hold after the close — the net token change, or a swap
back to the original quote asset. Tie it to the thesis, not to a habit:

- Accumulating a token you intend to hold → keep the position.
- Running the LP as a yield trade on a stable base → swap back, so P&L is legible in the
  asset you measure in.

## 7. Create, then verify

Call `create_lp_executor` with what you chose. Then confirm the position actually
exists rather than assuming it does:

```
list_executors(executor_types=["lp_executor"])
get_executor(executor_id="<id>")
```

An LP executor opens asynchronously. A position that is still `OPENING` a minute later
is worth reading the executor for; one that terminated immediately failed on-chain and
the reason will be in its record.

## 8. Expectations while it runs

- **Out of range is normal, not broken.** It means the position is fully converted to
  one side and earning no fees. It earns again if price comes back, or closes if it hits
  a limit price.
- **Fees accrue continuously and are realised on close.** Uncollected fees are part of
  the position's value, not separate income.
- **Close with `stop_executor`, never by hand in the DEX UI.** Closing externally leaves
  the executor believing it still owns a position, which is exactly the state that has
  to be reconciled later.
- **If a close fails repeatedly** the executor can terminate while the position is still
  live on-chain. That is an orphan, and it is recoverable — read the
  `recover_orphaned_position` skill. Do not open a new position on the same funds until
  it is resolved.

## Checklist before you call

- [ ] Pool chosen on TVL and volume/TVL, not on a headline APY.
- [ ] Current price read from the pool, and the pair's base/quote orientation confirmed.
- [ ] Range anchored on recent realised range, not on a round number.
- [ ] `side` matches the tokens supplied, and the range sits on the correct side of spot.
- [ ] Both limit prices set, unless the user explicitly wants an open-ended position.
- [ ] `keep_position` matches whether the user wants the token or the quote asset back.
- [ ] Bounds and amounts stated back to the user before creating.

---
name: Net-Position Market Maker
description: Native Hyperliquid perp market making as ONE net position, requoted
  atomically each tick by the perp_requote routine (cancel-all-then-place-two),
  spread sized to the market regime.
agent_key: null
default_config:
  frequency_sec: 60
  execution_mode: loop
  venue: hyperliquid
  notional_per_side: 20
  leverage: 2
  max_net_usd: 80
  risk_limits:
    max_position_size_quote: 200
    max_open_executors: 4
    max_drawdown_pct: 15
created_by: 456181693
---

# Net-Position Market Maker

You make markets on **Hyperliquid perpetuals** as **one net position** worked by
two resting quotes (a bid below mid, an ask above). Hyperliquid nets per coin, so
there is a single net position; the two quotes push it up and down and you earn
the spread on the oscillation.

**You do NOT place quotes as `order_perp` executors.** That model leaked — a new
executor per side per tick relied on you to cancel the stale ones, and stale +
restart-detached orders piled up (15 orphans, 2× inventory). Instead your quotes
are owned by the **`perp_requote` routine**, which each tick does the whole cycle
atomically against the venue: **cancel every resting order on the coin → read the
net position → place exactly one bid and one ask**, skipping a side that would
breach the inventory cap. At most two live orders after any tick. No leak.

## Configuration at launch
`coin` is **always provided at launch** — read it from `[CURRENT CONFIG]`. If
missing, abort and notify: "coin is required. Launch with trading_context='Make
markets on COIN'."

## Each tick

### 1. Classify the regime → size the spread
Run `manage_routines(action="run", name="market_analyzer", config={"coin": COIN})`.
It returns the regime and the **spread guidance** (`spread_pct` = regime × ATR,
floored at the perp fee TP), the mid, and your live inventory. The regime is your
core lever — quiet tightest, ranging ×1.5, volatile ×3–5, trending widen + skew
or stand down.

### 2. Requote atomically
Run `manage_routines(action="run", name="perp_requote", config={`
- `"coin": COIN,`
- `"spread_pct": <the routine's spread_pct as a fraction, e.g. 0.0027>,`
- `"notional_usd": notional_per_side,`
- `"leverage": leverage,`
- `"max_net_usd": max_net_usd`
`})`.

It cancels all resting orders, re-centers both quotes off the current mid, and
respects the inventory cap (it skips the side that would push net past
`max_net_usd`, which is how you manage skew — no manual widening needed).

- **Stand down** (routine/market_analyzer says a strong confirmed trend): call
  `perp_requote` with `"stand_down": true` (and `"flatten": true` if you want to
  also close the net position). It cancels both quotes and places nothing.

### 3. Journal
One line: regime, mid, spread, net position + uPnL (from the requote output), any
fills since last tick. Fills are auto-notified — do not `send_notification` for
routine ticks; reserve it for a routine error or a stand-down.

## Why this is leak-proof
Every tick the venue is swept clean and re-quoted, so there can never be more
than two live orders and orphans cannot accumulate. Your only per-tick decisions
are the **spread** (from regime) and **whether to stand down** — the mechanics are
the routine's, enforced in code.

---
name: Accumulate Favorites
description: Rest patient Polymarket limit buys for Argentina-Yes, Spain-No and
  England-No, re-pricing stale quotes, to accumulate the preferred World Cup
  winner position at attractive prices.
agent_key: null
default_config:
  frequency_sec: 300
  execution_mode: loop
  venue: polymarket
  event_slug: world-cup-winner
  targets:
  - Argentina:Yes
  - Spain:No
  - England:No
  per_order_usdc: 10
  max_per_target_usdc: 30
  discount_pct: 0.008
  requote_drift_pct: 0.01
  cross_after_ticks: 3
  risk_limits:
    max_position_size_quote: 100
    max_open_executors: 8
    max_drawdown_pct: 50
created_by: 456181693
---

# Accumulate Favorites

Rest patient limit **buys** for the three targets and accumulate each toward its
cap at attractive prices. Argentina is preferred — fill it first and hardest.

## Each tick

### 1. Resolve tokens + prices
Run `manage_routines(action="run", name="world_cup_odds", config={"event_slug":
event_slug, "targets": targets})`. For each target you get `token_id`, `bid`,
`ask`, `mid`. If a target reads CLOSED or missing, skip it and note it.

### 2. Read what you already hold + have resting
From `[CORE DATA - native_executors]`: for each target `token_id`, sum the
`order_pred` executors that are its accumulation — FILLED (DONE) count toward
`accumulated_usdc`; RESTING are your live quotes.

### 3. For each target, decide
Priority order: **Argentina first**, then Spain-No, then England-No.

- **Accumulated ≥ `max_per_target_usdc`** → done; if a resting quote remains,
  leave it only if still attractive, else stop it.
- **No resting quote and room left** → place one (below).
- **Resting quote is stale** — the market moved so your limit is now above
  `ask × (1 − discount_pct)` by more than `requote_drift_pct`, or it's no longer
  the attractive side of the book → stop it (`keep_position=false` cancels it on
  the venue) and re-place at the new price.
- **Resting and still attractive** → leave it.

### 4. Place a limit buy — patient, but not so patient it never fills
`manage_executors(action="create")`:
- `executor_type: "order_pred"`, `venue: "polymarket"`, `market: token_id`,
  `order_type: "limit"`, `position: "LONG"` (you hold the token),
  `amount_quote: per_order_usdc` (must be ≥ 10, the Polymarket minimum).
- **Default (patient):** `limit_px = round(ask × (1 − discount_pct), 3)` — a
  *small* discount (0.8%), close to the book so it actually gets hit, clamp to
  ≥ 0.01 and < 1. A 3% discount never filled all session — you accumulate
  nothing by resting far below the market.
- **Cross to guarantee a fill:** if a target's quote has rested unfilled for
  `cross_after_ticks` ticks (the market isn't coming to you), **cross the
  spread** on the next placement — `limit_px = ask` (or `ask + 1 tick`) — so it
  fills immediately. Accumulating the position slightly worse beats never
  accumulating it. Argentina (preferred) should cross sooner than Spain/England.

Never exceed `max_per_target_usdc` of resting + filled on one target, and never
more than `max_open_executors` total.

### 5. Journal
One line per tick: for each target — accumulated_usdc, resting quote price, and
any fills since last tick. Fills are auto-notified by the runtime; only
`send_notification` on a FAILED order or a geo-block (CLOB region) error, then
pause the loop.

## Notes
- **Hold to resolution.** No stop-loss — these settle to 0/1 at tournament end.
  The only risk lever is *how much* you accumulate and *how cheap* your basis.
- **$10 minimum** per order is a hard Polymarket rule; never size below it.
- A limit that never fills at a good price is fine — patience is the strategy.

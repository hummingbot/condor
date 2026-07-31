# How the Bollinger Band Trader works

A walkthrough of the data, the math, and the decision path — enough to audit the agent
without reading the code.

## The problem this agent solves

Bollinger Bands are the most widely misused indicator in retail trading, and the failure
mode is always the same: **treating a band touch as a signal.** "Price hit the upper band,
sell it" works in a range and is ruinous in a trend, and the touch itself is identical in
both cases. Most losses from this indicator come from fading a band walk.

So the agent is built around a two-stage decision instead of a one-stage one:

```
1. What volatility regime is this?   →  squeeze / expansion / band walk / range
2. Given the regime, what does %B mean?  →  follow it, fade it, or stand aside
```

Every routine, veto, and exit rule below serves that ordering.

## Components

```
agents/bollinger_band_trader/
  AGENT.md                                  # identity, domain knowledge, response format
  HOW_IT_WORKS.md                           # this file
  validation.md                             # what was verified and how
  routines/
    band_state.py                           # primary read: classify + derive levels
    squeeze_screener.py                     # rank a watchlist by squeeze tightness
    band_trade_sizer.py                     # risk sizing + pre-trade guardrails
  skills/bollinger_playbook/SKILL.md        # the act-on-it procedure
  strategies/bb_squeeze_breakout/strategy.md # optional loop: tick playbook
```

The agent is useful at every layer. Consulted with no loop running, it answers band
questions using the routines. Given a strategy, it can run the same logic unattended.

## Data sources

Everything comes from the Hummingbot API through the standard routine client — no
external feeds, no API keys beyond the exchange connection already configured.

| Call | Used for |
|---|---|
| `market_data.get_candles(connector, pair, interval, max_records)` | Bands, %B, bandwidth history, ATR, ADX, volume |
| `market_data.get_prices(connector, [pair])` | Live entry price in the sizer |
| `portfolio.get_total_value()` | Risk budget and the reserve check |

`band_state` requests 300 candles on each of 15m / 1h / 4h. The depth matters: the
bandwidth percentile rank is only meaningful against a long history of that same pair's
bandwidth, and 300 candles gives roughly three days of 15m context and fifty days of 4h.

## The math

All indicators are computed in plain Python inside the routines — no pandas, no TA
library, so the definitions are auditable in place.

**Middle band** — `SMA(close, period)`, default period 20.

**Standard deviation** — population (divide by `n`, not `n − 1`), matching the standard
Bollinger definition. Using the sample deviation would widen the bands slightly and shift
every %B reading.

**Upper / lower band** — `mid ± std_mult × σ`, default multiplier 2.0.

**%B** — `(close − lower) / (upper − lower)`. Normalized position in the band: 0 = lower,
0.5 = middle, 1.0 = upper. Deliberately **not** clamped — values outside [0, 1] are the
signal that price closed beyond the band.

**Bandwidth** — `(upper − lower) / mid × 100`. Absolute width as a percentage of the mean.

**Bandwidth rank (`bw_rank`)** — the percentile of the current bandwidth within the
previous 200 bandwidth readings for the same pair and timeframe, with ties counted at
half weight. **This is the squeeze detector, and it is the reason the agent works across
assets.** Raw bandwidth of 1.2% is a tight coil on a major and a dead-flat range on a
small-cap; the percentile normalizes that away.

**Keltner containment (TTM squeeze)** — Keltner Channel is `EMA(close, 20) ± 1.5 × ATR(20)`
where ATR is a simple rolling mean of true range. `squeeze_on` is true when the Bollinger
Bands sit entirely inside the Keltner Channel. This is a second, independent squeeze test:
`bw_rank` is relative to the pair's own history, Keltner containment is relative to
current range. Either one firing marks a squeeze.

**ADX** — Wilder's ADX: true range and directional movement are Wilder-smoothed over 14
periods, DX is computed from the resulting ±DI, and DX itself is smoothed again over 14.
**The second smoothing is load-bearing.** A raw single-window DX — which is what the
first draft of this routine used, and what the repo's `market_analyzer` still uses —
saturates near 100 on any sustained directional push. Since price reaching a Bollinger
band *is* a directional push, raw DX made every band tag read as a trend and left
`reversion_range` unreachable in practice. That is measured, not assumed: over 600
synthetic ranges, no band tag ever produced a reversion verdict before the fix, and the
regression is pinned by `test_adx_does_not_pin_at_100_in_a_range`.

## Classification

Per timeframe, in this order (first match wins):

| Verdict | Condition |
|---|---|
| `expansion_up` | bandwidth grew > 15% over 3 candles **and** `%B > 1.0` |
| `expansion_down` | bandwidth grew > 15% over 3 candles **and** `%B < 0.0` |
| `squeeze` | `squeeze_on` **or** `bw_rank ≤ 20` |
| `band_walk_up` | ≥ 3 of the last 6 closes at `%B ≥ 0.90` **and** ADX > 25 |
| `band_walk_down` | ≥ 3 of the last 6 closes at `%B ≤ 0.10` **and** ADX > 25 |
| `reversion_range` | `bw_rank ≥ 40`, bandwidth **not** expanding, **and** ADX < 25 |
| `neutral` | none of the above |

**Expansion is tested before the squeeze, and the order is not cosmetic.** Bandwidth rank
is computed from history, so on the very candle a coil breaks, the rank is still low — a
squeeze-first ordering reports `squeeze_pending` on exactly the bar the setup exists to
trade. `test_firing_coil_reads_as_expansion_not_squeeze` pins this.

ADX splits the remaining space cleanly at 25: above it only continuation setups are
reachable, below it only reversion.

A **failed breakout** is tracked separately: the last close is back inside the band while
one of the two closes before it was outside. It is checked before everything else because
it is the highest-quality signal in the set — a break with no follow-through.

## From classification to levels

`band_state` combines the entry timeframe (15m) with the trend timeframe (4h) and emits
one setup with concrete prices:

| Setup | Entry | Stop | Target |
|---|---|---|---|
| `squeeze_pending` | — (reports both triggers) | — | — |
| `squeeze_breakout_long` | current price | middle band | `entry + 2 × (entry − stop)` |
| `squeeze_breakout_short` | current price | middle band | `entry − 2 × (stop − entry)` |
| `band_walk_long` | middle band (pullback) | `mid − 1 ATR` | upper band |
| `band_walk_short` | middle band (pullback) | `mid + 1 ATR` | lower band |
| `reversion_long` | lower band | `lower − 0.5 ATR` | middle band |
| `reversion_short` | upper band | `upper + 0.5 ATR` | middle band |
| `failed_breakout_long` | current price | 3-candle low − 0.25 ATR | halfway above the mean |
| `failed_breakout_short` | current price | 3-candle high + 0.25 ATR | halfway below the mean |

Then three gates can still turn any of them into `no_trade`:

1. **Higher-timeframe veto** — a reversion against a 4h band walk or expansion is
   rejected outright. This is the single most valuable rule in the agent.
2. **Reward:risk floor** — 1.5 for continuation setups, 1.2 for reversions.
3. **Volume filter** — a squeeze breakout on below-average volume is rejected with
   "wait for the retest".

## Sizing

`band_trade_sizer` is the only path to a position size. Fixed fractional risk:

```
risk_quote     = portfolio × risk_pct / 100          (default 0.5%)
stop_pct       = |entry − stop| / entry
raw_notional   = risk_quote / stop_pct
notional       = min(raw_notional,
                     portfolio × max_position_pct/100,      (default 10%)
                     portfolio × (1 − reserve_pct/100))     (default 20% reserved)
amount_base    = notional / entry
```

The stop distance drives the size, so a wide-stop setup automatically gets a smaller
position and every trade risks the same amount. Seven guardrails run afterwards
(portfolio known, stop on the protective side, stop distance between 0.05% and 20%, risk
within policy, leverage ≤ 5, notional above the exchange minimum, margin inside the
reserve). Any `FAIL` blocks the trade and no payload is emitted.

When the size is capped by the position limit or the reserve, the routine reports it as a
`WARN` — the trade is still valid, it just risks less than the target.

## Tick flow (loop mode)

```
band_state ──► setup == no_trade / squeeze_pending ──► HOLD, journal
           └─► tradeable setup
                  ├─ position already open on this pair? ──► manage / close only
                  └─ band_trade_sizer ──► FAIL ──► HOLD, journal blocked_by
                                      └─► PASS ──► manage_executors(create, position_executor)
```

Default frequency is 900s to match the 15m entry candle. Running faster re-reads the same
candle and produces duplicate signals rather than new information.

## Cost

The routines are pure Python over candle data — no LLM tokens, no paid data. Only the
agent's reasoning costs anything, and **that cost is much larger than it looks.**

Measured on a real 19-tick session:

| | |
|---|---|
| System prompt | ~5,600 tokens |
| Tool schemas (25 tools, 2 MCP servers) | ~15,000 tokens |
| Base, **resent on every model call** | ~20,600 tokens |
| Round-trips per tick | 1–10 (a tick is an agentic loop, not one call) |
| **Measured input per tick** | **~101,000 tokens** (range 20.8k–214.8k) |
| Signal the model reasons over | **110 tokens** — the `band_state` output |

So ~99.9% of what you pay for is overhead. On a Sonnet-class model that is ~$0.43/tick,
or ~$41/day at a 15m cadence. On the free model this agent now defaults to it is $0.00.

Two consequences worth internalising:

- **Pick the model deliberately.** "Cheap Anthropic" is not cheap here: at ~101k
  tokens/tick, Haiku-class runs ~$20/day. A free or sub-cent model is the sane default
  for a loop whose ticks are overwhelmingly HOLD.
- **The LLM is not doing the analysis.** `band_state` already emits `setup`, `bias`,
  `entry`, `stop`, `target`, `rr` and the veto outcome; `band_trade_sizer` emits PASS/FAIL
  and a ready payload. On a HOLD tick the model reads `no_trade` and writes a paragraph
  meaning `no_trade`. Gating the model behind a verdict-changed check, or removing it from
  the hot path entirely, removes most of the cost without changing behaviour.

## Known limitations

- **`bw_rank` needs history.** A pair with fewer than ~50 candles gives an unreliable
  percentile. Both routines skip pairs that are too thin rather than guessing.
- **ADX differs from `market_analyzer`.** This agent smooths; the Market Making Expert's
  routine does not. The two will report different ADX values for the same market, and
  this one is the textbook figure. Do not compare them directly.
- **Reversion is the rarest verdict.** Over 400 synthetic ranges it fires on roughly 29%
  of readings, and the subset that also tags a band — the tradeable case — is a few
  percent. That is intended: fading is the setup with the worst failure mode, so the gates
  are the tightest. It does mean a live agent will spend most of its time on breakouts.
- **Squeeze direction is unknowable.** The agent deliberately refuses to pre-position
  during a squeeze. Some of the move is always given up waiting for the break to confirm.
- **No cross-pair correlation model.** The 3-position cap and the "no two same-direction
  majors" rule are crude proxies. Three correlated longs are three copies of one trade.
- **Whipsaws around the threshold.** A pair oscillating at `bw_rank ≈ 20` will flip
  between `squeeze` and `reversion_range` on consecutive ticks. The strategy's one-position
  -per-pair rule absorbs this, but the readings will look inconsistent.
- **Fees are not in the R:R.** A 1.2 R:R reversion on a spot pair with 0.075% maker fees
  is thinner than it looks. On spot, prefer the 1.5 floor.

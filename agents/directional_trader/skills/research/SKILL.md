---
name: research
description: Phase 1 — market regime classification, indicator exploration, and a
  confirmed signal spec, before any controller code is written
when_to_use: When starting work on any new strategy or pair, or when the user wants
  to explore a signal idea, validate an indicator, or understand the market regime.
  Always run this before writing controller code.
created: '2026-07-30T20:10:41Z'
source: agent:directional_trader
---

# Phase 1: Research

Produce a confirmed, statistically grounded **signal spec** — indicator params,
entry/exit logic, regime conditions — backed by visible data. No controller code
until this passes.

## Step 1 — Gather market data

`get_candles(...)` with:
- `connector_name` (e.g. `binance_perpetual`)
- `trading_pair` (e.g. `BTC-USDT`)
- `interval` — `1h` swing, `15m` intraday, `1d` position
- `days` — ≥ 60 swing, ≥ 14 intraday, ≥ 180 position

Also fetch, when the pair is a perpetual:
- **funding rate** — reveals directional bias and carry cost
- **order book snapshot** — reveals liquidity depth and skew

## Step 2 — Compute exploratory indicators

Apply a broad set, then narrow:

```python
import pandas_ta as ta

# Trend
df.ta.ema(length=20, append=True)
df.ta.ema(length=50, append=True)
df.ta.ema(length=200, append=True)
df.ta.adx(length=14, append=True)
df.ta.supertrend(length=10, multiplier=3.0, append=True)

# Momentum
df.ta.rsi(length=14, append=True)
df.ta.macd(fast=12, slow=26, signal=9, append=True)

# Volatility
df.ta.bbands(length=20, std=2.0, append=True)
df.ta.atr(length=14, append=True)

# Custom
df["zscore"] = (df["close"] - df["close"].rolling(20).mean()) / df["close"].rolling(20).std()
df["ret"]    = df["close"].pct_change()
df["rvol"]   = df["ret"].rolling(20).std() * (252 ** 0.5)
```

## Step 3 — Classify the regime

| Regime          | Detection                                                          | Strategy family        |
|-----------------|--------------------------------------------------------------------|------------------------|
| Strong trend    | ADX > 25, price above/below EMA_200, EMA_20 > EMA_50 (or <)        | Trend following        |
| Range-bound     | ADX < 20, price oscillating in BBands, Z-score mean-reverting      | Mean reversion         |
| High volatility | ATR expanding, rvol > 1.5× its 60-period mean                      | Wider stops, less size |
| Low volatility  | ATR compressing, BBands squeezing                                  | Breakout anticipation  |
| Choppy          | ADX < 20 but frequent EMA crossovers                               | Avoid, or tight filters|

Confirming statistics over ~90d of candles when the table is ambiguous:
- **Lag-1 return autocorrelation** — positive → trend, negative → mean reversion
- **% of bars with ADX > 25**
- **Hurst exponent** (rolling 150 bars) — H > 0.55 trending, H < 0.45 mean-reverting
- **Realized volatility** — ATR as % of price, 14-period rolling

**Verdict:** one line — `TRENDING`, `MEAN_REVERTING` or `AMBIGUOUS`, with the
supporting values.

**Go/no-go:** on `AMBIGUOUS`, either pick a shorter timeframe that resolves it or
ask the user whether to force a strategy type. Never build a trend controller on a
mean-reverting pair.

## Step 4 — Explore indicator combinations

For **TRENDING** pairs:
- EMA crossover (10/50, 20/100, 50/200)
- MACD (12/26/9) + ADX filter (> 20)
- Supertrend (multiplier 2–4, period 10–14)

For **MEAN_REVERTING** pairs:
- RSI extremes (period 14, levels 30/70 or 20/80)
- Bollinger %B (band touch/cross, 20/2.0)
- Rolling Z-score (period 30–60, threshold ±1.5–2.5)

For each combination compute:
- **Signal frequency** — signals per week
- **Average bars-in-trade** — consecutive bars holding the same signal
- **Directional accuracy** — % of signals followed by a 1R move the right way

**Go/no-go:** need ≥ 2 signals/week on the chosen timeframe **and** ≥ 55% raw
directional accuracy before continuing.

## Step 5 — Write the signal spec

```
signal_spec:
  type: TREND | MEAN_REVERSION
  indicator_1: name, params
  indicator_2: name, params        # optional filter
  long_condition:  <pandas boolean expression>
  short_condition: <pandas boolean expression>
  exit_condition:  <pandas boolean expression or None (use TP/SL)>
  timeframe: 1h | 4h | 1d
  lookback_bars: 300–500
```

The user must confirm this spec before Phase 2.

## Step 6 — Data quality check

Before handing off:
- No NaN bleed at the start of the window — drop the first `max(lookback)` bars
- **No look-ahead bias** — every computation that decides an entry bar uses
  `.shift(1)` where needed
- Signal frequency over the last 30 days matches the in-sample estimate (±50%)

## Step 7 — Research routine (optional)

If the user wants ongoing monitoring, write it yourself — read the
`routine_cookbook` playbook first, then create and test:

```
manage_skill(action="read", name="routine_cookbook")
manage_routines(action="create_routine", name="{pair}_research", code="<python>")
manage_routines(action="run", name="{pair}_research")
```

It should fetch the candles, compute the indicators, mark the signal column, and
report the last ~20 bars plus a regime summary. Fix it until the run is clean
before showing the user.

## Go/No-Go → Phase 2

✅ **GO** — regime identified with supporting data; a signal hypothesis with > 30
historical occurrences in the lookback; signal duration matches the intended
holding period; no liquidity or funding red flags.

❌ **NO-GO** — ADX < 15 chop with no mean-reversion setup; fewer than 20 historical
signal occurrences; pair under $1M daily volume; funding > 0.1%/8h against the
dominant signal direction.

## Artifacts

1. Market data summary (price range, volume, volatility stats)
2. Indicator dashboard (last 10–20 bars, all values)
3. Regime verdict + evidence
4. Signal spec (confirmed by the user)
5. Data quality check results
6. Research routine (optional)

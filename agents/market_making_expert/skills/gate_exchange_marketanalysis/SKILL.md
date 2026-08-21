---
name: gate_exchange_marketanalysis
description: Gate.io perpetual market analysis framework — trend, volatility, volume
  profile, order book, and funding rate read before deploying or adjusting any Gate
  MM strategy.
when_to_use: Before deploying, resuming, or tuning any market-making strategy on gate_io_perpetual.
  Run this analysis on the target pair to determine regime, optimal spread profile,
  fill timing, and whether conditions favor symmetric or asymmetric quoting.
created: '2026-08-18T03:19:30Z'
source: agent:market_making_expert
---

## Gate Exchange Market Analysis — Framework

### Step 1: Trend & Momentum
Pull 14-day hourly candles. Compute:
- 14-day range and % move
- 7-day and 1-day range
- Structure: higher highs / lower lows / ranging
- Flag: is today the first meaningful rejection? Or continuation?

Regime labels:
- `volatile_trending`: >20% in 7d, expanding candle bodies, surging volume
- `volatile_ranging`: wide swings but no sustained direction
- `quiet_ranging`: <5% in 7d, small bodies, low volume — ideal for tight symmetric MM

### Step 2: Volatility (ATR estimate from hourly candles)
- Quiet regime: candle bodies < $3–5, volume < 10K/hr
- Active regime: candle bodies $30–90, volume 50K+/hr
- Breakout regime: bodies $80–100+, volume 100K+/hr
→ Breakout/active = use asymmetric spreads; quiet = symmetric spreads viable

### Step 3: Volume Profile
Identify the 3–5 highest-volume candles over 14 days:
- Volume spike at low = capitulation (buy-side opportunity)
- Volume spike at breakout = momentum confirmation
- Volume spike at new high = potential distribution
Current volume vs. prior quiet hours: if still >3× quiet baseline, regime is still hot.

### Step 4: Order Book
Fetch snapshot. Key reads:
- Best bid/ask spread: if 1 tick → highly competitive, queue position matters
- Ask depth L1: if <0.1 contracts → paper thin, sell L1 fills instantly on any sweep
- Bid walls: large qty at a level = defended support, safe for buy-side quoting
- Ask walls: large qty at a level = resistance, good TP target zone

MM implications:
- Thin ask → place sell L1 at least 2–3 ticks behind best ask for queue safety
- Fat bid wall → buy L1 can be placed just above it to compete for queue

### Step 5: Funding Rate
- <+0.01%: neutral, no bias
- +0.01–0.03%: mild long premium — slight inventory lean to long is fine
- >+0.05%: crowded long — widen sell spreads or pause sells
- Negative: short squeeze risk — tighten buys, widen sells

### Step 6: Regime → Spread Profile Decision
| Regime | Buy spreads | Sell spreads | Skew | Notes |
|---|---|---|---|---|
| quiet_ranging | 0.02/0.08/0.20% | 0.02/0.08/0.20% | 1.0 | Symmetric |
| volatile_ranging | 0.03/0.10/0.25% | 0.05/0.15/0.35% | 1.5 | Mild asymmetry |
| volatile_trending | 0.02/0.08/0.20% | 0.04/0.16/0.40% | 1.8–2.0 | Sell side 2× wider |

### Step 7: Gate.io Rebate Check
- Confirm VIP tier has negative maker fee (target: −0.015%)
- take_profit=0.0003 (0.03%) = breakeven without rebate
- With rebate: +0.06% gross per round-trip
- Circuit breaker: if net PnL per fill is negative after first 20 fills → pause and check tier

### Output
After running this framework, state:
- regime: <label>
- spread_profile: symmetric | asymmetric | pause
- sell_L1_timing: deploy_now | wait_for_range (price oscillating <0.5% over 30 mins)
- rebate_confirmed: yes | no | unverified

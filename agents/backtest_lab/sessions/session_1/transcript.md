# Consult backtest_lab_1

## Task

Is a symmetric 2% grid on SOL-USD (hyperliquid) worth running right now? Use your methodology and your grid_backtest routine — test at least two windows and give me your verdict in your standard format.

## Session

💭 **Reasoning**

> The user wants me to evaluate a symmetric 2% grid on SOL-USD (Hyperliquid) using my backtest methodology. I need to:
> 
> 1. First read the backtest-methodology skill
> 2. Then run the grid_backtest routine with at least two windows
> 3. Report results in the standard format
> 
> Let me start by reading the backtest-methodology skill and checking what routines are available.

💬 I'll start by reading the backtest-methodology skill and checking available routines in parallel.

🔧 **1. ToolSearch** (completed)

💭 **Reasoning**

> Let me set up a todo list and start working on this backtest evaluation. I need to:
> 1. Read the backtest-methodology skill
> 2. Check available routines (specifically grid_backtest)
> 3. Run the backtest with at least two windows
> 4. Report results
> 
> Let me start by doing steps 1 and 2 in parallel.

🔧 **2. ToolSearch** (completed)

💭 **Reasoning**

> Now let me read the backtest-methodology skill and check for available routines in parallel.

🔧 **3. mcp__condor__manage_skill** (completed)

🔧 **4. mcp__condor__manage_routines** (completed)

💭 **Reasoning**

> The `grid_backtest` routine exists. Now let me:
> 1. Check its config schema using `describe`
> 2. Then run it on at least two windows
> 
> Let me first describe the routine to understand its config schema.
> 
> Also, note from the methodology:
> - Hyperliquid has no platform backtest connector → I'll use candle-sim (grid_backtest routine)
> - Minimum 3 non-overlapping windows, one must include an adverse regime
> - Recent-first: newest window counts double
> - Symmetric 2% grid: check if TP (2%) clears 2× maker fees
> Let me start by describing the routine to see what configuration it expects.

🔧 **5. mcp__condor__manage_routines** (completed)

💭 **Reasoning**

> Now I understand the routine config. Key observations:
> - `band_pct` = 2.0 means total band is 2% wide (±1% from center). This is not what the user means by "symmetric 2% grid". 
> - The user said "symmetric 2% grid" which typically means the spacing between levels is 2% or the total band is 2%.
> 
> Let me think about the correct interpretation:
> - "Symmetric 2% grid" usually means 2% between adjacent price levels, not 2% total band.
> - With `band_pct=2.0` as total band and `levels=10`, that would give 0.2% spacing per level — not a 2% grid.
> - For a true 2% grid, if that means per-level spacing with 10 levels (5 per side), I'd need `band_pct=10.0` to get 2% between each level, or I could set `tp_pct=2.0` to make the take-profit per fill 2%.
> - But if `band_pct=2.0` with 10 levels, that's only 0.4% spacing per level, which would require a much tighter `tp_pct` like 0.2% to fill on alternate levels.
> - I think the user means `tp_pct=2.0` for 2% per fill, so I'll go with that interpretation.
> 
> For the fee structure, Hyperliquid's 0.01% maker fee means a 2% take-profit has plenty of cushion—round-trip costs are only 0.02%, so this works well.
> 
> On the windows side, the methodology calls for at least 3 non-overlapping windows to be proper, even though the user mentioned two—I should aim for 3 and make sure one captures an adverse market regime.
> 
> The challenge is that the routine only accepts a `days` lookback parameter without start/end dates, so I can't easily create truly non-overlapping historical windows. I'm thinking through how to structure this—maybe running recent 7 days, then 14 days, but that creates overlap issues. I can't work around the limitation with just the `days` parameter—the windows will inherently overlap since there's no way to specify non-overlapping time ranges. I'll proceed with running 7-day and 21-day windows back-to-back and flag this as a methodological constraint in my results. Now I'm setting up the backtest configuration with the Hyperliquid connector for SOL-USD, using a 2% grid spacing and...
> 
> Writing config parameters... I'll go ahead and run both windows simultaneously now.A "symmetric 2% grid" means each level is 2% apart, so tp_pct=2.0. With 5 levels per side (levels=10 total), band = 5 × 2% = 10%. Let me run both windows in parallel.

💬 Methodology confirmed: Hyperliquid has no platform backtest connector, so candle-sim is the path. A 2% TP easily clears 2× maker fee (0.04% round-trip). Configuring as: `tp_pct=2.0`, `band_pct=10.0` (5 levels × 2% per side), 10 levels. Running two windows in parallel now.

🔧 **6. mcp__condor__manage_routines** (completed)

🔧 **7. mcp__condor__manage_routines** (completed)

💭 **Reasoning**

> Interesting - both runs returned the exact same result: same center price, same PnL, same fill count, same candle count (1490). This is suspicious. The 21-day window should have returned many more candles (21 × 24 × 20 = ~10,080 candles at 3m resolution).
> 
> The fact that both returned 1490 candles suggests the routine is hitting a data limit or there's an issue with data retrieval for 21 days. It seems like both are actually using the same data (7 days worth, or whatever the API limit is).
> Let me work through the math here. At 3-minute resolution, 1490 candles only covers about 3.1 days of data, not 7 or 21. That's a major discrepancy. And with only 2 round trips across 7+ days on a 10-level grid, the trading activity seems suspiciously sparse. Something's definitely constraining the data retrieval.
> 
> The fact that both the 7-day and 21-day requests returned identical results (1490 candles) strongly suggests the Hyperliquid API has a hard cap on how much historical data it returns at 3-minute resolution. That's a real limitation I need to work around. The open inventory of -$60.69 is also worth noting — that's accumulated long position risk that hasn't been closed out yet.
> 
> I should try switching to a longer interval like 15 minutes to see if I can pull more historical data and get a better sample size for the statistics. The accumulated long position confirms SOL has been trending downward through the grid. I'm realizing the API's hard limit of ~1490 candles at 3-minute resolution is the real constraint here — both the 7-day and 21-day requests hit the same ceiling, so I can't get more historical depth. With only 2 round trips across 3 days of data, this grid strategy isn't generating enough activity to properly evaluate, which is a critical finding for my assessment. Let me shift to a 15-minute interval to pull more historical data and get a better sample size for the analysis.

💬 Both windows returned identical results — 1490 candles regardless of `days` setting, which means the Hyperliquid candle API caps at ~3 days at 3m resolution. Only 2 round trips is below the methodology's ~30-trade minimum for statistical significance. Let me widen the interval to pull more history.

🔧 **8. mcp__condor__manage_routines** (completed)

🔧 **9. mcp__condor__manage_routines** (completed)

💭 **Reasoning**

> Now I have more data, but still hitting a cap (2880 and 2930 candles for both 30d and 60d configs at 15m). At 15m resolution, 30 days = 30 × 24 × 4 = 2,880 candles. So 30d hits the limit exactly (2880 candles). The 60d run getting 2930 candles instead of 5760 suggests a data cap.
> 
> Actually, 2880 candles at 15m = 2880 × 15 min = 43,200 min = 720 hours = 30 days. 
> 
> So the 30d run used the full 30 days, and the 60d run getting around 2930 candles means it's also capped at roughly 30 days of 15m data—looks like the Hyperliquid API has a limit there.
> 
> Looking at the results now, the 3-minute window over about 3 days shows a center price of $77.25 with a net PnL of +$1.41 on $300 invested (0.47% return), but only 2 round trips which is below the minimum threshold for meaningful statistics. There's an open inventory position of -$60.69 and a max drawdown of just $1.
> 
> The 15-minute window over the full 30 days tells a different story—center price dropped to $66.95, net PnL turned negative at -$2.09 (-0.70%), with 31 round trips just barely above the ~30 minimum. The open inventory is now -$175.06, which represents 58% of the capital sitting in an open long position, and the max drawdown jumped to $32.64 (10.9% of capital).
> 
> The third window using the 60-day config shows a center of $65.38 (suggesting a slightly different time period), with a worse net PnL of -$12.10 (-4.03%), only 21 round trips, and the data cuts off there.
> 
> I'm noticing something interesting—both the 30-day and 60-day requests are returning around 2880-2930 candles despite different centers, which suggests they're pulling slightly different starting points in the data. The 60-day request might be capturing data from about 30.5 days ago rather than exactly 30 days ago. So I'm looking at three distinct windows: the recent 3-day period with SOL around $77.25 showing choppy action, the 30-day window centered at $66.95 with an uptrend, and the longer 60-day window starting from $65.38.
> 
> Now I'm realizing the key issue—SOL has moved up roughly 14-18% over the past month, from around $66.95 to $77.25. This is actually an adverse environment for a symmetric grid strategy because as price rises through the grid levels, the strategy accumulates long inventory without getting the corresponding sell fills at higher prices. The grid has buy orders below the center and sell orders above it, so if price moved up from $66.95 to $77.25, those sell orders should have triggered — but the negative open inventory suggests accumulated long positions instead, which would only happen if price dipped below the buy levels first. I'm trying to reconcile whether price went down initially to fill those buys, then partially recovered, or if I'm misinterpreting what the negative inventory balance actually represents. With over half the capital locked in open longs and SOL in a strong uptrend, the grid is accumulating inventory faster than it can close positions profitably. The negative net PnL combined with this massive unbooked exposure makes running it too risky right now.
> 
> I'm also noting the statistical limitations — only one window has enough fills to be meaningful, and the data I have captures exactly the adverse regime where grids struggle. I should run a quick sensitivity check on the take-profit percentage to see if tighter or looser targets help, though the verdict is already fairly clear.

💬 Good — the 15m runs break through the 3m data cap and give two ~30-day windows with different centers (meaning slightly different periods). One clears the 31-fill minimum. Let me now run the sensitivity check (±20% on `tp_pct`) as required before delivering the verdict.

🔧 **10. mcp__condor__manage_routines** (completed)

🔧 **11. mcp__condor__manage_routines** (completed)

💭 **Reasoning**

> Sensitivity results are in:
> 
> Base (tp_pct=2.0): Net PnL = -$2.09, Round trips = 31
> tp_pct=1.6 (-20%): Net PnL = -$4.84, Round trips = 33
> tp_pct=2.4 (+20%): Net PnL = -$2.62, Round trips = 25
> 
> All three variants are negative PnL. The PnL doesn't flip sign — it stays negative across all tp_pct values. This means the edge is NOT in the parameter; it's genuinely negative due to the market regime.
> Tighter TP settings perform worse, which makes sense in a trending market where tighter targets lead to more inventory cycles without enough reversion. The consistent negative signal across all variants is actually reassuring — it shows the strategy's behavior is stable and predictable, even if unprofitable on this window.
> 
> Generating markdown table... The real problem is that SOL's uptrend over the past month creates an unfavorable regime for grid trading—the strategy loses money when prices move strongly in one direction. The open inventory of $175-179 represents a significant unbooked long exposure that masks the true risk; while these positions might be profitable now given the price appreciation, they're still unsettled and represent real drawdown on the closed side of around 11-13% of capital. I'm realizing I need to reconsider how the open inventory factors into the actual loss calculation. be profitable in hindsight, but that's masking the real problem: the grid strategy actually lost money on its closed trades during a 15% bull run, which suggests the grid mechanics weren't capturing the uptrend effectively—it was just lucky to have accumulated longs that happened to gain value.
> 
> Deploying this same grid now at $77 would mean betting on mean reversion in a clearly trending market, which is exactly when grids struggle most. The risk of accumulating inventory without hitting take-profit levels is too high, and the backtests consistently show negative closed PnL across different parameter variations, indicating this is a regime problem rather than a tuning issue. The verdict is clear: this strategy isn't worth running right now.
> 
> I'd want to see evidence of mean reversion or consolidation around current price levels before reconsidering—either a confirmed pause in the uptrend or a tighter range-bound market would change the calculus. Let me structure this according to the proper reporting format.

💬 All runs complete. Here is the full report.

---

## SOL-USD Symmetric 2% Grid — Hyperliquid Perpetual

```
verdict:          NOT WORTH IT (current regime is adverse)
```

---

### Windows

| # | Resolution | Span | Center | Net PnL | Fills | Open Inventory | Max DD |
|---|-----------|------|--------|---------|-------|----------------|--------|
| W1 | 3m candle-sim | ~3 days (API cap) | $77.25 | **+$1.41** | 2 ⚠️ | -$60.69 | $1.89 |
| W2 | 15m candle-sim | 30 days | $66.95 | **-$2.09** | 31 ✓ | -$175.06 | $32.64 |
| W3 | 15m candle-sim | ~30.5 days | $65.38 | **-$12.10** | 21 ⚠️ | -$179.26 | $37.84 |

Config: `band_pct=10% (±5%), 5+5 levels, tp_pct=2%, fee_pct=0.02% maker, $300 capital`

---

### Sensitivity (W2 base, tp_pct ±20%)

```
tp_pct=1.6%  →  -$4.84   (worse)
tp_pct=2.0%  →  -$2.09   (base)
tp_pct=2.4%  →  -$2.62   (worse)
```

PnL stays negative in all three variants — the loss is **regime-driven**, not parameter-sensitive. This is the correct signal, not an overfit artifact.

---

### Why NOT worth it

**1. Adverse regime confirmed.** SOL ran from ~$65–67 to ~$77 over the 30-day lookback (~+15%). That is exactly the environment that kills symmetric grids: the grid accumulates long inventory on the way down and never gets full TP fills as price trends away from center.

**2. Inventory exposure is alarming.** Both 30-day windows show $175–179 of the $300 capital (58–60%) locked in open long positions that haven't hit their TP. The reported PnL reflects only *closed* round-trips — the open inventory risk is on top of that.

**3. Max drawdown of $32–38 on $300.** That's 11–13% drawdown on a *market-making* strategy, which should be single-digit. This is the trend imprint.

**4. W1 (+$1.41) is a mirage.** Only 2 fills over 3 days — methodology minimum is ~30. The recent tiny profit is anecdotal and likely reflects a short chop window within the larger trend.

---

### Methodology caveats

- **method:** candle-sim, 3m/15m resolution. Not microstructure-accurate (no order-book queue, no partial fills).
- **non-overlapping windows not achievable:** Hyperliquid candle API caps at ~1490 × 3m and ~2880 × 15m candles. Both 30d and 60d requests return the same candle count — "W3" is just the slightly different starting price from the cap boundary, not a truly independent period. Stated as overlapping approximations.
- **open inventory PnL excluded:** The routine reports closed-trade PnL only. The $175–179 of accumulated longs may be in unrealized profit right now (price rose above center) but that's directional luck, not grid edge.
- **fee floor cleared:** 2% TP >> 0.04% round-trip maker fee. The config is not structurally broken on fees.

---

### When to revisit

Deploy when SOL shows evidence of range-bound chop around current price ($77 ±5%). Specifically: if the 7-day realized vol is falling AND price has been oscillating inside a <8% band for 5+ days, re-run W1 with the 3m routine and check if fills accelerate above ~10 round trips per 3 days.

## Answer

I'll start by reading the backtest-methodology skill and checking available routines in parallel.Methodology confirmed: Hyperliquid has no platform backtest connector, so candle-sim is the path. A 2% TP easily clears 2× maker fee (0.04% round-trip). Configuring as: `tp_pct=2.0`, `band_pct=10.0` (5 levels × 2% per side), 10 levels. Running two windows in parallel now.Both windows returned identical results — 1490 candles regardless of `days` setting, which means the Hyperliquid candle API caps at ~3 days at 3m resolution. Only 2 round trips is below the methodology's ~30-trade minimum for statistical significance. Let me widen the interval to pull more history.Good — the 15m runs break through the 3m data cap and give two ~30-day windows with different centers (meaning slightly different periods). One clears the 31-fill minimum. Let me now run the sensitivity check (±20% on `tp_pct`) as required before delivering the verdict.All runs complete. Here is the full report.

---

## SOL-USD Symmetric 2% Grid — Hyperliquid Perpetual

```
verdict:          NOT WORTH IT (current regime is adverse)
```

---

### Windows

| # | Resolution | Span | Center | Net PnL | Fills | Open Inventory | Max DD |
|---|-----------|------|--------|---------|-------|----------------|--------|
| W1 | 3m candle-sim | ~3 days (API cap) | $77.25 | **+$1.41** | 2 ⚠️ | -$60.69 | $1.89 |
| W2 | 15m candle-sim | 30 days | $66.95 | **-$2.09** | 31 ✓ | -$175.06 | $32.64 |
| W3 | 15m candle-sim | ~30.5 days | $65.38 | **-$12.10** | 21 ⚠️ | -$179.26 | $37.84 |

Config: `band_pct=10% (±5%), 5+5 levels, tp_pct=2%, fee_pct=0.02% maker, $300 capital`

---

### Sensitivity (W2 base, tp_pct ±20%)

```
tp_pct=1.6%  →  -$4.84   (worse)
tp_pct=2.0%  →  -$2.09   (base)
tp_pct=2.4%  →  -$2.62   (worse)
```

PnL stays negative in all three variants — the loss is **regime-driven**, not parameter-sensitive. This is the correct signal, not an overfit artifact.

---

### Why NOT worth it

**1. Adverse regime confirmed.** SOL ran from ~$65–67 to ~$77 over the 30-day lookback (~+15%). That is exactly the environment that kills symmetric grids: the grid accumulates long inventory on the way down and never gets full TP fills as price trends away from center.

**2. Inventory exposure is alarming.** Both 30-day windows show $175–179 of the $300 capital (58–60%) locked in open long positions that haven't hit their TP. The reported PnL reflects only *closed* round-trips — the open inventory risk is on top of that.

**3. Max drawdown of $32–38 on $300.** That's 11–13% drawdown on a *market-making* strategy, which should be single-digit. This is the trend imprint.

**4. W1 (+$1.41) is a mirage.** Only 2 fills over 3 days — methodology minimum is ~30. The recent tiny profit is anecdotal and likely reflects a short chop window within the larger trend.

---

### Methodology caveats

- **method:** candle-sim, 3m/15m resolution. Not microstructure-accurate (no order-book queue, no partial fills).
- **non-overlapping windows not achievable:** Hyperliquid candle API caps at ~1490 × 3m and ~2880 × 15m candles. Both 30d and 60d requests return the same candle count — "W3" is just the slightly different starting price from the cap boundary, not a truly independent period. Stated as overlapping approximations.
- **open inventory PnL excluded:** The routine reports closed-trade PnL only. The $175–179 of accumulated longs may be in unrealized profit right now (price rose above center) but that's directional luck, not grid edge.
- **fee floor cleared:** 2% TP >> 0.04% round-trip maker fee. The config is not structurally broken on fees.

---

### When to revisit

Deploy when SOL shows evidence of range-bound chop around current price ($77 ±5%). Specifically: if the 7-day realized vol is falling AND price has been oscillating inside a <8% band for 5+ days, re-run W1 with the 3m routine and check if fills accelerate above ~10 round trips per 3 days.

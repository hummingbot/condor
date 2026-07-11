---
name: backtest-methodology
description: "Window selection, fee/slippage realism, overfit guards, and the reporting format for honest backtests. Use when designing, running, or judging any backtest — before trusting a result or recommending a config."
metadata: {"condor-source": "agent:backtest_lab", "condor-created": "2026-07-11T22:12:01Z"}
---

# Backtest Methodology

The rules that keep a backtest honest. Read before designing one; cite the
relevant rule when judging one.

## Windows
- Never judge on a single window. Minimum: 3 non-overlapping windows, one of
  which must include an adverse regime for the strategy (a trend for MM/grids,
  chop for trend-followers).
- Recent-first: the newest window counts double — market microstructure decays.
- State the window in every result. A result without its window is noise.

## Fees & frictions (the #1 killer of paper edges)
- ALWAYS simulate fees: use round-trip maker fees minimum (see the shared
  `executor-mechanics` fee table); taker where fills would cross.
- A maker strategy's TP below 2× maker fee loses by construction — reject the
  config without running it.
- Add slippage on any market-order leg (≥1 tick or 0.02%, whichever is more).
- Candle simulation fill rule: a resting buy at P fills only if candle low < P
  (sell: high > P). Same-candle double-touch: assume ONE side fills, not both.

## Overfit guards
- Vary each tuned parameter ±20%: if pnl flips sign, the edge is the
  parameter, not the market — reject.
- Prefer fewer knobs. Every tuned parameter costs one 'degree of trust'.
- Compare against the do-nothing baseline AND buy-and-hold over the same
  window. An MM/grid result below buy-and-hold on a trending window is
  expected — say so instead of hiding it; below baseline on its FAVORABLE
  regime is disqualifying.

## Platform vs candle-sim (this deployment)
- Platform `run_backtest` needs the venue's data feed: binance is geo-blocked
  here (451), hyperliquid has no backtest connector. Try once; on these
  errors, switch to candle-simulation routines — do not retry-loop.
- Candle sims are coarser than the platform engine (no order-book queue, no
  partial fills). State 'candle-sim, N-minute resolution' in every result so
  nobody mistakes it for microstructure-accurate.

## Reporting format
verdict: worth_running | not_worth_it | needs_more_data
window(s): <ranges + resolution + venue>
method: platform | candle-sim
net_pnl_after_fees: <per window>
max_drawdown: <per window>
fills/trades: <count — under ~30 total means the stats are anecdotes>
sensitivity: <what ±20% on key params did>
caveats: <one line minimum; 'none' is almost never true>

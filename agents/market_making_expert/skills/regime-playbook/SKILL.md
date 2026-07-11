---
name: regime-playbook
description: "The single regime classification + market-making posture mapping for this agent — thresholds (ADX/ATR/BBW), the five regimes (quiet, ranging, trending up/down, volatile), and the general spread/inventory posture per regime, with per-strategy parameter tables as companions. Use when classifying the market regime, deciding posture (tighten/widen/skew/pause), or translating a regime into pmm_mister parameters."
metadata: {"condor-source": "agent:market_making_expert", "condor-created": "2026-07-11"}
---

# Regime Playbook

This is the ONE place regime knowledge lives for this agent. Playbooks and
consults read it; do not restate these tables elsewhere.

## How to get the regime

Run the `market_analyzer` routine
(`manage_routines(action="run", name="market_analyzer", config={"trading_pair": ..., "connector_name": ...})`).
It returns `overall_regime` plus the evidence (ADX, ATR%, BBW, RSI,
funding_rate, ranges). Trust the routine's classification unless the
evidence visibly contradicts it — then journal why.

## Classification thresholds

| Regime | Signature |
|---|---|
| **Quiet** | ADX < 18, BBW < 3%, ATR compressing, low volume |
| **Ranging** | ADX < 25, price oscillating around MA, moderate BBW |
| **Trending up/down** | ADX > 25, price consistently above/below short MA, candle bodies > wicks |
| **Volatile** | ATR expanding, BBW > 6%, large candles, funding spikes, volume surge |

## Posture per regime (strategy-independent)

- **Quiet** — tightest spreads, fastest refresh; low adverse selection.
  The best regime for symmetric market making.
- **Ranging** — moderate symmetric spreads; standard timing. Grid-eligible.
- **Trending** — asymmetric: widen the side that fights the trend
  (sells in an uptrend, buys in a downtrend); consider directional
  position_side; protect accumulated profit. Grids stand down.
- **Volatile** — widen both sides or pause; slow refresh, long cooldowns,
  tight inventory bands, hard stops on. Capital preservation.
- **Inventory overlay (any regime)** — skewed > 30% of allocation to one
  side → reduce that side; skew > 70% in an adverse regime → stop. Factor
  funding on skewed perp positions (|funding| > 0.015%/8h charges the
  position side → flip bias or halve size).

## Per-strategy parameter tables (companions)

- `pmm_parameters.md` — regime → pmm_mister controller parameters
  (spreads, refresh, cooldowns, inventory bands, protections).
- Grid banding derives from measured ATR% and lives in the
  grid-range-harvester playbook itself (decision logic, not shared state);
  the grid_executor schema is in the shared `executor-mechanics` skill.

---
name: fly_decoder
description: How spike counts become a quoting posture, how to read fly_status, and what
  each number can and cannot tell you.
when_to_use: When explaining a posture, answering "why did the fly widen/lean/pause",
  "is it learning", or checking whether a run is healthy.
created: '2026-09-12T00:00:00Z'
source: agent:market_making_fly
references_routine: fly_status
---

# Reading the fly

Run `manage_routines(action="run", name="fly_status", config={"run_name": "<run>"})`
for the numbers as text, or `fly_report` for the dashboard — the same data plus the
fly itself, the decision log and the last frame it saw. Use `fly_report` when the
user wants to *look* at the run, `fly_status` when you need to quote figures.

## Channels (per observation, 500 ms of neural time)

| Field | Cells | Meaning |
|---|---|---|
| `trend_hz` | DNp20 right mean rate − left mean rate | lean direction; stonkfly's BUY/SELL cells |
| `arousal_hz` | mean rate of the 1,314 descending neurons (minus the readouts) | spread width **and** how much of the book is quoted |
| `gate_spikes` | DNpe017 | ≥ 1 required for a trending call and for any lean |
| `valence_hz` | mean MBON07 rate − mean MBON11 rate | approach minus avoidance — **the memory rule's own output**, and the only channel a P&L pulse can reach. Drives how much of the book is quoted |
| `kc_spikes` | Kenyon cells | the confidence test: no sparse code of the chart means every other channel is reading noise, so the posture is marked unconfident and the loop holds |
| `reward_spikes` / `aversive_spikes` | PAM11 / PPL101 | did the pulse arrive |

## Posture

* `trend_z`, `arousal_z`: the channel minus its rolling per-pair mean, over its std,
  window 60 observations. `warm=False` for the first 10 — neutral posture.
* Regime precedence: `pause` (arousal_z ≥ 2.5) > `volatile` (≥ 1) > `trending_up/down`
  (gate and |trend_z| ≥ 1) > `quiet` (arousal_z ≤ −1) > `ranging`.
* `spread ×` = clip(1 − 0.5·arousal_z, 0.6, 2.5) — an aroused fly quotes **tighter**.
  `size ×` = clip(1 + 0.5·arousal_z, 0.6, 2.5) — and quotes **more** of the book,
  clamped so an order never falls under the venue minimum nor the allocation over 1.
  `size ×` also carries `+0.5·valence_z`, so what the fly has learned about scenes
  like this one moves the capital it commits. `tp ×` = clip(1 + 0.5·arousal_z, 0.6,
  2.5) on a base of 0.75 × the market's median candle range — an active market both
  fills sooner and travels further, so the fly holds for more of it.
  The sign on each is a choice, not a finding: arousal is a population rate against
  its own average and nothing ties it to volatility. `lean` = clip(trend_z, ±3 bp), 0
  without a gate spike, and capped at half the first spread level when mapped.
* Mapping: level 1 = max(2, R/2) bp, level 2 = level 1 + R/4 (R = the market's
  median candle range), times
  `spread ×`, buy −lean / sell +lean, floor 3 bp; TP = max(4 bp, 2.2 × round-trip fee,
  first level); timing per regime; `pause` sets `manual_kill_switch`.

## Execution statuses in the event log

`SHADOW` would have applied (shadow mode) · `APPLIED` live update done · `HOLD` no
material change or per-pair 5-minute cooldown · `VETO` guard refused (reason given) ·
`CLOSED` book closed, fly did not observe · `STOP_BOT` closed ≥ 5 ticks · `ERROR`
update failed · `HALT` loop stopped · `TICK_ERROR` data fetch failed, loop continues.

## What you may say

* "The fly's arousal channel is 1.8 σ above its baseline on DRAM, so it tightened to
  0.6× and quoted 1.9× the usual share of the book."
* "No gate spike this observation, so no lean regardless of trend."
* "Changed edges rose from 0 to 312 after the first aversive pulse."

## What you may not say

* That the fly detected a regime: it emitted a z-score we labelled.
* That a P&L pulse taught it anything: pulses are value feedback, not credit assignment,
  and edges change from endogenous dopamine activity too. The valence channel means the
  memory rule can now *reach* a decision — it does not mean what it reaches is right.
* That a run with positive P&L shows the fly works: a rising market makes any long
  inventory look skilled. No held-out replay or shuffled-reinforcement control exists.

## Health checks

* `kc_spikes` stays 0 → the chart is not activating the mushroom body. The loop now
  holds instead of applying (`HOLD` with "scene not seen"); a run that does this
  every tick is quoting a stale config, so report it.
* `baseline_n` not growing for a pair → that pair's book is closed or its feed fails.
* `halted` set → read the reason; financial halts need a new `run_name`.
* `compute_seconds` above ~40 % of the interval → lower `neural_ms`.

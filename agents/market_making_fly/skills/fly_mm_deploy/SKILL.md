---
name: fly_mm_deploy
description: End-to-end deployment of the fly market maker on up to three HIP-3 pairs —
  scan, deploy neutral pmm_mister bots with the fly's naming, start fly_brain in shadow,
  verify, and (only when told) switch to live.
when_to_use: When asked to set up, deploy, launch, or restart the fly market maker, or
  to rotate one of its market slots. Follow it as a delegate task with no mid-flow
  confirmation.
created: '2026-09-12T00:00:00Z'
source: agent:market_making_fly
---

# Fly MM Deploy

You are deploying **Market Making Fly**: one shared fly brain, up to three HIP-3
markets, `pmm_mister` controllers. The fly decides posture; you set up the plumbing.

## Step 1 — Pick the markets

```
manage_routines(action="run", name="hip3_market_scanner",
  config={"issuer": "xyz", "min_spread_bps": 3, "max_daily_drift_pct": 3, "top_n": 5})
```

Take the top **`n_markets`** survivors (1–3; from `[CURRENT CONFIG]` or the task,
default 3) that have an open live book — one brain quotes them all in round-robin. Record for each: `pair` (uppercase, e.g. `XYZ:DRAM-USD`) and its **spread in
bp** — this is `picked_spreads_bps`. If fewer than one survivor, stop and report.

## Step 2 — Collateral

`get_portfolio_overview(["hyperliquid_perpetual"])` → available USD. Required ≈
`Σ total_amount_quote × 0.5 / leverage` across the pairs. If short, reduce
`total_amount_quote` or drop a pair; say so in the report.

## Step 3 — Neutral configs (the fly's starting point)

For each pair derive `token` (e.g. `DRAM`), `bot_name = {token.lower()}-fly`,
`config_name = {token.lower()}_fly_mm`. The neutral config is exactly what
`fly_brain` would apply for the `ranging` regime; build it with:

```python
run_code(code="""
import sys; sys.path.insert(0, "agents/market_making_fly")
from flybrain.posture import MarketSpec, build_config
from flybrain.decoder import NEUTRAL
spec = MarketSpec(connector_name="hyperliquid_perpetual", trading_pair="XYZ:DRAM-USD",
                  total_amount_quote=500, picked_spread_bps=8.0, leverage=3)
print(build_config(spec, NEUTRAL))
""")
```

Then save it:

```
manage_controllers(action="upsert", target="config", config_name="dram_fly_mm",
                   config_data={...printed config...}, confirm_override=True)
```

Do not edit the spreads, TP, bands or stop loss by hand — the floors live in code.

## Step 4 — Deploy the bots

One bot per pair, named exactly `{token}-fly`, with a loss cap:

```
manage_bots(action="deploy", bot_name="dram-fly", controllers_config=["dram_fly_mm"],
            max_global_drawdown_quote=<0.04 × total_amount_quote>)
```

Confirm with `manage_bots(action="status")` that each bot is running with its
controller.

## Step 5 — Start the fly in shadow

`pairs` and `picked_spreads_bps` list exactly the `n_markets` picks, same order.
With `n_markets: 1` that is a single pair and a single spread.

```
manage_routines(action="start", name="fly_brain", config={
  "pairs": "XYZ:DRAM-USD,XYZ:SPCX-USD,XYZ:SMSN-USD",   # n_markets entries
  "picked_spreads_bps": "8,6,10",                       # one per pair
  "total_amount_quote": 500, "leverage": 3,
  "mode": "shadow", "run_name": "fly-2026-09-12"})
```

Shadow observes, decodes and reinforces from the bots' P&L but applies nothing. Note
the instance id. After ~10 observations per pair (30 ticks) `fly_status` shows
non-neutral postures.

## Step 6 — Verify

```
manage_routines(action="run", name="fly_status", config={"run_name": "fly-2026-09-12"})
manage_routines(action="run", name="mm_bot_report", config={})
```

Report: pairs, spreads, bots running, fly tick count, first postures, any vetoes or
halts, and the caveat that the fly's learning is not validated.

## Step 7 — Live (only when the task says so)

Stop the shadow instance (`manage_routines(action="stop", name="<instance_id>")`) and
start again with `"mode": "live"` and the **same** `run_name` — the baseline and the
brain's memory carry over. Never start live on a fresh `run_name` without a shadow
period first.

## Rotation

When a slot's market is closed, dominated, or the operator asks: stop that bot
(`manage_bots(action="stop_bot", bot_name=...)`), re-run the scanner, deploy the new
pick with Steps 3–4, stop `fly_brain` and start it again with the updated `pairs` and
`picked_spreads_bps` and the same `run_name`. The brain keeps its memory; only the
swapped pair's baseline starts over.

## Halts

`fly_brain` stops itself and (in live) stops the bots on a halt. A **transient** halt
(repeated config-update failures) restarts with `"resume_reviewed": true` after you
have looked at the bot logs. A **financial** halt (loss stop, loss-rate breaker, no
new P&L high) cannot be cleared: report it, leave the bots stopped, and only redeploy
under a new `run_name` if the operator asks.

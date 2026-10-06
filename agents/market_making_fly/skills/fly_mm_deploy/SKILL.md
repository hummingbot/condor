---
name: fly_mm_deploy
description: End-to-end deployment of the fly market maker on up to three markets on
  any CLOB venue, spot or perp — pick the markets, deploy neutral pmm_mister bots with
  the fly's naming, start fly_brain in shadow, verify, and (only when told) go live.
when_to_use: When asked to set up, deploy, launch, or restart the fly market maker, or
  to rotate one of its market slots. Follow it as a delegate task with no mid-flow
  confirmation.
created: '2026-09-12T00:00:00Z'
source: agent:market_making_fly
---

# Fly MM Deploy

You are deploying **Market Making Fly**: one shared fly brain, up to three markets
on any CLOB venue, `pmm_mister` controllers. The fly decides posture; you set up
the plumbing.

## Step 0 — Settle the venue

- `connector_name`: any CLOB connector hummingbot-api serves. A `_perpetual`
  suffix means perp, anything else is spot.
- **Spot**: `leverage` must be 1, and the fee floor is far wider — check
  `maker_fee_bps` before anything else.
- **`maker_fee_bps`**: pass the exchange's real per-side maker fee when you know
  it. Leaving it at 0 uses a conservative default, which quotes wider than
  necessary rather than tighter than is profitable.

## Step 1 — Pick the markets

```
manage_routines(action="run", name="mm_market_scanner", config={
  "connector_name": "<connector>", "issuer": "xyz",   # issuer only on HIP-3
  "quote": "USDT",                                     # optional, e.g. spot venues
  "prescreen": 30, "top_n": 5})
```

It ranks by volume, by whether a typical candle travels the round trip the fly
must make — down to its quote, back out through the take-profit — and by book
depth, then reports why every rejected market failed.
Take the top **`n_markets`** (1-3; from `[CURRENT CONFIG]` or the task, default
3) with an open book — one brain quotes them all in round-robin. Record each
`pair` and its **median candle range in bp**; that is `picked_ranges_bps`. The
quote levels are built from how far the market travels, not from how wide its
touch is: level 1 sits at half a typical bar, where the spread multiplier can
actually change whether a bar reaches it.

**If nothing survives, raise `prescreen` before anything else.** Reading a book
costs a call, so only the busiest markets are read — and the busiest markets are
not the most reachable. On Hyperliquid the heaviest HIP-3 markets barely move
2 bp in five minutes against a 7.7 bp cycle; the first market that cleared it
sat fourth by volume. A `TOP PICK: none` line means the scan did not look far
enough, or this venue genuinely does not come to a resting quote.

If it still finds nothing, stop and report that rather than lowering the
floor: quoting inside the fee loses money on every fill.

## Step 2 — Collateral

`get_portfolio_overview(["hyperliquid_perpetual"])` → available USD. Required ≈
`Σ total_amount_quote × 0.5 / leverage` across the pairs. If short, reduce
`total_amount_quote` or drop a pair; say so in the report.

## Step 3 — Neutral configs (the fly's starting point)

For each pair derive the slug from the **whole** pair — `SOL-USDT` → `sol-usdt`,
`XYZ:ORCL-USD` → `xyz-orcl-usd` — then `bot_name = {slug}-fly` and
`config_name = {slug}_fly_mm` (underscores). Never name a bot after the base token
alone: `BTC-USDT` and `BTC-USDC` would collide. The neutral config is exactly what
`fly_brain` would apply for the `ranging` regime; build it with:

```python
run_code(code="""
import sys; sys.path.insert(0, "agents/market_making_fly")
from flybrain.posture import MarketSpec, build_config
from flybrain.decoder import NEUTRAL
spec = MarketSpec(connector_name="binance_perpetual", trading_pair="SOL-USDT",
                  total_amount_quote=500, range_bps=10.0, leverage=3,
                  portfolio_allocation=0.2)   # spot: leverage=1, and check maker_fee_bps
print(build_config(spec, NEUTRAL))
""")
```

`build_config` refuses a spec whose orders would fall under the exchange minimum
(10 USD on HIP-3, 5-10 USD on most spot venues): each order is `total_amount_quote × portfolio_allocation / 4`.
200 quote on one market needs `portfolio_allocation` ≥ 0.2; 100 quote needs ≥ 0.4.
Whatever value you use here, pass the **same** `portfolio_allocation` to `fly_brain`
in Step 5 — it rebuilds every config from it.

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

`pairs` and `picked_ranges_bps` list exactly the `n_markets` picks, same order.
With `n_markets: 1` that is a single pair and a single range.

```
manage_routines(action="start", name="fly_brain", config={
  "pairs": "XYZ:DRAM-USD,XYZ:SPCX-USD,XYZ:SMSN-USD",   # n_markets entries
  "picked_ranges_bps": "10,8,14",                      # one per pair
  "total_amount_quote": 500, "leverage": 3, "portfolio_allocation": 0.2,
  "mode": "shadow", "run_name": "fly-2026-09-12"})
```

Shadow observes, decodes and reinforces from the bots' P&L but applies nothing. Note
the instance id. After ~10 observations per pair (30 ticks) `fly_status` shows
non-neutral postures.

## Step 6 — Verify

```
manage_routines(action="run", name="fly_status", config={"run_name": "fly-2026-09-12"})
manage_routines(action="run", name="fly_report", config={"run_name": "fly-2026-09-12"})
```

Report: pairs, ranges, bots running, fly tick count, first postures, any vetoes or
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
`picked_ranges_bps` and the same `run_name`. The brain keeps its memory; only the
swapped pair's baseline starts over.

## Halts

`fly_brain` stops itself and (in live) stops the bots on a halt. A **transient** halt
(repeated config-update failures) restarts with `"resume_reviewed": true` after you
have looked at the bot logs. A **financial** halt (loss stop, loss-rate breaker, no
new P&L high) cannot be cleared: report it, leave the bots stopped, and only redeploy
under a new `run_name` if the operator asks.

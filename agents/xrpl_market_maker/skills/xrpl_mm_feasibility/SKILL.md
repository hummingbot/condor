---
name: xrpl_mm_feasibility
description: Pre-build spike that determines which execution path XRPL market making
  can use — controller mode, executor mode, or neither.
when_to_use: Before the first deployment of any XRPL maker strategy, and again after
  any Hummingbot or connector upgrade. Its outcome selects the execution path.
created: '2026-07-28T00:00:00Z'
source: agent:xrpl_market_maker
---

# XRPL Market Making — Feasibility Spike

**Run this before the first deployment.** Its result decides the execution path, and the
strategy behaves differently depending on the answer. Do not guess.

## Why this exists

Market making needs requoting at machine speed. XRPL closes ledgers every 3–5 seconds and
RLUSD/XRP tracks a liquid CEX asset, so a 60-second LLM tick loop cannot place individual
quotes competitively — by the time the agent reasons, the quote is stale and taken.

The intended architecture is **controller mode**: a PMM controller requotes continuously
while the LLM only *tunes* it. That decouples quote frequency from inference frequency —
which is also what makes the strategy viable on a small account.

Whether a PMM controller drives the `xrpl` connector is a question to **test**, not to
infer. The connector polls its user stream rather than pushing fills, which is the one
genuine difference from the CEX connectors PMM controllers were built against.

## Step 1 — Can a PMM controller target `xrpl`?

```
manage_controllers(action="list")
```

**A controller's schema defaults are not its requirements.** Every market-making
controller ships perpetual-flavoured defaults (`connector_name: binance_perpetual`,
`leverage: 20`, `position_mode: HEDGE`) because that is the common case, not because
spot connectors are rejected. `connector_name` is a plain `str`. Do not conclude
"unsupported" from a `describe` output — that answers nothing about `xrpl`.

Known-settled points (verified 2026-07-30 against hummingbot-api; re-verify only after
a Hummingbot or connector upgrade):

- **`leverage` / `position_mode` are inert for spot.** `v2_with_controllers.py` applies
  both only when `is_perpetual(connector_name)` — i.e. `"perpetual" in connector`. On
  `xrpl` they are never sent. Set `leverage: 1` and ignore `position_mode`.
- **XRPL supports MARKET orders.** `/connectors/xrpl/order-types` reports
  `LIMIT, LIMIT_MAKER, MARKET, AMM_SWAP`, so the triple barrier's MARKET stop-loss and
  time-limit exits are not a blocker. Confirm with `manage_controllers(action="describe")`
  plus the order-types endpoint rather than assuming either way.
- **`pmm_simple` validates against `xrpl`.** A config with `connector_name="xrpl"` and an
  XRPL pair is accepted and yields `markets: {'xrpl': {...}}`.

Still worth checking per candidate:

- Does it need a candles feed for the connector? `pmm_dynamic` derives spreads from
  NATR/MACD candles; `pmm_simple` needs none. No XRPL candle feed means `pmm_dynamic`
  is out while `pmm_simple` is fine.
- Does it size orders against total balance? XRPL reserves make that wrong.

## Step 2 — Dry-run a controller config

**Do not skip this step.** It is the only thing that actually answers Step 1; a schema
read is evidence about defaults, not about `xrpl`. Upsert a config against `xrpl` and
inspect validation:

```
manage_controllers(action="upsert", target="config", ...)
```

A schema rejection here is a clean answer — record the exact error and move to Step 3.
An acceptance is also a clean answer: controller mode is available.

**Expect pair lookups to fail even when the controller is fine.** The API's shared
keyless data connector is built with `trading_pairs=[]`, and the XRPL connector derives
trading rules only for the pairs it was constructed with — so
`/connectors/xrpl/trading-rules` reports *every* XRPL pair as "not found", including the
connector's own default `SOLO-XRP`. That is a market-data plumbing limitation, not a
verdict on controller support, and it affects executor mode identically. Configuring XRPL
credentials for an account gives a real trading connector with pairs and sidesteps it.

## Step 3 — Confirm the executor-mode fallback

Independent of the above, verify the fallback path works:

```
manage_executors(executor_type="order_executor")
```

Confirm the schema accepts `connector_name="xrpl"` with LIMIT / LIMIT_MAKER. This path is
slower but certain, since it only needs what the connector already supports.

## Step 4 — Record the verdict

Write the outcome to the journal as a `category="execution"` learning, and set the
strategy's `bot_name` accordingly.

| Outcome | `bot_name` | Path |
|---|---|---|
| A PMM controller drives `xrpl` | set it | **Controller mode.** LLM tunes; bot requotes. Target design. |
| No controller, `order_executor` works | leave empty | **Executor mode.** LLM places LIMIT ladders each tick. Widen spreads to cover the longer requote interval. |
| Neither works | — | **Stop.** Report to the user; do not improvise with `place_order`. |

Record *what was tried and what it returned*, not a conclusion on its own. "Controller
mode unavailable" with no upsert error attached is not a verdict — it is a skipped step,
and it will be inherited as fact by every later run.

## Step 5 — If executor mode, re-check spread viability

Executor mode means a much longer requote interval, which raises the adverse-selection
floor — possibly above the AMM fee ceiling, in which case the strategy is not viable at
that tick rate at all.

```
manage_routines(action="run", name="xrpl_mm_quote_planner",
                config={"tick_interval_sec": <your actual frequency_sec>})
```

If it reports `viable: false`, the honest options are to requote faster or not run. Do not
tighten the spread below the floor to force fills — that is paying for volume with
adverse selection.

## What NOT to do

- Do not call `place_order` directly. Executors or controllers only.
- Do not conclude "it works" from a config that merely *saved*. Confirm an offer actually
  reaches the ledger and appears in the order book.
- Do not run this against mainnet with meaningful size before Step 3 passes. XRPL testnet
  is fine for mechanics, though it will not reproduce real book depth or competition.

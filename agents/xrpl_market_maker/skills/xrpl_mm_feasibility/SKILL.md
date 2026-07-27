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

**But it is unverified that a PMM controller drives the `xrpl` connector.** The connector
is LIMIT-only with a polling user stream, unlike the CEX connectors PMM controllers were
built against. Establish this first.

## Step 1 — Can a PMM controller target `xrpl`?

```
manage_controllers(action="list")
```

Inspect available market-making controllers and their config schemas. For each candidate,
check whether its connector field accepts `xrpl` and whether it requires anything the
connector cannot provide:

- Does it require MARKET orders anywhere (stop-loss, emergency close)? XRPL has none.
- Does it assume a websocket user stream for fill callbacks? XRPL polls.
- Does it assume a `_perpetual` connector or position mode? XRPL is spot CLOB.
- Does it size orders against total balance? XRPL reserves make that wrong.

## Step 2 — Dry-run a controller config

If a candidate looks viable, upsert a config against `xrpl` and inspect validation:

```
manage_controllers(action="upsert", target="config", ...)
```

A schema rejection here is a clean answer — record it and move to Step 3.

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

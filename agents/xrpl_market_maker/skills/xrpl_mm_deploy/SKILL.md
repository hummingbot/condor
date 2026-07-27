---
name: xrpl_mm_deploy
description: End-to-end playbook for standing up an XRPL CLOB maker — preflight checks,
  reserve/trustline setup, sizing, first quotes, and verification.
when_to_use: When asked to set up, deploy, or launch market making on an XRPL pair.
  Follow start to finish when running as a delegate task.
created: '2026-07-28T00:00:00Z'
source: agent:xrpl_market_maker
---

# XRPL Maker Deploy Playbook

Assumes `xrpl_mm_feasibility` has already run and the execution path is known. If it has
not, read that skill first.

## Phase 1 — Preflight

**1. Confirm the pair is worth quoting.**

```
explore_geckoterminal(action="top_pools", network="xrpl")
```

Require real depth *and* real turnover. Reserve alone is not liquidity — FUZZY/XRP holds
~$1.6M against ~$50K daily volume (≈3% turnover, effectively dead capital). RLUSD/XRP is
currently the only XRPL pair with both.

**2. Verify the issuer's transfer fee is 0%.**

A non-zero transfer fee on the issued asset applies on top of everything and can exceed
the entire spread. Check the issuer's account settings before sizing. If it is non-zero,
subtract it from the spread ceiling — or do not quote.

**3. Confirm balances and free reserve.**

```
get_portfolio_overview()
```

Needed: XRP for the base reserve (1 XRP) plus 0.2 XRP per intended offer, a trustline for
the issued asset, and inventory on both sides. **Reserved XRP is not spendable** — size
against free balance.

## Phase 2 — Spread viability

```
manage_routines(action="run", name="xrpl_mm_quote_planner",
                strategy_id="xrpl_market_maker.rlusd_xrp_maker",
                config={"xrpl_pair": "<pair>", "tick_interval_sec": <frequency_sec>,
                        "levels_per_side": <n>, "total_amount_quote": <capital>})
```

Read the `VIABILITY` block. **`viable: false` means do not deploy** — the adverse-selection
floor has met the AMM fee ceiling. Requote faster or wait for volatility to fall. Never
tighten below the floor to force fills.

Also read `divergence_vs_reference_bps`. A large gap between the XRPL book and the CEX
reference means stale data far more often than it means opportunity.

## Phase 3 — Deploy

### Controller mode (preferred, if feasibility confirmed it)

Remember there are **two config stores** and updating one does not update the other:

```
manage_controllers(action="upsert", target="config", ...)   # saved template
manage_bots(action="deploy", ...)                           # live bot
```

Declare `max_global_drawdown_quote` within the session's risk limits on every deploy.
Derive `bot_name` from the run key so restarts reattach to the same bot.

### Executor mode (fallback)

Place laddered LIMIT / LIMIT_MAKER offers, passing `controller_id` as a **top-level**
argument so PnL attributes to this agent:

```
manage_executors(action="create", controller_id="{agent_id}",
                 executor_config={"type": "order_executor", "connector_name": "xrpl", ...})
```

Start with **one level per side** and confirm it reaches the ledger before laddering.

## Phase 4 — Verify it actually worked

A clean tool response is **not** proof the offer exists on-ledger. Confirm:

```
get_market_data(data_type="order_book", connector_name="xrpl", trading_pair="<pair>")
```

Your offer should be visible at the expected price. If it is not:

- **`tecUNFUNDED_OFFER`** → sizing ignored reserved XRP. Recompute against free balance.
- **`tecNO_LINE` / `tecPATH_DRY`** → trustline missing or insufficient limit.
- **Offer accepted but invisible** → likely crossed and filled immediately. Check balances
  before re-placing, or you will double up.

## Phase 5 — Hedge decision (state it explicitly)

Unhedged XRP inventory dominates spread capture — XRP has moved 2.7% in an hour against
roughly 3 bps per fill. Decide and *tell the user which is active*:

- **Unhedged** — deliberate XRP exposure. Acceptable only if the user chose it knowingly.
- **Hedged** — neutralise net delta with a short on `bitget_perpetual`. Preferred. Check
  leg parity every tick; if one leg is missing, restoring it is the only action for that tick.

## Phase 6 — Journal

Write one action entry per tick. Record execution failures as `category="execution"`
learnings — XRPL trustline and reserve rejections recur and are cheap to avoid twice.

## Rollout

`dry_run` → `run_once` → `loop` with short `max_ticks` and conservative limits. XRPL
testnet exercises the mechanics but will not reproduce real book depth or competing
makers, so plan on small live size early rather than a long testnet phase.

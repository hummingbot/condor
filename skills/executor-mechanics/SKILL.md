---
name: executor-mechanics
description: "How Condor trading agents act through Hummingbot executors — controller_id isolation, the grid limit_price+keep_position risk model (grids have NO stop_loss), position handover, per-type config schemas, and the fee floor every take_profit must clear. Use when creating, sizing, or debugging any executor (grid, position, order), or when a create call fails on schema/validation."
compatibility: "Requires the Condor MCP server (manage_executors, manage_skill) connected"
metadata: {"condor-source": "shared", "condor-created": "2026-07-11"}
---

# Executor Mechanics

Operating rule: operate Condor only through the connected MCP tools — never
import its Python modules, edit runtime stores, or call private endpoints.

Agents act on markets ONLY through Hummingbot executors, never `place_order`
(the risk engine blocks it). Every executor you create is tagged with your
session id: pass `controller_id="<your agent id>"` as a TOP-LEVEL argument to
`manage_executors(action="create", ...)` — never inside `executor_config`.
That tag is what gives you an isolated virtual portfolio: your executors,
positions, PnL, and exposure are partitioned from every other agent on the
same account.

## Per-type schemas (companion files — pull only what you need)

```
manage_skill(action="read_file", name="executor-mechanics", file="grid_executor.md")
```

- `grid_executor.md` — range harvesting; band geometry, density math, and the
  limit_price + keep_position risk model.
- `position_executor.md` — directional position with a triple barrier
  (stop_loss / take_profit / time_limit / trailing_stop).
- `order_executor.md` — simple order with retry; the CLEANUP primitive for
  closing leftover inventory.

Schemas drift as the API evolves: on ANY create failure, re-fetch the live
schema with `manage_executors(executor_type="<type>")`, diff against what you
sent, fix, retry once, and journal the fix.

## The two risk models

- **Grids have NO stop_loss.** `limit_price` + `keep_position` is the entire
  mechanism: price crossing the limit stops the grid; `keep_position=true`
  hands the accumulated inventory over to you (visible next tick with its
  breakeven) instead of market-dumping it. Never suggest a stop_loss
  parameter on a grid.
- **Position executors carry a triple barrier** — set stop_loss/take_profit/
  time_limit explicitly; defaults leave most barriers off.

## Position handover pattern

A stopped-with-keep_position executor leaves a position tagged with your
controller_id. You never lose track of it: it appears in your executor/
position data with units + breakeven. Recycle it deliberately — typically an
order_executor close near breakeven — rather than panic-closing.

## Fee floor — every take_profit must clear round-trip fees

Rule: `take_profit > 2 × maker_fee` (entry + exit, both LIMIT_MAKER).

| Exchange | Market | Maker fee | Round-trip | Minimum TP |
|---|---|---|---|---|
| Binance | Perpetual | 0.02% | 0.04% | > 0.04% (0.08%+ safe) |
| Binance | Spot | 0.075% | 0.15% | > 0.15% (0.20%+ minimum viable) |

Spot fees are ~3.75× higher than perp fees — a TP that works on perps loses
money on spot. Always check the connector suffix (`_perpetual` vs bare)
before setting TP, and verify per-order size clears the venue minimum
notional (~$5–10 on Binance).

## Order types

Always prefer `LIMIT_MAKER` (post-only, earns maker fees) for open and TP
orders; use `LIMIT`/`MARKET` only when the venue lacks post-only or you are
deliberately crossing to close risk.

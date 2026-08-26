---
name: xrpl_mm_deploy
description: End-to-end playbook for standing up an XRPL CLOB maker — preflight,
  planner, controller deploy, verify.
when_to_use: When asked to set up, deploy, or launch market making on an XRPL pair.
  Follow start to finish when running as a delegate task.
created: '2026-07-28T00:00:00Z'
source: agent:xrpl_market_maker
---

# XRPL Maker Deploy

Default path: **controller mode** (`pmm_simple`). Fall back to executor mode only after a
real controller failure (schema reject, deploy/status error, or no on-ledger orders).

## Phase 1 — Preflight

1. **Pair quality** — `explore_geckoterminal(action="top_pools", network="xrpl")`. Need
   real depth *and* turnover. RLUSD/XRP is currently the only pair with both.
2. **Issuer transfer fee = 0%** — non-zero fees can erase the whole spread.
3. **XRPL credentials configured** — without them the keyless connector has empty trading
   rules and nothing can size. Stop and tell the user; executor mode will not rescue this.
4. **Balances** — `get_portfolio_overview()`. Need free XRP for reserves (1 + 0.2×offers),
   a trustline for the issued asset, and inventory on both sides.

## Phase 2 — Spread viability

```
manage_routines(action="run", name="xrpl_mm_quote_planner",
                strategy_id="xrpl_market_maker.rlusd_xrp_maker",
                config={"xrpl_pair": "<pair>", "tick_interval_sec": <frequency_sec>,
                        "requote_interval_sec": <executor_refresh_time>,
                        "levels_per_side": <n>, "total_amount_quote": <capital>})
```

`requote_interval_sec` is the bot's `executor_refresh_time` in controller mode — not
`frequency_sec`. Wrong interval → false `viable: false`.

If `viable: false` with the right interval → **do not deploy**. Shorten
`executor_refresh_time` or wait. Never tighten below the floor. Large
`divergence_vs_reference_bps` usually means stale data, not opportunity.

## Phase 3 — Deploy (controller first)

`pmm_simple` only. Set `leverage=1`; leave `stop_loss`/`take_profit`/`time_limit`/
`trailing_stop` `null`.

**Override these four defaults:**

| Field | Default | Set to |
|---|---|---|
| `executor_refresh_time` | `300` | `30` (from config) |
| `skip_rebalance` | `false` | `true` |
| `buy_spreads` / `sell_spreads` | `0.01,0.02` | planner `controller_spreads` (fractions) |
| `total_amount_quote` | `100` | planner `controller_total_amount_quote` (XRP on RLUSD-XRP) |

`pmm_simple` centres on XRPL mid, not the CEX reference — if divergence is routinely
wide, recommend executor mode instead of deploying.

Two stores — update both when retuning later:

```
manage_controllers(action="upsert", target="config", ...)
manage_bots(action="deploy", ...)
```

Deploy under config `bot_name` (`rlusd-xrp-maker`) — stable name so restarts reattach.
Set `max_global_drawdown_quote` in the quote asset on every deploy.

**Treat as failed → fall through to executors only when:**
- upsert rejects the config
- deploy fails / bot status errored soon after
- bot running but no on-ledger orders within a few ticks

Journal the failure as `category="execution"`. Falling back means clear `bot_name` to `''`
so PnL attributes to direct executors.

### Executor fallback

```
manage_executors(action="create", controller_id="{agent_id}",
                 executor_config={"type": "order_executor", "connector_name": "xrpl", ...})
```

One LIMIT/LIMIT_MAKER level per side first; confirm on-ledger before laddering.

## Phase 4 — Verify

```
get_order_book(connector_name="xrpl", trading_pair="<pair>")
```

Offer must be visible at the expected price.
- `tecUNFUNDED_OFFER` → sized against reserved XRP; recompute free balance
- `tecNO_LINE` / `tecPATH_DRY` → trustline missing
- Accepted but invisible → likely filled; check balances before re-placing

## Phase 5 — Hedge

State which is active: **unhedged** (deliberate XRP exposure) or **hedged**
(`bitget_perpetual` delta neutral — preferred).

## Rollout

`dry_run` → `run_once` → `loop` with short `max_ticks` and conservative limits.

`execution_mode` accepts exactly `dry_run`, `run_once`, or `loop` — nothing else.
There is no `live` value; continuous live trading is `loop`.

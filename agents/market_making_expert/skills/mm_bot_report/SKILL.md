---
name: mm_bot_report
description: 'Run the MM bot status report: running bots, open/hold-mode positions,
  closed position breakdown (TP/SL/Early/Hold), PnL, volume, and error summary.'
when_to_use: When the user asks for a bot status report, "how is the bot doing", "show
  me the report", "what's the PnL", "any errors", "how are positions", "closed positions
  breakdown", or any general health/status check on the running MM bots. Also use
  after deploying a new bot to verify it's running correctly.
created: '2026-07-02T15:46:08Z'
source: agent:market_making_expert
references_routine: mm_dashboard
---

## MM Bot Report

Run the `mm_dashboard` routine — it fetches everything in one shot:

```
manage_routines(action="run", name="mm_dashboard", config={})
```

> This skill named a `mm_bot_report` routine that does not exist (MM-8). The MM agent
> ships three routines — `mm_dashboard`, `market_analyzer` and `hip3_market_scanner` —
> and `mm_dashboard` is the one that answers "how is the bot doing".

**What it returns:**
- **Portfolio value** — inventory across the focus connector
- **Active controllers** — count, with a per-controller table
- **Open positions** — the bot's active executors, with a positions table
- **PnL** — realized, unrealized and net, per controller and in total
- **Volume** — total traded volume
- **Error summary** — error count per active bot from live logs (unless disabled)

**Config overrides** (pass as `config={}` keys) — these are `mm_dashboard.Config`'s
only fields; anything else is rejected:
- `connector_name` — focus connector for the portfolio scan (default: `binance_perpetual`, empty = all)
- `trading_pair` — filter bot positions to one pair (default: all)
- `min_value_usd` — hide inventory below this USD value (default: 1.0)
- `include_errors` — set `false` to skip the error log fetch (default: true)

**After reading the output:**
1. Surface the KPIs (portfolio value, active controllers, open positions, net PnL).
2. Flag any errors — if errors are present, note the bot name and count. For deeper log analysis run `manage_routines(action="run", name="logs_summary")` (global routine).
3. Compare open positions against the controllers running: a controller with no open
   position is quoting nothing, and one holding inventory it is not re-quoting is not
   earning spread.
4. Summarize PnL vs volume to comment on fee efficiency.

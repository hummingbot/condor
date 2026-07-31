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
references_routine: mm_bot_report
---

## MM Bot Report

Run the `mm_bot_report` routine — it fetches everything in one shot:

```
manage_routines(action="run", name="mm_bot_report", config={})
```

**What it returns:**
- **Controllers** — active controller count
- **Open positions** — active executors currently placing quotes (`is_trading=True`)
- **Hold-mode** — active executors paused/holding inventory (`is_trading=False`)
- **Recent closes breakdown** — by close type: TP | SL | Early | Hold | Trail | Expired
- **PnL & Volume** — realized + unrealized PnL per controller, total volume
- **Error summary** — error count per active bot from live logs

**Config overrides** (pass as `config={}` keys):
- `trading_pair` — filter to one pair (default: all)
- `connector_name` — filter to one connector (default: all)
- `recent_closes` — how many closed executors to analyze (default: 100)
- `include_errors` — set `false` to skip error log fetch (default: true)

**After reading the output:**
1. Surface the KPIs (open positions, hold-mode count, top close type).
2. Flag any errors — if errors are present, note the bot name and count. For deeper log analysis run `manage_routines(action="run", name="logs_summary")` (global routine).
3. If hold-mode > 0 and user hasn't set it intentionally, flag it — positions holding inventory aren't earning spread.
4. Summarize PnL vs volume to comment on fee efficiency.

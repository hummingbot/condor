---
name: routine_cookbook
description: The single reference for writing Condor routines — anatomy, the create → test → fix loop, fetching Hummingbot data, parallel calls, reports/charts, continuous loops, and candlestick charts. Routes to a companion file per topic.
when_to_use: Before implementing or debugging ANY routine. Read this first, then pull the specific companion file(s) for what your routine actually does (data, async, reports, continuous, charts).
source: chat
shared: true
---

# Routine Cookbook

Everything needed to take a task description → a working, tested routine. Read
this overview, then fetch the companion file(s) for what your routine actually
does:

```
manage_skill(action="read_file", name="routine_cookbook", file="hummingbot_client.md")
```

## Which companion file to read

| Your routine needs to…                                              | Read                    |
|---------------------------------------------------------------------|-------------------------|
| Fetch market data, candles, prices, order book, portfolio, executors| `hummingbot_client.md`  |
| Make 4+ parallel API calls / bulk fetch many pairs / rate-limit     | `async_patterns.md`     |
| Produce a report — KPIs, tables, Plotly charts, rich inline output  | `report_builder.md`     |
| Run a continuous loop (monitor, tracker, alerts) until stopped      | `continuous.md`         |
| Render a candlestick chart, indicator overlay, or volume footprint  | `candles_chart.md`      |

Most routines need `report_builder.md` plus one or two others. A continuous
price monitor with a live dashboard, for example, reads `hummingbot_client.md`
+ `continuous.md`.

## Where the routine lives

**Agent-local** — `agents/{slug}/routines/` — visible only to that agent. This is
the default when you *are* an agent: your own routines are yours, and you create
them with no `strategy_id` (your slug is already the scope).

**Global** — `routines/` — visible to every user and agent. Use it only for
general-purpose analysis/monitoring not tied to one agent. From the chat, target
an agent's local dir explicitly with `strategy_id="agent_slug.strategy_slug"`
(or a bare agent slug).

If the scope is ambiguous, clarify it before writing code.

## Basic routine anatomy

```python
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client
import logging

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"  # Market Data | Analysis | Arbitrage | Monitoring | Bot Analysis

class Config(BaseModel):
    """One-line description shown in UI."""
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    connector_name: str = Field(default="binance_perpetual", description="Exchange")

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"
    # ... work ...
    return "result string"
```

Must export: `Config` (Pydantic BaseModel) and `async def run(config, context) -> str`.
The `Config` docstring is the UI description. `CATEGORY` groups it in the catalog.

## The loop: create → test → fix

1. **Understand** — what to analyze, monitor or compute; agent-local or global?
2. **Check existing** — `manage_routines(action="list")` to avoid duplicates.
3. **Read** — this overview + the companion file(s) for what you are building.
4. **Create** — `manage_routines(action="create_routine", name="snake_case", code="...")`
5. **Test** — `manage_routines(action="run", name="snake_case", config={})`
6. **Iterate** — read the error, fix, re-run until the output is clean.

Never report a routine as done before step 5 comes back clean.

## `manage_routines` action reference

```python
manage_routines(action="list")
manage_routines(action="create_routine", name="x", code="...")
manage_routines(action="read_routine", name="x")
manage_routines(action="edit_routine", name="x", code="...")
manage_routines(action="delete_routine", name="x")
manage_routines(action="run", name="x", config={})       # one-shot
manage_routines(action="start", name="x", config={})     # continuous
manage_routines(action="stop", name="instance_id")       # stop continuous
manage_routines(action="list_instances")                 # list running

# From the chat, target an agent's local library by adding strategy_id
manage_routines(action="create_routine", name="x", code="...", strategy_id="slug.strategy")
manage_routines(action="run", name="x", strategy_id="slug.strategy", config={})
```

## Non-negotiables (apply to every routine)

- **Every routine MUST generate a ReportBuilder report** — see `report_builder.md`.
- All client calls are **async** — always `await`; never `time.sleep`, only `asyncio.sleep`.
- **Parse defensively**: handle `None`/missing keys, return error strings, never raise to the caller.
- One routine per task. Lead with code, be direct.
- Test after writing (`manage_routines(action="run", ...)`) and fix until the output is clean.

These are starting patterns, not a bypass — running a routine still goes through
the normal execution/confirmation controls.

---
name: routine_builder
description: Create, edit, and debug Python analysis/monitoring routines in routines/
when_to_use: The user wants to create, modify, fix, or debug a routine (market analysis, monitoring, charts, reports).
created: 2026-06-18
source: builtin
---

You are building a Python **routine** — an executable script in `routines/`. Drive
it via `manage_routines` (CRUD: list, read, create_routine, edit_routine, delete).
Do NOT explore source code; use the MCP tools directly.

A *skill* is know-how (when + steps); a *routine* is the executable script. A skill
can reference a routine via `references_routine` to bridge know-how → execution.

## Workflow
1. **Understand** what to analyze or monitor.
2. **Check existing** routines with `manage_routines(action="list")` to avoid duplicates.
3. **Create** with `manage_routines(action="create_routine", name="snake_case", code="...")`.
4. **Test** by running it: `manage_routines(action="run", name="routine_name", config={})`.
5. **Iterate** — read errors, fix, re-test until it works.

## Routine Anatomy
```python
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

CATEGORY = "Market Data"  # Market Data | Analysis | Arbitrage | Monitoring | Bot Analysis

class Config(BaseModel):
    """One-line description shown in UI."""
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    connector_name: str = Field(default="binance_perpetual", description="Exchange")

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"
    # ... do work ...
    return "result string"
```
**Must export:** `Config` (Pydantic BaseModel) and `async def run(config, context) -> str`.
The `Config` docstring is the UI description; `CATEGORY` groups it in the catalog.

## Execution Contexts
Routines run in 3 contexts; `context.bot` is **always available** — never `None`:

| Context | `context.bot` | `context._chat_id` | Trigger |
|---------|---------------|---------------------|---------|
| Telegram | Real bot | User's chat ID | `/routines` |
| Web Dashboard | `_HttpBot` (HTTP fallback) | User ID or 0 | Web API |
| MCP | `_HttpBot` (HTTP fallback) | `settings.chat_id` or 0 | `manage_routines` |

In non-Telegram contexts `_HttpBot` (`condor/routine_store.py`) uses the Telegram
HTTP API via `TELEGRAM_TOKEN`. It supports `send_message`, `send_photo`,
`send_document`, `edit_message_text`. **Always use keyword args:**
```python
await context.bot.send_message(chat_id=chat_id, text="Hello", parse_mode="MarkdownV2")
await context.bot.send_photo(chat_id=chat_id, photo=buf, caption="Chart")
```

## Continuous Routines
Set `CONTINUOUS = True` for routines with internal loops (run as asyncio tasks).
- Always catch `asyncio.CancelledError` at the outer `try` (without it, stop fails silently).
- Re-raise `CancelledError`; catch+log other inner exceptions, don't re-raise.
- Use `asyncio.sleep`, never `time.sleep`. Return a summary string when cancelled.

## Rich Output (RoutineResult)
```python
from routines.base import RoutineResult
return RoutineResult(text="Summary", table_data=[{"Pair": "BTC-USDT", "Price": 100000}], table_columns=["Pair", "Price"])
return RoutineResult(text=summary, chart_image=png_bytes)
return RoutineResult(text=summary, sections=[{"type": "kpi", "label": "Price", "value": "$100K", "delta": "+5%", "trend": "up"}])
```

## Mandatory Report Generation
**Every routine MUST generate a ReportBuilder report** — the inline result is
ephemeral; the report is the persistent record. Add at the end of `run()`:
```python
try:
    from condor.reports import ReportBuilder
    builder = ReportBuilder("Report Title")
    builder.source("routine", "routine_name").tags(["relevant", "tags"])
    builder.kpi("Key Metric", value)        # optional
    builder.table(table_data, columns)       # if tabular
    builder.plotly(fig)                      # if a chart
    builder.markdown(summary_text)           # text summary
    builder.manual_order()
    builder.save()
except Exception as e:
    logger.warning(f"Report generation failed: {e}")
```
**Only these ReportBuilder methods exist:** `source`, `tags`, `kpi`, `markdown`,
`table`, `plotly`, `manual_order`, `save`. Always wrap in try/except and pass the
real routine filename to `builder.source("routine", ...)`.

## Live Reports for Continuous Routines
```python
from condor.reports import LiveReport
report = LiveReport("Monitor Title", source_name="routine_name", tags=["live"])
# each tick:
report.clear(); report.builder.manual_order()
report.builder.kpi("Price", f"${price:,.2f}"); report.builder.table(history[-50:])
report.update()  # creates on first call, updates thereafter
```
**LiveReport API:** `clear()`, `update()`, `report_id`, `builder`. Methods that DO
NOT exist: `heading()`, `text()`, `section()`, `html()` — use `markdown(...)`.

## Hummingbot Client API
```python
client = await get_client(context._chat_id, context=context)
await client.market_data.get_candles(connector, pair, interval="1m", max_records=100)
await client.market_data.get_order_book(connector, pair, depth=10)
await client.market_data.get_prices(connector, trading_pairs)
await client.market_data.get_funding_info(connector, pair)
await client.portfolio.get_state(); await client.portfolio.get_total_value()  # float
await client.executors.search_executors(controller_ids=[], status="active", limit=50)
await client.executors.get_performance_report(controller_id=cid)  # NOT executor_id
```
Parse candles defensively: `records = result if isinstance(result, list) else result.get("data", result.get("candles", []))`.

## Plotly Rules
Every figure must place the legend at the bottom:
```python
fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5))
```

## Common Mistakes
- `get_order_book()` NOT `get_order_book_snapshot`
- `get_candles(connector, pair, interval, max_records)` NOT `limit`
- `get_performance_report(controller_id=...)` NOT `executor_id`
- `create_executor(config_dict)` — plain dict, NOT a Pydantic model
- `builder.kpi(label, value)` — individual args, NOT a list of dicts
- All client methods are async — always `await`. Handle missing data gracefully
  (return error strings, don't raise). Use `asyncio.gather` + `Semaphore(10)` for bulk.

## Rules
- Be direct and concise. Lead with code, not explanations.
- Always test routines after creating them — run and show output. Fix errors immediately.
- One routine per task. Do NOT explore source code — use MCP tools only.
- Every routine must generate a ReportBuilder report — no exceptions.

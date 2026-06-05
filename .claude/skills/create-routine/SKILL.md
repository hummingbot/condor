---
name: create-routine
description: Create or edit Python routines for market analysis, monitoring, and data visualization. Use when the user asks to create, modify, or fix a routine in the routines/ folder.
---

# Create / Edit Routine

You are working on a routine for Condor — a Python script auto-discovered from `routines/`. Routines run via Telegram (`/routines`) or the web dashboard.

> **Not agent routines.** Agent routines live inside trading agent strategies and are created via `/trading-agent-builder`.

## Minimal Routine

```python
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

CATEGORY = "Market Data"  # Market Data | Analysis | Arbitrage | Monitoring | Bot Analysis

class Config(BaseModel):
    """One-line description shown in UI."""
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    connector_name: str = Field(default="binance_perpetual", description="Exchange connector")

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"
    # ... do work ...
    return "result string"
```

## Key Rules

- File goes in `routines/` as `snake_case.py`
- Must export `Config` (Pydantic BaseModel) and `async def run(config, context) -> str`
- `Config.__doc__` = routine description in UI
- `CATEGORY` at module level groups it in the catalog
- Return a string, or `RoutineResult` for rich output
- `get_client()` is optional — routines can use external APIs directly (aiohttp, etc.)
- Use `asyncio.gather` for parallel fetches
- Handle missing data gracefully — return error strings, don't raise

## Rich Output

```python
from routines.base import RoutineResult

# Tables in web dashboard
return RoutineResult(
    text="Summary for Telegram",
    table_data=[{"Pair": "BTC-USDT", "Price": 100000}],
    table_columns=["Pair", "Price"],
)

# Chart image sent to Telegram
return RoutineResult(text=summary, chart_image=png_bytes)

# KPI cards in web dashboard
return RoutineResult(text=summary, sections=[
    {"type": "kpi", "label": "Price", "value": "$100K", "delta": "+5%", "trend": "up"},
])
```

## ReportBuilder (HTML Reports)

Always lazy-import inside try/except:

```python
try:
    from condor.reports import ReportBuilder
    builder = ReportBuilder("Report Title")
    builder.source("routine", "routine_name").tags(["tag1", "tag2"])
    builder.kpi("Price", "$100K", delta="+5%", trend="up")  # individual calls, NOT a list
    builder.markdown("## Analysis\nSome text")                # use markdown() for all text/headings
    builder.table([{"Col": "val"}])                           # columns auto-detected from first row
    builder.plotly(fig)                                        # Plotly figure object
    builder.manual_order()                                     # preserve insertion order (default: kpi→plotly→table→markdown)
    builder.save()
except Exception as e:
    logger.warning(f"Report generation failed: {e}")
```

**Only these methods exist:** `source`, `tags`, `kpi`, `markdown`, `table`, `plotly`, `manual_order`, `save`. No `heading()`, `text()`, `section()`, or `html()`.

### Live Reports for Continuous Routines

Use `LiveReport` for continuous routines that produce a living report updated each tick:

```python
from condor.reports import LiveReport

report = LiveReport("Monitor Title", source_name="routine_name", tags=["live"])
history = []

try:
    while True:
        # ... fetch data ...
        history.append({"Time": now, "Price": price})

        report.clear()  # reset builder for fresh render
        report.builder.manual_order()
        report.builder.kpi("Price", f"${price:,.2f}")
        report.builder.table(history[-50:])
        report.update()  # creates on first call, updates thereafter

        await asyncio.sleep(interval)
except asyncio.CancelledError:
    return "Stopped"
```

**LiveReport API:** `clear()`, `update()`, `report_id` (property), `builder` (property — the underlying `ReportBuilder`)

## Execution Contexts

Routines run in **3 different contexts** — your code must work in all of them:

| Context | `context.bot` | `context._chat_id` | Trigger |
|---------|---------------|---------------------|---------|
| **Telegram** | Real bot (python-telegram-bot) | User's chat ID | `/routines` command |
| **Web Dashboard** | `_HttpBot` (HTTP fallback) | User ID or 0 | Web API |
| **MCP** | `_HttpBot` (HTTP fallback) | `settings.chat_id` or 0 | `manage_routines` tool |

**Key point:** `context.bot` is **always available** — never `None`. In non-Telegram contexts, it's an `_HttpBot` that sends messages via the Telegram HTTP API using `TELEGRAM_TOKEN`. You can always call `context.bot.send_message(...)` safely.

### What `_HttpBot` supports
- `send_message(chat_id=..., text=..., parse_mode=...)`
- `send_photo(chat_id=..., photo=..., caption=...)`
- `send_document(chat_id=..., document=..., caption=...)`
- `edit_message_text(chat_id=..., message_id=..., text=...)`

If `TELEGRAM_TOKEN` is not set, calls are silently ignored (no crash).

## Continuous Routines

Set `CONTINUOUS = True` for routines with internal loops. These run as asyncio tasks until cancelled.

```python
import asyncio
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

CONTINUOUS = True

class Config(BaseModel):
    """Live price monitor with alerts."""
    connector: str = Field(default="binance", description="Exchange connector")
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    threshold_pct: float = Field(default=1.0, description="Alert threshold %")
    interval_sec: int = Field(default=10, description="Check interval in seconds")

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    # Send start notification (works in all contexts)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Started monitoring {config.trading_pair}",
    )

    last_price = None
    try:
        while True:
            prices = await client.market_data.get_prices(
                connector_name=config.connector,
                trading_pairs=config.trading_pair,
            )
            current = prices["prices"].get(config.trading_pair)

            if current and last_price:
                change = abs((current - last_price) / last_price) * 100
                if change >= config.threshold_pct:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"Alert: {config.trading_pair} moved {change:.2f}%",
                    )
            last_price = current or last_price
            await asyncio.sleep(config.interval_sec)

    except asyncio.CancelledError:
        return "Stopped"
```

### Continuous routine rules:
- Always catch `asyncio.CancelledError` at the outer loop — re-raise or return
- Use `context.bot.send_message()` for real-time notifications (works in all contexts)
- Inner loop exceptions should be caught and logged, NOT re-raised
- Return a summary string when cancelled

## Sending Charts to Telegram

```python
buf = io.BytesIO()
fig.savefig(buf, format="png", dpi=150)  # matplotlib
# OR: fig.write_image(buf, format="png", scale=2)  # plotly
buf.seek(0)

# Works in all contexts (Telegram, Web, MCP)
await context.bot.send_photo(chat_id=context._chat_id, photo=buf, caption="Title")

# Also return as RoutineResult for web dashboard
return RoutineResult(text=summary, chart_image=buf.getvalue())
```

## Hummingbot Client API

```python
client = await get_client(context._chat_id, context=context)

# Market data
await client.market_data.get_candles(connector, pair, interval="1m", max_records=100)
await client.market_data.get_order_book(connector, pair, depth=10)
await client.market_data.get_prices(connector, trading_pairs)           # str or list
await client.market_data.get_funding_info(connector, pair)
await client.market_data.get_price_for_volume(connector, pair, volume, is_buy)
await client.market_data.get_historical_candles(connector, pair, interval, start_time, end_time)
await client.market_data.get_candles_last_days(connector, pair, days, interval="1h")

# Portfolio
await client.portfolio.get_state(account_names=None, connector_names=None)
await client.portfolio.get_total_value()  # returns float
await client.portfolio.get_distribution()
await client.portfolio.get_history(limit=100, interval=None)

# Executors
await client.executors.search_executors(controller_ids=[], status="active", limit=50)
await client.executors.get_performance_report(controller_id=cid)  # NOT executor_id
await client.executors.create_executor(executor_config_dict)
```

### Parsing responses

```python
# Candles — handle both formats
result = await client.market_data.get_candles(connector, pair, interval="1m", max_records=100)
records = result if isinstance(result, list) else result.get("data", result.get("candles", []))

# Order book
ob = await client.market_data.get_order_book(connector, pair, depth=10)
bids, asks = ob.get("bids", []), ob.get("asks", [])  # [[price, size], ...]

# Bounded concurrency for bulk fetches
sem = asyncio.Semaphore(10)
async def fetch(p):
    async with sem:
        return await client.market_data.get_candles(connector, p, interval="1m", max_records=100)
results = await asyncio.gather(*[fetch(p) for p in pairs], return_exceptions=True)
```

## Plotly Chart Rules

- **Legend always at the bottom:** Every Plotly figure must set `fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5))` so the legend appears horizontally below the chart, never on top or to the side.

## Common Mistakes

- `get_order_book()` NOT ~~`get_order_book_snapshot`~~
- `get_candles(connector, pair, interval, max_records)` NOT ~~`get_candles(pair, interval, limit)`~~
- `get_performance_report(controller_id=...)` NOT ~~`get_performance_report(executor_id=...)`~~
- `create_executor(config_dict)` — plain dict, NOT Pydantic model
- `builder.kpi(label, value)` — individual args, NOT a list of dicts
- All client methods are async — always `await`
- `get_total_value()` returns `float`, all others return `dict`

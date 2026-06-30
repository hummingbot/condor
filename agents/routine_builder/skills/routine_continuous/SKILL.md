---
name: routine_continuous
description: 'Continuous routine patterns: CONTINUOUS flag, CancelledError handling,
  LiveReport for live dashboards'
when_to_use: When building a monitoring or continuous routine with an internal loop
  (price monitor, portfolio tracker, alert system) that runs until explicitly stopped.
created: '2026-06-30T08:54:30Z'
source: chat
---

## Basic Continuous Routine

```python
import asyncio
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client
import logging

logger = logging.getLogger(__name__)

CONTINUOUS = True  # Required — marks this as a continuous routine

class Config(BaseModel):
    """Monitor description shown in UI."""
    trading_pair: str = Field(default="BTC-USDT", description="Pair to monitor")
    interval_sec: int = Field(default=30, description="Check interval in seconds")

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id
    await context.bot.send_message(chat_id=chat_id, text="Monitor started ✅")

    try:
        while True:
            try:
                client = await get_client(chat_id, context=context)
                if not client:
                    await asyncio.sleep(config.interval_sec)
                    continue

                # ... do work ...
                await context.bot.send_message(chat_id=chat_id, text="Update...")

            except asyncio.CancelledError:
                raise  # Always re-raise — this is the stop signal
            except Exception as e:
                logger.warning(f"Tick error: {e}")  # Log, don't re-raise — keep running

            await asyncio.sleep(config.interval_sec)

    except asyncio.CancelledError:
        return "Monitor stopped"
```

## LiveReport (single updating dashboard per run)

Use instead of spamming `send_message` when you want one live-updating report:

```python
from condor.reports import LiveReport

report = LiveReport("Monitor Title", source_name="routine_name", tags=["live", "monitoring"])

try:
    while True:
        try:
            # ... fetch data ...

            # Rebuild report each tick
            report.clear()
            report.builder.manual_order()
            report.builder.kpi("Price", f"${price:,.2f}")
            report.builder.kpi("24h Change", f"{change:+.2f}%")
            report.builder.table(history[-50:], ["Time", "Price", "Volume"])
            report.builder.markdown(f"_Last update: {timestamp}_")
            report.update()  # Creates on first call, updates in-place thereafter

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Tick error: {e}")

        await asyncio.sleep(config.interval_sec)

except asyncio.CancelledError:
    return "Stopped"
```

### LiveReport API (only these exist)
- `clear()` — reset builder for next tick
- `update()` — save/update the report (sync)
- `report.builder` — ReportBuilder instance (use `.kpi()`, `.table()`, `.markdown()`, `.plotly()`, `.manual_order()`)
- `report.report_id` — ID of the live report (available after first `update()`)

Methods that DO NOT exist: `heading()`, `text()`, `section()`, `html()` — use `markdown()`.

## When to use LiveReport vs send_message

| | LiveReport | send_message |
|---|---|---|
| **Use when** | Live dashboard, single updating view | Alert system, event-based, each tick is a new message |
| **Output** | One report updated in-place | New Telegram message each tick |
| **Good for** | Price monitors, portfolio trackers | Threshold alerts, trade signals |

## MCP Lifecycle

```python
manage_routines(action="start", name="my_monitor", config={"interval_sec": 60})
manage_routines(action="list_instances")   # find instance_id
manage_routines(action="stop", name="<instance_id>")
```

## Rules

- `CONTINUOUS = True` must be at module level
- Always catch `CancelledError` at the outermost `try` and **re-raise** it — this is how stop works
- Catch all other exceptions **inside** the loop — one bad tick should never kill the monitor
- Never use `time.sleep` — always `asyncio.sleep`
- Prefer `LiveReport` over spamming `send_message` for monitoring routines
- Return a summary string from `run()` when cancelled (e.g. `"Stopped after 42 ticks"`)

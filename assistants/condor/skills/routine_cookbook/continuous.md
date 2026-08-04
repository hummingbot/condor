# Continuous Routines

Patterns for monitoring/continuous routines with an internal loop that runs
until explicitly stopped.

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

report = LiveReport(
    "Monitor Title",
    source_name="routine_name",
    tags=["live", "monitoring"],
    auto_refresh_seconds=30,
)

try:
    while True:
        try:
            # ... fetch data ...

            # Rebuild report each tick
            report.clear()
            report.builder.manual_order()
            report.builder.section("01 / LIVE STATUS", "Current monitor state")
            report.builder.kpi("Price", f"${price:,.2f}")
            report.builder.kpi("24h Change", f"{change:+.2f}%")
            report.builder.table(history[-50:], ["Time", "Price", "Volume"])
            report.builder.markdown(f"_Last update: {timestamp}_")
            await report.update()  # Creates on first call, updates in-place thereafter

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Tick error: {e}")

        await asyncio.sleep(config.interval_sec)

except asyncio.CancelledError:
    if report.report_id is not None:
        report.clear()
        report.builder.auto_refresh(None)
        report.builder.section("MONITOR STOPPED", "Final fixed snapshot")
        report.builder.table(history[-50:], ["Time", "Price", "Volume"])
        await report.update()
    return "Stopped"
```

### LiveReport API (only these exist)
- `clear()` — reset builder for next tick
- `update()` — async save/update of the report
- `report.builder` — ReportBuilder instance (use `.section()`, `.kpi()`, `.table()`, `.markdown()`, `.plotly()`, `.manual_order()`)
- `report.report_id` — ID of the live report (available after first `update()`)
- `auto_refresh_seconds` — optional browser reload interval preserved across `clear()`

Auto-refresh reports show Live and Pause/Resume controls. User filtering, chart
selection, table search, sorting, or paging pauses refresh automatically. The
routine continues updating the file while the open viewer is paused; Resume loads
the latest version. On cancellation, rebuild once with
`report.builder.auto_refresh(None)` and call `update()` so the retained report is
clearly stopped instead of refreshing forever. Downloaded HTML remains a fixed
offline snapshot.

Methods that DO NOT exist: `heading()`, `text()`, `html()`. Use `section()` for
major boundaries and `markdown()` for narrative or subsection headings.

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

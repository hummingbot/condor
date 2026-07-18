---
name: routine-builder
description: "The single reference for writing Condor routines — public-REST data fetching, parallel calls, reports/charts, and candlestick charts. Routes to a companion file per topic. Use before implementing or debugging ANY routine. Read this first, then pull the specific companion file(s) for what your routine actually does (async, reports, charts). Routines are read-only and one-shot; repetition comes from cron schedules."
metadata: {"condor-source": "shared", "condor-created": "2026-07-18"}
---

# Routine Builder

The patterns for building routines, split into **companion files** so you load
only what your task needs. Read this overview, then fetch the relevant file(s):

```
manage_skill(action="read_file", name="routine-builder", file="report_builder.md")
```

## Which companion file to read

| Your routine needs to…                                              | Read                    |
|---------------------------------------------------------------------|-------------------------|
| Make 4+ parallel API calls / bulk fetch many pairs / rate-limit     | `async_patterns.md`     |
| Produce a report — KPIs, tables, Plotly charts, rich inline output  | `report_builder.md`     |
| Render a candlestick chart, indicator overlay, or volume footprint  | `candles_chart.md`      |

Most routines need `report_builder.md` plus one or two others. Data comes
from public REST endpoints (see the shipped market_scanner/ta_chart/arb_check
routines for working fetch patterns); monitoring is a one-shot check plus a
cron schedule, never an internal loop.

## Non-negotiables (apply to every routine)

- **Every routine MUST generate a ReportBuilder report** — see `report_builder.md`.
- All client calls are **async** — always `await`; never `time.sleep`, only `asyncio.sleep`.
- **Parse defensively**: handle `None`/missing keys, return error strings, never raise to the caller.
- Test after writing (`manage_routines(action="run", ...)`) and fix until the output is clean.

These are starting patterns, not a bypass — running a routine still goes through
the normal execution/confirmation controls.

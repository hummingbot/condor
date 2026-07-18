---
name: routine-builder
description: "The single reference for writing Condor routines — public-REST data fetching, parallel calls, reports/charts, and candlestick charts. Routes to a companion file per topic. Use before implementing or debugging ANY routine. Read this first, then pull the specific companion file(s) for what your routine actually does (async, reports, charts). Routines are read-only and one-shot; repetition comes from cron schedules."
compatibility: "Requires the Condor MCP server (manage_routines, manage_skill) connected"
metadata: {"condor-source": "shared", "condor-created": "2026-07-18", "condor-updated-by": "2026-07-18 chat", "condor-changelog": "[2026-07-18 chat] Identity/endpoint rule: wallet from accounts store, RPC from CONDOR_SOLANA_RPC, worker env scrub documented — learned building wallet_rebalancer (address + public RPC had been hardcoded) | [2026-07-18 chat] Worker env is now full process env + repo .env (allowlist removed by operator decision) — update the identity/endpoint rule accordingly"}
---

# Routine Builder

Operating rule: author and run routines only through the connected MCP tools
(`manage_routines`, `manage_skill`) — never by editing Condor's files directly.

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

- **Never hardcode identity or endpoints.** A wallet address belongs to the
  accounts store — default it dynamically, never bake an address into source:
  `wallet: str = Field(default="")` and when empty resolve
  `from condor.executors.wallets import account_store;
  account_store().resolve("<venue>", None).custody_address`.
  The Solana RPC endpoint is `os.environ["CONDOR_SOLANA_RPC"]` (the same
  variable `condor accounts add solana` consumes) with the venue's public
  endpoint only when unset — and NEVER echo the URL (or any env value that
  may embed a key) in output/reports; report a source label instead. The
  routine worker's environment is the full operator environment (process env
  + repo `.env`), so anything the user configured is available via
  `os.environ` — read it, never copy its values into routine source.
- **Every routine MUST generate a ReportBuilder report** — see `report_builder.md`.
- All client calls are **async** — always `await`; never `time.sleep`, only `asyncio.sleep`.
- **Parse defensively**: handle `None`/missing keys, return error strings, never raise to the caller.
- Test after writing (`manage_routines(action="run", ...)`) and fix until the output is clean.

These are starting patterns, not a bypass — running a routine still goes through
the normal execution/confirmation controls.

# Agentic Trading Framework

A framework for building **autonomous LLM-driven trading agents** that operate on top of Hummingbot. Each agent is an isolated, file-backed entity that runs on a fixed tick interval, reasons about the market with an LLM, and translates its decisions into real trades through Hummingbot **executors**.

---

## 1. Motivation

LLMs are powerful reasoners but terrible at the mechanical parts of trading: holding state across restarts, computing position breakevens, tracking volume and PnL, enforcing risk limits, and isolating one strategy's capital from another's. At the same time, hand-coded strategies are rigid: they cannot adapt to news, regimes, or qualitative context.

This framework splits the problem in two:

- **Deterministic layer (Python routines, providers, executors).** Pulls market data, computes indicators, fetches positions, runs the order lifecycle. Code is cheap and reliable — we use it everywhere we can so the LLM does as little arithmetic as possible.
- **Reasoning layer (LLM tick).** Looks at the pre-computed snapshot, reads its own journal and learnings, and decides *what to do next*: spawn a new executor, stop one, take profit, scale into a position. The LLM does not place individual orders — it manipulates executors.

The result is an agent that *thinks* like a discretionary trader but *acts* like a systematic one, with full auditability across every tick.

### Why executors are the heart of the design

The single most important architectural decision is that **agents only ever act through Hummingbot executors**. Each agent spawns its executors with its own `controller_id == agent_id`, which gives us three properties for free:

1. **Isolation.** Two agents running on the same account never see or touch each other's executors. Capital, PnL, and exposure are partitioned by `controller_id`.
2. **A virtual portfolio.** Each agent gets what is effectively its own sub-account: its own positions, breakeven prices, realized/unrealized PnL, and volume traded — without needing a real sub-account on the exchange.
3. **Position handover after an executor closes.** This is the key trick. When an executor (e.g. a long grid for $500) hits its stop condition, you can configure it to *keep the inventory* instead of dumping it at a loss. The agent now holds a **position** (units + breakeven) tracked by Hummingbot's positions API, still tagged with its `controller_id`. On the next tick the agent sees this leftover position alongside its active executors, can reason about it ("price is recovering, breakeven is at X, current is X+1%"), and can spawn a fresh **OrderExecutor** to scale it down, hedge it, or flip the side. The agent never loses track of inventory it created, and the human user never has to babysit the cleanup.

In short: executors give us the cleanest possible boundary between "what this agent is doing right now" and "what every other agent / human / bot is doing on the same account."

---

## 2. Architecture

```
                   ┌──────────────────────────── Trading Agent ───────────────────────────┐
                   │                                                                       │
                   │   Strategy (agent.md)        Journal (per session)     Learnings.md   │
                   │   ───────────────────        ────────────────────       ────────────  │
                   │   - system prompt            - summary                  cross-session │
                   │   - default config           - decisions                lessons       │
                   │   - skills/routines          - tick-by-tick log                       │
                   │                              - snapshot_N.md                          │
                   │                                                                       │
                   │                            ┌──── TickEngine ────┐                     │
                   │                            │  every N seconds:  │                     │
                   │   Routines  ───────────►   │  1. run providers  │  ────► MCP Tools    │
                   │  (deterministic            │  2. read journal   │       - candles     │
                   │   data prep)               │  3. build prompt   │       - orderbook   │
                   │                            │  4. ACP session    │       - geckoterm.  │
                   │                            │  5. capture tools  │       - executors   │
                   │                            │  6. write snapshot │       - notify      │
                   │                            └────────────────────┘                     │
                   │                                                                       │
                   └───────────────────────────────────────────────────────────────────────┘
                                                       │
                                                       ▼
                                          Hummingbot Executors
                                  (filtered by controller_id == agent_id)
```

### Components in `condor/trading_agent/`

| File | Role |
|---|---|
| `engine.py` | `TickEngine` — one instance per running agent. Runs the tick loop, builds prompts, drives ACP sessions, captures tool calls, persists snapshots. |
| `strategy.py` | `Strategy` + `StrategyStore`. Strategies live as `agent.md` files (YAML frontmatter + markdown body) under `trading_agents/{slug}/`. |
| `journal.py` | `JournalManager` — compact, human-readable per-session memory: summary, decisions, ticks, snapshots, executors. Also `learnings.md` cross-session. |
| `prompts.py` | Builds the per-tick prompt: system prompt + strategy + provider summaries + journal context + risk state + user directives. |
| `risk.py` | `RiskEngine` + `RiskLimits`. Hard guardrails (max exposure, max drawdown, max open executors) and the permission callback that auto-approves tool calls only if they pass the risk check. |
| `providers/` | Deterministic pre-tick data fetchers. Two core providers ship: `executors.py` (filtered by `controller_id`) and `positions.py` (positions summary, also filtered by `controller_id`). |
| `config.py` | `AgentConfig` schema persisted per session. |

### File layout per agent

```
trading_agents/
  river_scalper/
    agent.md                  # strategy definition (frontmatter + LLM instructions)
    learnings.md              # cross-session lessons the agent writes itself
    routines/                 # deterministic Python helpers (e.g. process_candles.py)
    sessions/
      session_1/
        config.yml            # frozen runtime config for this session
        journal.md            # summary, decisions, ticks, snapshots index
        snapshots/
          snapshot_1.md       # full prompt + response + tool calls for tick 1
          snapshot_2.md
          ...
    dry_runs/
      experiment_1.md         # one-shot dry-run / run-once snapshots
```

### Tick loop (`TickEngine._tick`)

1. **Resolve API client** for the configured server.
2. **Run core providers** — currently `executors` and `positions`, both filtered by `controller_id == agent_id`. Their structured `data` is kept for tracking; their `summary` strings go into the prompt.
3. **Read journal context** — `learnings.md`, `summary`, and the last 3 decisions.
4. **Get risk state** from `RiskEngine` (exposure, drawdown, open count). If the agent is blocked, log it and return without invoking the LLM.
5. **Build the prompt** via `build_tick_prompt(...)`, optionally appending any user directives queued via `inject_directive()`.
6. **Spawn an ACP session** for the strategy's `agent_key` (claude-code, gemini, …) with MCP servers wired in (Hummingbot tools, market data, notifications, journal writes). Stream events, capture text and tool calls.
7. **Persist** — for sessions, write `snapshot_N.md`, append a tick to the journal, and update the running summary. For dry-runs / run-once, write a flat `experiment_N.md`.

### Run modes

| Mode | Behavior |
|---|---|
| `dry_run` | One tick, no trading capability granted to MCP. Pure reasoning test. Saves a single experiment snapshot. |
| `run_once` | One tick *with* trading. Useful for manual single-shot execution. |
| `loop` | Standard mode. Ticks every `frequency_sec` until stopped or `max_ticks` reached. Creates a session folder with full journal. |

### Risk + permissions

`auto_approve_with_risk_check(...)` is wired in as the ACP permission callback. Every tool call the LLM tries to make is intercepted: trading-side tools are checked against `RiskLimits` (max exposure, max drawdown, max open executors); read-only tools are auto-approved. The agent literally cannot exceed its limits — the framework refuses on its behalf.

---

## 3. The executor / position-hold pattern in detail

This is worth its own section because it is the hardest thing to internalize and the main reason the framework works.

**Scenario.** An agent is configured with a "long grid v-bottom" strategy. It spawns a `GridExecutor` for $500 of BTC-USDT with `stop_loss_keep_position = true`.

1. The grid runs, fills several buy levels, accumulates ~0.005 BTC.
2. Price keeps dropping; the grid hits its stop-loss price.
3. Because of `keep_position`, the executor closes itself but **does not sell the BTC**. The 0.005 BTC stays in the account, tagged with the agent's `controller_id`, with a known average entry / breakeven.
4. On the next tick the `positions` provider returns: *agent X holds 0.005 BTC, breakeven $63,200, current $61,800, unrealized PnL -$7.00*.
5. The LLM sees this in its prompt. The strategy might say *"if we are stuck in a position, wait for a bounce within 1% of breakeven and exit with an OrderExecutor"*.
6. Price recovers to $62,900. The agent spawns an `OrderExecutor` (sell 0.005 BTC at market or limit), the position closes, the round-trip realizes its (smaller) loss or break-even.

Throughout this flow:

- The agent **never** loses track of the inventory it created, because every executor and every leftover position is tagged with its `controller_id`.
- A second agent on the same account, running a totally different strategy on the same pair, sees **none** of this. Its providers filter on its own `controller_id`.
- The user can read `journal.md` and `snapshot_N.md` to see exactly *why* the agent decided to hold, scale, or exit.

This is the closest thing to giving each LLM its own virtual sub-account without actually opening one.

---

## 4. Usage

### 4.1 Create a strategy

A strategy is just `trading_agents/{slug}/agent.md`:

```markdown
---
id: a1b2c3d4e5f6
name: River Scalper
description: VWAP-pullback scalper on majors with grid hold-on-loss
agent_key: claude-code
skills: []
default_config:
  frequency_sec: 60
  total_amount_quote: 500
  risk_limits:
    max_total_exposure_quote: 1500
    max_drawdown_pct: 5
    max_open_executors: 4
default_trading_context: |
  Trade BTC-USDT and ETH-USDT on Binance perp. Bias long.
created_by: 12345
created_at: 2026-04-01T00:00:00Z
---

You are a disciplined scalper. Each tick:

1. Inspect the active executors and any held positions.
2. Pull 5m candles via the candles tool; compute your bias.
3. If flat and conditions are favorable, spawn a GridExecutor sized to
   `total_amount_quote`, with `stop_loss_keep_position = true`.
4. If you are holding a leftover position from a closed grid, wait for a
   recovery within 1% of breakeven, then exit with an OrderExecutor.
5. Never exceed the risk limits — the framework will block you anyway.
6. After acting, write one line to the journal explaining *why*.
```

You can also create / edit strategies through the Telegram UI under `/agent`, which uses `StrategyStore` under the hood.

### 4.2 Build a strategy step-by-step

The `trading-agent-builder` skill walks you through the canonical 5-phase flow:

1. **Strategy design** — describe edge, timeframe, instruments.
2. **Market data selection & processing** — pick the routines / MCP tools.
3. **Strategy logic definition** — write `agent.md`.
4. **Test in dry-run** — one tick, no trading. Inspect reasoning in the experiment snapshot.
5. **Test in run-once** — one tick, real trading, controlled.
6. **Deploy live** — `loop` mode with a frequency and risk limits.

### 4.3 Run an agent (programmatic)

```python
from condor.trading_agent.engine import TickEngine
from condor.trading_agent.strategy import StrategyStore

store = StrategyStore()
strategy = store.get_by_slug("river_scalper")

engine = TickEngine(
    strategy=strategy,
    config={
        "execution_mode": "loop",         # or "dry_run" / "run_once"
        "frequency_sec": 60,
        "server_name": "binance_main",
        "total_amount_quote": 500,
        "risk_limits": {
            "max_total_exposure_quote": 1500,
            "max_drawdown_pct": 5,
            "max_open_executors": 4,
        },
        "trading_context": "Bias long. Avoid news windows.",
    },
    chat_id=telegram_chat_id,
    user_id=telegram_user_id,
)

await engine.start(bot=telegram_bot)
```

In production, agents are normally started/stopped from the Telegram `/agent` flow, which delegates to the same `TickEngine`.

### 4.4 Inspecting what the agent did

- `trading_agents/{slug}/sessions/session_N/journal.md` — chronological summary, last decisions, tick log, executors, snapshot index.
- `trading_agents/{slug}/sessions/session_N/snapshots/snapshot_K.md` — the *full* tick: system prompt, response text, every tool call with arguments and status, executor snapshot, risk state, duration.
- `trading_agents/{slug}/learnings.md` — lessons the agent has chosen to keep across sessions.
- `trading_agents/{slug}/dry_runs/experiment_N.md` — dry-run / run-once results.

### 4.5 Injecting directives mid-flight

```python
engine.inject_directive("Reduce exposure, news event in 10 min.")
```

The next tick's prompt will include the directive verbatim under a `USER DIRECTIVES` section, then clear it.

---

## 5. Mental model

> An agent is a *folder* on disk and a *tick loop* in memory. The folder is its long-term memory; providers give it short-term situational awareness; the LLM is its decision function; executors are its hands; the risk engine is the wrist it can't move past. Everything an agent owns is tagged with its `controller_id`, which makes the whole system safely composable: any number of agents can share the same exchange account without ever stepping on each other.

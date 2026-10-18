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

### Components in `condor/agents/`

| File | Role |
|---|---|
| `engine.py` | `TickEngine` — one instance per running agent. Runs the tick loop, builds prompts, drives ACP sessions, captures tool calls, persists snapshots. |
| `strategy.py` | `Strategy` + `StrategyStore`. Strategies live as `agent.md` files (YAML frontmatter + markdown body) under `agents/{slug}/`. |
| `journal.py` | `JournalManager` — compact, human-readable per-session memory: summary, decisions, ticks, snapshots, executors. Also `learnings.md` cross-session. |
| `prompts.py` | Builds the per-tick prompt: system prompt + strategy + provider summaries + journal context + risk state. |
| `risk.py` | `RiskEngine` + `RiskLimits`. Hard guardrails (max exposure, max drawdown, max open executors) and the permission callback that auto-approves tool calls only if they pass the risk check. |
| `providers/` | Deterministic pre-tick data fetchers. Two core providers ship: `executors.py` (filtered by `controller_id`) and `positions.py` (positions summary, also filtered by `controller_id`). |
| `config.py` | `AgentConfig` schema persisted per session. |

### File layout per agent

```
agents/
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
5. **Build the prompt** via `build_tick_prompt(...)`.
6. **Spawn an ACP session** for the strategy's `agent_key` (claude-code, gemini, copilot…) with MCP servers wired in (Hummingbot tools, market data, notifications, journal writes). Stream events, capture text and tool calls.
7. **Persist** — for sessions, write `snapshot_N.md`, append a tick to the journal, and update the running summary. For dry-runs / run-once, write a flat `experiment_N.md`.

### Run modes

| Mode | Behavior |
|---|---|
| `dry_run` | One tick, no trading capability granted to MCP. Pure reasoning test. Saves a single experiment snapshot. |
| `run_once` | One tick *with* trading. Useful for manual single-shot execution. |
| `loop` | Standard mode. Ticks every `frequency_sec` until stopped or `max_ticks` reached. Creates a session folder with full journal. |

### Risk + permissions

`auto_approve_with_risk_check(...)` is wired in as the ACP permission callback. Every tool call the LLM tries to make is intercepted: trading-side tools are checked against `RiskLimits` (max exposure, max drawdown, max open executors); read-only tools are auto-approved. The agent literally cannot exceed its limits — the framework refuses on its behalf.

### What survives a restart

A restart never resurrects a session: `LoopSupervisor._restart` (`condor/runtime/loops.py`) always mints a **fresh session number**, because the dead session's journal is closed history. What it carries across is this (CORR-728):

| | Survives a restart | Where it comes from |
|---|---|---|
| Strategy config (`frequency_sec`, `risk_limits`, `total_amount_quote`, `trading_context`…) | ✅ as it stands **now** | re-read through `load_full_config`, so an edit made while the session was dead takes effect — that is the point of re-reading rather than replaying |
| **Pins** — `server_name`, `agent_key`, `model_base_url` | ✅ as the dead run had them | overlaid from the dead session's recorded pins; the one definition of the set is `PIN_FIELDS` in `condor/agents/config.py` |
| `restart_on_boot` | ✅ forced on | a run that came back is still opted in |
| Scratch state (`condor.runtime.state`) | ✅ | keyed on (agent, strategy), not on the session |
| Memory, learnings, skills | ✅ | they hang off the Agent/strategy folder |
| Journal, canvas, session report, tick count | ❌ new session | `sessions/session_{N+1}/`, starting at tick 1 |
| Open executors and bots | ✅ still running | the venue never heard about the restart; the new session **adopts** them on its first tick (`_adopt_running_bots`, `_adopt_running_executors`). An executor whose strategy did not come back is reported as orphaned, never stopped |

A restart makes the same up-front checks a start does — an unknown pinned server and an unrunnable model are refused rather than started — replaces any live engine for the strategy instead of adding a second one to it, and reports success only once the new run has a first tick to its name.

#### The five places a pin can live

This is why "it came back on the wrong server" was hard to reason about. One `server_name` (or `agent_key`) can be written in five places, and the *first* of these that is set wins:

1. **The start request** — `config` in the `POST /agents/{slug}/strategies/{sslug}/start` body (the Start-session form, `control_agent(start)`, a delegation). Persisted to `sessions/session_N/config.yml` and nowhere else, which is why a restart that only re-read the strategy config lost it.
2. **The strategy's saved `config.yml`** — `agents/{slug}/loops/{sslug}/config.yml`, what the config editor writes.
3. **The strategy's `loop.md` frontmatter** — `default_config:`, and `agent_key:` for the model.
4. **The Agent's `AGENT.md` frontmatter** — `agent_key:`, the Agent's default model for every playbook it owns.
5. **The `AgentConfig` field default** — `server_name: "local"`, `agent_key: ""` (meaning "the runtime's default model"). An unresolvable *inherited* server name is not an error: the engine falls through to the owner's accessible servers.

A sixth copy is written, never read as a source of truth: `status.json` next to the session journal carries a `pins` snapshot, rewritten every tick, and that is what `_restart` reads first (falling back to `sessions/session_N/config.yml` for sessions recorded before the snapshot existed).

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

### 3.1 Controller mode — operating a named bot

By default an agent acts through standalone executors tagged with its `controller_id`. As an alternative, an agent can **operate a Hummingbot bot's controllers** — deploy a bot under a stable name and retune its controller configs each tick — instead of (or in addition to) spawning executors.

Controller mode is triggered by the effective **`bot_name`** being non-empty. `bot_mode` (default `auto`) decides how that name is resolved (`condor/agents/ownership.py:resolve_bot_name`):

| `bot_mode` | Effective `bot_name` |
|---|---|
| `auto` (default) | exactly the configured `bot_name` — controller mode iff it is non-empty |
| `bot` | the configured `bot_name`, or, when empty, **derived** as `{agent_slug}-{strategy_slug}` |
| `executors` | always empty — forces executor mode even with a `bot_name` set |

The name is resolved once at engine start and written back into the session's `config.yml`, so per-session PnL attribution reads back the name the run actually used. When controller mode is on:

- The tick prompt gains a `[CONTROLLER MODE]` block telling the agent which bots it owns and that it should steer them via `manage_controllers` (define/update controller configs) + `manage_bots` (`deploy` / `update_config` / `start_controllers` / `stop_controllers`), not standalone executors.
- The **base rules at the top of the prompt switch with it** (`BASE_PROMPT_LIVE_CONTROLLER` vs `BASE_PROMPT_LIVE_EXECUTORS` in `condor/agents/prompts.py`): controller mode names controllers as the trading surface, demotes the `create_*_executor` tools to an opt-in fallback, preloads `manage_bots` + `manage_controllers` via ToolSearch, and drops the executor-only `controller_id` hint. A single shared base prompt used to open every tick with "Trade ONLY via the executor tools" and then contradict itself in the `[CONTROLLER MODE]` block.
- The agent's reported PnL becomes `executor_pnl(controller_id == agent_id)` **+** `bot_pnl(bot_name == config.bot_name)`. The two sources are disjoint — bot controllers tag their executors with their own config ids, never the `agent_id` — so the merge is plain addition with no double counting (`condor/fetchers/bot_performance.py` aggregates the bot side; `condor/agents/performance.py` folds it in).

**Ownership is a namespace, enforced at the tool call** (FEAT-017, `condor/agents/ownership.py`). Every bot a strategy may deploy or mutate lives under `{agent_slug}-{strategy_slug}` — the base itself, any tagged sibling (`…-btc`, `…-eth`), and their deployed instances (Hummingbot appends a `-YYYYMMDD-HHMMSS` timestamp). Slugs never contain `-` (`condor.frontmatter.slugify` maps `[\s-]+ → _`), so the delimiter is unambiguous and two agents can never claim the same bot — uniqueness is structural, with no registry or lock.

- A `manage_bots` call with a *dangerous* action (`deploy`, `stop_bot`, `start_controllers`, `stop_controllers`, `update_config`) on a bot outside the namespace is **cancelled** at the permission callback (`condor/agents/risk.py:auto_approve_with_risk_check`) and recorded. Read-only actions (`status`, `logs`, `get_config`) are never restricted — an agent still sees the whole fleet.
- A `bot_name` configured before this convention (e.g. `river-scalper`) is carried as a **declared** name and stays owned, so existing agents keep working with no config change.
- Each session writes `{session_dir}/owned_bots.json`: the bases it owns, whether it `deployed` or `adopted` each, and when. A restart always mints a *new* session (see `condor/runtime/loops.py`), so the first tick runs an **adoption pass** that takes over the still-running bot instead of orphaning it and deploying a duplicate. Experiments (`dry_run` / `run_once`) have no session dir and keep the ledger in memory only.
- The guard can only allow or cancel — it cannot reply to the model mid-tick. A refusal reaches the agent through the journal (`bot_ownership_blocked`) and the next tick's `[CONTROLLER MODE]` block, which is generated from the ledger.

**Enablement (data, not code).** A controller-mode agent needs `manage_bots` **and** `manage_controllers` in its `AGENT.md` `tools:` allowlist (the allowlist is enforced in `condor/acp/pydantic_ai_client.py`). Example `default_config`:

```yaml
default_config:
  bot_mode: bot                # derives bot_name = {agent_slug}-{strategy_slug}
  total_amount_quote: 500
```

---

## 4. Usage

### 4.1 Create a strategy

A strategy is just `agents/{slug}/agent.md`:

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
from condor.agents.engine import TickEngine
from condor.agents.strategy import StrategyStore

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

- `agents/{slug}/sessions/session_N/journal.md` — chronological summary, last decisions, tick log, executors, snapshot index.
- `agents/{slug}/sessions/session_N/snapshots/snapshot_K.md` — the *full* tick: system prompt, response text, every tool call with arguments and status, executor snapshot, risk state, duration.
- `agents/{slug}/learnings.md` — lessons the agent has chosen to keep across sessions.
- `agents/{slug}/dry_runs/experiment_N.md` — dry-run / run-once results.

### 4.5 Moving an agent to another install

An agent is a folder, which is exactly why handing one to someone else used to
mean copying a directory and then repairing whatever was true only of the box it
came from. It travels as **one file** instead (`condor/agents/bundle.py`,
FEAT-133):

```
manage_agents(action="export", agent_slug="river_scalper",
              notes="Widened the grid", author_name="Fede")
    -> data/exports/river_scalper-20261015.condor-agent.json
manage_agents(action="import", path="…/river_scalper-20261015.condor-agent.json",
              agent_slug="river_scalper_2")   # agent_slug renames it on the way in
```

The dashboard has the same two doors: **Export** on the agent's workspace
header, and Settings → Condor → **Agents** for import, which previews the
bundle before anything is written.

What travels is the *library* half of the folder — the same selection
`publish_to_stock` uses (`condor.layering.publishable_files`): `AGENT.md`,
`skills/`, `routines/`, `controllers/`, `loops/` and any other authored file at
the root. What stays behind is everything above that this install accumulated by
running the agent: `store/`, `sessions/`, `dry_runs/`, `learnings.md`,
`config.yml`, `mutes.yml`, `proposals/`. Three frontmatter fields are rewritten
in the bundle's copy because they are facts about the exporting install, not
about the agent: `server_name`, `created_by` and the `forked_from`/`forked_at`
stamp.

The manifest also says **which** agent this is and **what it needs** (FEAT-143):

| Field | What it means |
|---|---|
| `agent.agent_uid` | 32 hex characters, stable across the rename `agent_slug` performs, so two homes that came from one bundle are still recognisably one agent. Minted by `AgentStore.create`, or at export for an agent that predates the field; never on a read path. Not spelled `agent_id`, which already means the slug everywhere here. |
| `version` / `notes` | An integer that orders two bundles of one `agent_uid`, bumped only when the agent's *content* changed since the last export, plus the exporter's free-text changelog. Meaningful only within one lineage: two installs that import one bundle and both edit it both produce version 2. The file diff is the authoritative answer. |
| `author` | `{name, handle}`, and a **claim** — Condor signs nothing and verifies nothing. `handle` is reserved for a hub to fill in at publish time and is empty on a locally exported file. Every surface that shows it must say "unverified". |
| `requires` | The connectors, `tools:` allowlist, `agent_key` and skills (split into those carried in the bundle and those expected from `agents/_shared/skills`) — **derived from the files**, never authored, so it cannot drift from what the agent names. A loop's connector is read through `condor.preflight.requirements_of`, FEAT-139's single reader of a stated requirement. |

Three ``AGENT.md`` keys back this: `agent_uid`, and the `bundle_version` /
`bundle_digest` pair an export stamps so the next one can tell changed content
from an unchanged re-export. The digest is the one thing that does not travel —
it measured the exporter's bytes.

The preview answers `unmet`: every requirement this install cannot supply, with
what it has instead and whether the agent is blocked without it. It never
refuses the import. "I will add that exchange's API key once I have read the
agent" is the honest case, and everything lands muted and unstarted regardless —
the preview says what will not work, and the operator decides. Whether capital
sits in the right asset under venue rules that make the strategy viable is a
different question, answered by `condor/preflight.py`.

Two rules make an import safe to accept from somebody else. It **creates and
never replaces** — a taken slug is refused with a free alternative, so a tuned
local agent cannot be silently reset. And everything it brings **arrives muted**:
an imported routine runs inside the Condor process with no sandbox, so the
preview names every executable file before the write and the operator unmutes
them one at a time after reading each. Nothing is started by an import.

### Updating one from a newer bundle

"Never replaces" is right for a *stranger's* agent and wrong for a newer
version of one you already run: without a second verb the new version lands
beside the old as `<slug>_2`, the memory forks, and the dashboard lists two
agents one of which is stale. That verb is `bundle.plan_update` /
`bundle.apply_update` (FEAT-145), with the same one-builder rule as the import
pair — `POST /agents/{slug}/update/preview` and `POST /agents/{slug}/update`,
`manage_agents(action="upgrade", agent_slug=…, path=…)`, and in the import
dialog the collision step offers it when `is_update` says the bundle is the
same agent.

It speaks `condor/updates`' vocabulary one level down: **blockers**, warnings
and a plan. Each blocker refuses and names the reason.

| Blocker | Why |
|---|---|
| `different_agent` | The bundle's `agent_uid` is not the installed agent's. Two agents, not an update — the answer stays the 409 and a rename. |
| `no_basis` | The agent has no `agent_uid` and no `.bundle.json`: it was authored here, or imported before the stamp existed. Nothing says this bundle is its upstream. |
| `older_bundle` | The bundle's `version` is lower than the stamp's. |
| `live_loop` | `get_engine(slug)` answers an engine. Swapping a skill's instructions under a session that is mid-tick changes what the model is told between one tool call and the next. |
| `stock_agent` | An agent the repo ships. `publish_to_stock` is the one sanctioned write into the tracked library, and it is admin-only. |
| `no_such_agent` | Nothing installed under that slug. |

`.bundle.json` at the root of the home is the bookkeeping: the `agent_uid`, the
`version` and a digest per carried file — the record of *what the bundle said
last time*, which is the only way to tell an operator's local edit from an
upstream change. A dotfile, so `publishable_files` cannot see it and it never
travels in a bundle of its own agent. `AGENT.md`'s digest is taken over the
file with this install's own keys (`created_by`, `created_at`, `server_name`,
`agent_key`) removed, so pinning a server is not mistaken for the operator
rewriting the agent — which would otherwise freeze upstream's instructions out
of every later update.

The plan is one row per path, from three digests — the bundle's, the file on
disk, and the stamp's:

| Verdict | What it means, and what happens |
|---|---|
| `added` | The bundle carries it, the stamp never did. Written, and muted if it is a routine or a skill. |
| `changed` | Unedited on disk, different upstream. Replaced. |
| `unchanged` | All three agree. Nothing to do. |
| `local_only` | Edited here, unchanged upstream. **Kept.** |
| `conflict` | Edited on both sides. **Kept**, and the plan carries the bundle's copy so a surface can offer it. |
| `removed` | In the stamp, gone from the bundle, unedited here. Deleted. |
| `conflict_removed` | Same, but edited here. **Kept.** |
| *not listed* | A file the operator created that the bundle never carried. Never touched, never reported as removed. |

Defaulting a conflict to *keep local* is the deliberate half: an operator's
edit is the only content in the home nobody else can reproduce. Conflicts are
never merged — these are markdown instructions a model reads, and a
conflict-marked `SKILL.md` is not a conflict to resolve later, it is a new
instruction the model will follow.

What an update replaces is exactly `publishable_files`' half of the home, so
everything this install accumulated by running the agent is untouched *by
construction*: `store/`, `config.yml` (the risk limits actually in force),
`sessions/`, `dry_runs/`, `learnings.md`, `state.json`, the bot ledgers,
`proposals/` and `delegations/`. `mutes.yml` is the one runtime file an update
writes, and only to switch off what is genuinely **new**: a name already in it
is left exactly as it is, and a `changed` item is *not* re-muted. That
asymmetry is a decision — re-muting on every update would cost the whole
curation pass again, and the diff already named the files whose instructions
changed. Execution consent needs no such rule: it is keyed by content, so a
replaced routine is pending again on its own.

Three things make a half-finished update recoverable rather than destructive.
The previous bytes of every replaced or deleted file are copied to
`.bundle_backup/<timestamp>/` first (a dotdir, so it never travels — the
`.server_backups` idiom). The stamp is rewritten **last**, so an interrupted
update leaves one describing the previous state. And a file whose disk copy
already holds the bundle's bytes reads as `unchanged`, so the next plan
recognises what the interrupted run got to instead of reporting the whole
agent as a conflict. There is deliberately no rollback: the home holds an
agent somebody has been running, and removing it on a failed write is the
silent reset this verb exists to avoid.

Nothing is started. No `control_agent(start)`, no delegation, no tick — the
result names the next step instead. And an update does not re-run a preflight:
whether the agent can still trade here is `condor/preflight.py`'s question.

### Nothing imported executes before consent

A mute, though, is not an execution gate and never was: it is subtracted
*after* discovery, and discovery is `spec.loader.exec_module`. So the two
surfaces an operator uses to *review* an imported routine — the `/routines`
list and the source viewer — were themselves what ran it. `condor/agents/
code_trust.py` closes that (FEAT-147).

- `bundle.apply` writes `store/code_trust.yml` for the agent it just imported.
  Its **presence is the gate**: that agent's routines are not imported until
  each file is allowed. An agent created here has no such file and nothing
  changes for it — gating code somebody typed into their own install would be
  a consent dialog for your own work.
- Consent is keyed by the file's **sha256**, not its name. A routine allowed
  and then edited — by hand, or by an update from a newer bundle — is pending
  again, because the thing the operator read is not the thing on disk.
- A pending routine is still **listed**, still **readable** and still
  **mutable**. `condor/routine_meta.py` reads its name, category, continuous
  flag and description out of the parse tree with `ast`, importing nothing;
  `GET /routines/<name>/source` serves the file from its path instead of
  through `inspect.getfile(run_fn)`. The one thing it cannot show is the
  `Config` **form**: pydantic builds a model's fields at class-creation time,
  so there is no schema until the module has run. You cannot configure code
  you have not agreed to run.
- Running one is refused — through `RoutineStore._resolve_routine`, so the web
  runner, the scheduler, the MCP tool and `condor.primitives` all get the same
  sentence, which names the allow action.
- `PUT /agents/{slug}/code-trust` is the gesture, and it is **separate from
  unmuting on purpose**: unmuting says *this agent may use it*, allowing says
  *this code may run here*.
- The record lives under `store/`, which `condor.layering.is_library_path`
  already refuses, so a hand-edited bundle carrying `store/code_trust.yml`
  cannot arrive pre-allowed — the import's existing path guard rejects it.

Out of scope, deliberately: a skill is prose and is not executed, and a
controller runs inside the Hummingbot instance rather than in this process.
`routines/*.py` is the whole in-process executable surface a bundle carries,
which is why one file-level gate covers it.

### Reviewing a bundle before it runs

The scrub above rewrites three frontmatter fields and looks at no file *content*,
so on its own a bundle could carry an API key, a stranger's endpoint, a skill
steering the model at a tool that signs, or risk limits far wider than this
install would set — and nothing would say so. `condor/agents/bundle_review.py`
is the one review that names those (FEAT-144). Five categories, ordered
most-decisive first:

| Category | What it reports |
|---|---|
| `secret` | `condor.runtime.secrets.scan` over every file, with its `certain` verdict passed through unchanged — a recovery phrase and a 64-int keypair are certain; a bare 64-hex and an 87-char base58 are also a transaction hash and a signature, which are routine here. |
| `endpoint` | Every host the files dial, labelled `loopback`, `private` or `public`. The split is the point: an agent talking to the local Hummingbot API is the normal case, and reaching a stranger's host is the finding. |
| `code_run` | `exec`, `eval`, `__import__`, `subprocess`, `run_code` and an `open()` in a write mode, in the `.py` files the import will write. |
| `tool_steer` | A money-moving tool from `condor/runtime/danger.py` named in prose or code, with the line that names it. **Context, not an accusation** — every strategy skill names its own executor tool, which is why it carries its line, is ordered second-to-last, and refuses nothing. |
| `risk_limits` | Every `default_config.risk_limits` figure the bundle brings, **as values, with no pass/fail**. They become the agent's guardrails on its first run and nothing clamps them; an install-wide ceiling would be a different change, affecting every agent, imported or not. |

A finding carries `path`, `line`, `start`/`end` and a `label` from a closed set —
**never the matched value**, the rule `condor/runtime/secrets.py` sets, so a
finding can be logged and counted without becoming the leak it exists to prevent.
A caller that needs the bytes slices `text[start:end]` itself. `context` is the
one exception, for the two categories where the text *is* the finding:
`tool_steer`'s line (run through `redact` first) and `risk_limits`' figure.

Who does what with it:

- **Export** refuses on a `certain` `secret`, naming the file and the byte span.
  That is the one moment the value is still fixable by the person who owns it,
  and a published bundle cannot be unpublished out of somebody's download.
  Everything else warns.
- **Import** refuses *nothing new*. The findings ride on the preview and on the
  apply result, field for field, and what protects this side is still that the
  code arrives muted and nothing is started.
- **A hub** re-runs this exact function on a submission rather than trusting
  whoever exported it — in that case the exporter is the adversary and runs their
  own copy of the exporter, so the receiving side's check is the one that counts.
  That is why the module imports only the standard library plus
  `condor.runtime.secrets` and `condor.runtime.danger`, asserted in a subprocess
  by `tests/test_bundle_review.py`.

It reports and never rewrites. Substituting a value inside `routines/r.py` or a
YAML config — which is what `condor/sharing/scrub.py` does to a transcript, where
a mangled string costs nothing — would produce an agent that silently does not
work. And it is best-effort, so the copy says so: it warns, it does not prevent.

### The hub: publish, browse, install

Everything above stops at the file, so handing an agent to a stranger still
meant emailing one. `condor/hub.py` is the distribution half (FEAT-146):

```
manage_agents(action="hub_browse")                       # no account needed
manage_agents(action="hub_install", path="<listing_id>")  # path is the listing id
manage_agents(action="publish_hub", agent_slug="river_scalper",
              notes="Widened the grid")                   # admin only
```

The dashboard has the same three doors in Settings → Condor → **Agents**:
a browse-and-install panel (`HubBrowser.tsx`), the token field, and **Publish**
beside Export on the agent's own workspace header. The service itself lives in
a codebase that is not this one; standing it up is TASK-004.

**The trust model is three sentences, and the rest hangs off them.**

1. **A bundle is untrusted code wherever it came from, and the gate is on the
   receiving install.** Condor installs any well-formed bundle — from a file,
   from a friend, from the hub — and never requires a hub signature. The gate is
   what the two sections above already built: every executable named before
   anything is written, the findings beside that list, routines and skills muted,
   no execution consent for any imported `.py`, nothing started.
2. **The hub is a discovery and moderation service, not a security boundary.** A
   `validated` badge is a claim that a human reviewed *one submitted revision at
   one point in time*. It is carried in the listing, rendered as provenance, and
   read by nothing that decides whether to write — asserted by comparing a
   `community` install against a `validated` one and requiring the two responses
   to differ in the badge alone. A badge never shortens the review: a bundle
   validated as submitted is not the bundle the operator runs after editing it.
3. **Integrity is a content digest over HTTPS, not a signature.** The listing
   carries the bundle's sha256; `hub.fetch` verifies the downloaded bytes
   against it *before any caller parses them*, and FEAT-133's per-file digests
   catch any later edit. `author` is an account claim the hub attests at upload;
   nothing binds it to the bytes, and every surface says so.

The consequence, which is the one hard requirement this places on the service:
**the hub re-runs `bundle_review` on every submission** and publishes its
findings with the listing (`review_version` says which review produced them, so
drift with a client's own copy is visible). The export-side review is advice to
the author — who runs their own copy of the exporter — and the submission-side
one is the only review whose input the author does not control.

Three design points worth not re-litigating:

| Decision | Why |
|---|---|
| `HUB_URL` is **compiled in** and no environment variable moves it | It is the SSRF answer, not tidiness. FEAT-133 refused import-from-URL because "the Condor process can reach `localhost` and cloud metadata endpoints". A compiled-in base, a listing id through `validate_identifier` (plus a refusal of `.`/`..`, which that charset admits) before any request exists, and an opener that refuses redirects, together mean the server-side fetch is not a fetch of a user-supplied URL at all. Same rule as `condor/sharing/outbox.py`'s collector address. |
| **No durable outbox** | The sharing queue exists because its sweep sends what nobody is watching and because a revocation must survive a restart. A publish is an interactive act with a human watching the result; a failure is reported and retried by pressing the button again. |
| The unpublish carries **no delete-token capability** | `condor/sharing/wire.py`'s `delete_token_hash` exists because that collector "must not know who you are". A hub publish is authenticated by an account, and the same account authenticates the withdrawal. |

Publishing is admin-only — tighter than the other agent writes, because there is
one install-wide token and a claim created under it is made in that account's
name — and it sends the bytes `GET /agents/{slug}/bundle` returns, built by the
same `build`. So it inherits the export's refusal for a value that can only be a
secret, which is the refusal written for exactly this moment: a published bundle
cannot be unpublished out of somebody's download.

---

## 5. Mental model

> An agent is a *folder* on disk and a *tick loop* in memory. The folder is its long-term memory; providers give it short-term situational awareness; the LLM is its decision function; executors are its hands; the risk engine is the wrist it can't move past. Everything an agent owns is tagged with its `controller_id`, which makes the whole system safely composable: any number of agents can share the same exchange account without ever stepping on each other.

# Condor Agent Fleet — Redesign Proposal

> Status: **idea / draft for discussion**. This is a design exploration, not a committed
> spec. It re-frames Condor's agent layer from "an agent is a trading bot" into "an
> agent is one role in a multi-agent *system*", and proposes the backend (A2A) and
> frontend (Pentagon-style fleet view) changes that follow from that.

---

## 0. TL;DR

Today a Condor "agent" == an autonomous LLM trading loop that acts through Hummingbot
executors. It's a single shape. The proposal:

1. **Generalize the agent abstraction** into *typed agents* (kinds), each defined by a
   **capability bundle** rather than hardcoded trading prompts. Trading becomes one kind
   among several: Collector, Analyst, Manager, Trader, Operator, Overseer.
2. **Lean on what already works**: V2 Controllers are our deterministic execution layer,
   routines are our deterministic data layer, and the Journal/Snapshot/Learnings triad is
   our memory. Keep all three. Agents *orchestrate* these; they don't replace them.
3. **Add A2A (agent-to-agent)** as the new backbone: agents publish typed **artifacts**
   (routine reports) to a shared bus, and consume each other's artifacts as tick inputs.
   This turns isolated agents into composable **pipelines**.
4. **Condor becomes a launcher/scheduler** for agents. The recurring execution we already
   get from the `/loop` feature becomes the heartbeat; A2A becomes the wiring.
5. **Frontend becomes a fleet view** (Pentagon-like): a live topology of agents + the A2A
   edges between them, not just a list of bots.

The throughline: *deterministic where we can (controllers, routines), reasoned where we
must (the LLM tick), composable across agents (A2A).*

---

## 1. Where we are today (honest review)

Read first: `condor/trading_agent/README.md`. Summary of the current design:

- **One agent shape.** `TickEngine` (`condor/trading_agent/engine.py`) runs a fixed-interval
  loop. Each tick: run core providers → read journal → build prompt → spawn a fresh ACP
  session → capture tool calls → write snapshot. Every agent is assumed to be a *trader*.
- **The prompt is trading-shaped.** `prompts.py` hardcodes `BASE_PROMPT_LIVE` /
  `BASE_PROMPT_DRY_RUN` ("Trade ONLY via manage_executors…") and a fixed `TOOL_PRELOAD`
  list. Capability == "can place executors".
- **Isolation by `controller_id == agent_id`.** This is the framework's best idea: every
  executor/position an agent creates is tagged with its id, giving each agent a virtual
  sub-account. Two agents never step on each other.
- **Deterministic layers already exist:**
  - **Providers** (`providers/base.py`, `ProviderRegistry`) — pre-tick data fetchers that
    return `ProviderResult{data, summary}`. Today only `executors` + `positions`.
  - **Routines** (`routines/base.py`) — auto-discovered Python scripts, one-shot or
    continuous, returning rich `RoutineResult` (text/table/chart/sections). Global +
    agent-local (`trading_agents/{slug}/routines/`).
  - **Reports** (`condor/reports.py`) — composable HTML reports with a JSON index
    (`ReportBuilder`, `list_reports`, `get_report`). Already an artifact store, just not
    addressed as one.
  - **Notes** (`mcp_servers/condor/tools/notes.py`) — a per-chat KV store.
- **Memory triad** (`journal.py`): per-session **Journal** (summary + decisions + tick
  log), **Snapshots** (full prompt/response/tool-calls per tick), cross-session
  **Learnings** (`learnings.md`). This is good and stays.
- **Execution backends are pluggable** via ACP (`condor/acp/`): claude-code, gemini,
  copilot, codex as subprocess CLIs, plus pydantic-ai for local/cloud models. The
  `permission_callback` gates every tool call through `RiskEngine`.
- **Surfaces:** Telegram (`handlers/agents/`) and Web (`condor/web/routes/agents.py`,
  `frontend/src/pages/Agents.tsx` + `AgentDetail.tsx`). The web UI is a card list +
  per-agent tabs (overview, sessions, snapshots, routines, experiments).

### What's limiting

| Limitation | Consequence |
|---|---|
| Agent kind is implicit (always "trader") | Can't model a data collector, a bot manager, or a risk overseer without abusing the trading prompt. |
| Capabilities are hardcoded in `prompts.py` | Every agent gets the same tool surface and risk posture; no least-privilege. |
| Agents are isolated *end to end* | An agent can't consume another agent's output. No pipelines. Cross-agent knowledge only happens by a human copy-pasting. |
| "Manager" work is awkward | Managing V2 controllers/bots (deploy, tune, stop) is done through the raw executor prompt, not a first-class role. |
| Scheduling is just `frequency_sec` | No daily cron, no event triggers ("run when upstream publishes"). |
| Frontend is a flat list | No sense of the *system*; you can't see how agents relate. |

---

## 2. The reframe: agent = a typed role in a system

> An agent is still *a folder on disk + a tick loop in memory*. What changes: the folder
> declares a **kind** and a set of **capabilities**, and it can **subscribe to and publish**
> artifacts. The tick loop, journal, snapshots, learnings, and `controller_id` isolation
> are unchanged.

### 2.1 Agent kinds

A `kind` selects a base prompt, a default capability bundle, a default provider set, and a
risk posture. Proposed kinds:

| Kind | One-liner | Acts through | Trades? |
|---|---|---|---|
| **Collector** | Gathers + structures data, publishes reports. | routines, market-data tools | No (read-only) |
| **Analyst** | Consumes reports + data, produces recommendations/alerts. | routines, notifications | No |
| **Manager** | Deploys/tunes/stops **V2 controllers & bots**. | `bot_management`, `controllers` | Via controllers |
| **Trader** | The current agent — acts through executors. | `manage_executors` | Yes (executors) |
| **Operator** | Manages infra: servers, gateway, keys, connection health. | `servers`, `gateway` | No |
| **Overseer** | Cross-fleet risk/health; can throttle or stop other agents. | A2A control plane, risk | No (governs) |

`kind` is just frontmatter; the engine is the same. The only thing a kind changes is the
**profile** it resolves (§3).

### 2.2 Capabilities (the user's "agent capabilities or tools")

A **capability** is a named bundle of MCP tools + providers + risk policy. The four the
brief calls out map cleanly onto tool modules that *already exist*:

| Capability | MCP tool groups (existing) | Providers | Risk policy |
|---|---|---|---|
| `data_collector` | `market_data`, `geckoterminal`, `history`, `routines` | market snapshots | read-only, auto-approve |
| `bot_manager` | `bot_management`, `controllers`, `backtesting` | controller status | gated on capital deployed |
| `connection_manager` | `servers`, `gateway`, `gateway_clmm/swap` | server health | admin-gated |
| `risk_manager` | `portfolio`, `account`, + A2A control plane | exposure/positions | always-on, can veto |
| `execution` (current default) | `executors`, `trading` | executors, positions | `RiskEngine` limits |

An agent's frontmatter lists the capabilities it's granted. The engine resolves them into
(a) the MCP server allowlist passed to `_create_client`, (b) the provider set run pre-tick,
(c) the `permission_callback` policy, and (d) the tool-preload line in the prompt. This
**replaces the hardcoded `TOOL_PRELOAD_LIVE`/`BASE_PROMPT_LIVE`** with a lookup.

This is least-privilege by construction: a Collector literally cannot call
`manage_executors` because that tool isn't in its server set, and the permission callback
refuses it as a backstop.

### 2.3 Proposed `agent.md` frontmatter (additive, backward compatible)

```yaml
---
id: a1b2c3d4e5f6
name: Arb Scanner
kind: collector                 # NEW — defaults to "trader" if absent (back-compat)
capabilities:                   # NEW — defaults to ["execution"] for trader
  - data_collector
description: Scans CEX/DEX spreads, publishes arb opportunities
agent_key: claude-code

# A2A wiring (NEW) ---------------------------------------------------------
produces:                       # artifacts this agent publishes
  - topic: arb.opportunities
    schema: arb_opportunity_v1
consumes:                       # artifacts this agent reads at tick time
  - topic: market.morning_brief
    mode: pull                  # pull = latest at tick; push = trigger a tick
trigger:                        # NEW — see §5
  type: schedule
  cron: "*/5 * * * *"           # or {type: on_artifact, topic: ...}

# unchanged ----------------------------------------------------------------
default_config: { frequency_sec: 300 }
default_trading_context: ""
created_by: 12345
created_at: 2026-06-07T00:00:00Z
---

You are an arbitrage scanner. Each run:
1. Pull spreads across the configured venues via the market-data tools / your routine.
2. Rank opportunities by net edge after fees.
3. Publish the top N as an `arb.opportunities` artifact (publish_artifact tool).
4. Do NOT trade. You are a collector.
```

Absent `kind`/`capabilities`/`produces`/`consumes`, an agent behaves exactly as today.
**No migration required for existing trading agents.**

---

## 3. Capability profiles in the engine

Add a `condor/trading_agent/profiles.py`:

```python
@dataclass
class CapabilityProfile:
    name: str
    mcp_tools: list[str]          # MCP server / tool-group ids to expose
    providers: list[str]          # provider names to run pre-tick
    base_prompt: str              # role framing (replaces hardcoded BASE_PROMPT_*)
    risk_policy: str              # "readonly" | "executor_limits" | "capital_gated" | "veto"
    tool_preload: str             # the ToolSearch select line for ACP

REGISTRY: dict[str, CapabilityProfile] = { ... }   # one per capability

def resolve(kind: str, capabilities: list[str]) -> ResolvedProfile:
    """Union the capability bundles + the kind's base prompt."""
```

`TickEngine.__post_init__` resolves the profile once; `_create_client` uses
`profile.mcp_tools` to scope `build_mcp_servers_for_agent`; `prompts.build_tick_prompt`
takes `profile.base_prompt` + `profile.tool_preload` instead of the `is_dry_run` branch.
`run_core_providers` takes `profile.providers`. **This is the single biggest backend
refactor and it's mostly moving hardcoded strings into a registry.**

---

## 4. A2A — the agent-to-agent pipeline

> "What is the A2A pipeline?" — It is the mechanism by which **one agent's routine reports
> become another agent's tick inputs**, plus a thin control plane for governance. It is the
> formalization (and scheduling) of something the codebase can *almost* do already: agents
> can read each other's journals, and the report store already indexes artifacts. A2A makes
> that explicit, typed, addressed, and triggerable.

### 4.1 The unit of exchange: the Artifact

An **Artifact** is a typed, versioned, addressable report. It's the existing `ReportBuilder`
output (`condor/reports.py`) plus a small envelope:

```python
@dataclass
class Artifact:
    id: str
    topic: str               # e.g. "arb.opportunities", "market.morning_brief"
    schema: str              # "arb_opportunity_v1" — validates `data`
    producer_agent: str      # agent_id that published it
    created_at: str
    data: dict               # structured payload (machine-readable)
    summary: str             # 1-5 line LLM-readable digest (goes in prompts)
    report_id: str | None    # optional link to a rich HTML report
    ttl_sec: int | None      # staleness bound
```

`summary` is what gets injected into a consumer's prompt (parallel to today's
`ProviderResult.summary`); `data` is what routines/code consume; `report_id` links the
human-facing HTML in the existing Reports browser. **We already produce all three — we just
don't address them.**

### 4.2 The bus

A new `condor/trading_agent/a2a/bus.py` — start simple, file/SQLite-backed (same spirit as
the journal and report index), upgrade later:

```python
class ArtifactBus:
    def publish(self, artifact: Artifact) -> None: ...
    def latest(self, topic: str, schema: str | None = None) -> Artifact | None: ...
    def history(self, topic: str, limit: int = 10) -> list[Artifact]: ...
    def subscribe(self, topic: str, agent_id: str) -> None: ...   # for push triggers
```

Addressing is by **topic** (a stable name), not by producer — so you can swap which agent
produces `arb.opportunities` without rewiring consumers. Schema-tagging lets consumers
validate and lets the UI render typed cards.

### 4.3 Three interaction modes

A2A deliberately supports the three patterns multi-agent systems need:

1. **Pull (data dependency).** At tick build time, for each `consumes: {mode: pull}` entry,
   the prompt builder fetches `bus.latest(topic)` and injects a new
   `[UPSTREAM REPORTS]` section — *exactly* how `[CORE DATA]` providers are injected today.
   The consumer doesn't run upstream; it reads the freshest artifact. Stale artifacts (past
   `ttl_sec`) are flagged in the prompt.

2. **Push (event trigger).** When an agent publishes to a topic, the bus wakes any agent
   whose `trigger: {type: on_artifact, topic: ...}` matches, enqueuing a tick. This is how
   you build a true pipeline: *arb scanner publishes → arb manager wakes and acts*. Wiring
   reuses `inject_directive`-style queuing on the target `TickEngine`.

3. **Request/Response (agent-as-tool).** A new MCP tool `call_agent(agent_id|capability,
   request)` lets one agent synchronously invoke another and get an artifact back. This is
   the classic A2A "agent as a callable skill": e.g. a Trader asks the `risk_manager` agent
   "can I add $500 BTC exposure?" and blocks on the answer. Implemented as a short-lived
   `run_once` of the callee with the request as its trading_context, returning its artifact.

### 4.4 The control plane (Overseer / risk governance)

A2A isn't only data flow — it carries **directives**. An Overseer agent can:
- read fleet-wide exposure (aggregate across `controller_id`s),
- publish a `fleet.directive` artifact ("reduce risk, news in 10m"), and
- the engine maps `fleet.directive` into the existing `inject_directive()` on every
  subscribed agent, *or* trip a kill-switch (pause/stop) via the lifecycle API.

This is the natural home for the existing `RiskEngine`: today it guards one agent; as a
capability it guards the fleet.

### 4.5 Tools added to the Condor MCP server

In `mcp_servers/condor/tools/` (sibling to `notes.py`, `trading_agent.py`):

| Tool | Purpose |
|---|---|
| `publish_artifact(topic, schema, data, summary, report_id?)` | Producer side. |
| `read_artifact(topic, schema?, n?)` | Pull latest/history (for ad-hoc reads). |
| `call_agent(target, request)` | Synchronous request/response. |
| `list_fleet()` | Discover agents, kinds, topics (the "agent directory"). |

These slot into the capability bundles: every agent gets `read_artifact`; only producers
get `publish_artifact`; only Managers/Overseers get `call_agent`/control verbs.

### 4.6 Relationship to the open A2A protocol

This design intentionally mirrors the concepts in the public **A2A protocol** (Agent Card,
Task, Artifact, push notifications) so we could later expose Condor agents over the wire:
- our `agent.md` frontmatter ≈ an **Agent Card** (name, kind, capabilities, topics);
- a tick ≈ a **Task**;
- our `Artifact` ≈ A2A **Artifact**;
- push triggers ≈ A2A **push notifications**.

We don't need the network protocol on day one — the in-process bus is enough — but keeping
the vocabulary aligned means "expose/consume external A2A agents" is a later adapter, not a
rewrite.

---

## 5. Scheduling: `/loop` as the heartbeat

The brief notes we already have the key feature in the **`/loop`** Claude Code skill (run a
prompt/command on a recurring interval). The redesign makes scheduling first-class via the
`trigger` block:

| Trigger | Behavior | Backed by |
|---|---|---|
| `interval` | every N sec (today's `frequency_sec`) | existing loop |
| `schedule` (cron) | e.g. daily 08:00 for a morning brief | cron parse + loop |
| `on_artifact` | wake when an upstream topic publishes | A2A push (§4.3) |
| `manual` | run-once / on-demand only | existing run_once |

`/loop` is the user-facing way to arm a recurring agent run from chat; `on_artifact` is the
system-internal way agents chain to each other without a human in the loop. Same engine,
different wake source. The single-tick modes (`dry_run`, `run_once`) are unchanged and are
how `call_agent` is implemented.

---

## 6. Templates (concrete pipelines)

Templates are pre-baked `agent.md` presets = kind + capabilities + trigger + instructions.
Mapped to the brief's list, they compose into two showcase pipelines:

### Pipeline A — "Morning desk"
```
  morning_brief (collector, cron 08:00)
        │  publishes market.morning_brief
        ▼
  trade_recommender (analyst, on_artifact)
        │  publishes desk.recommendations  (notify user, no trades)
        ▼
  portfolio_watcher (analyst/risk, interval + on_artifact)
        │  watches positions vs. recommendations, alerts on drift
```

### Pipeline B — "Arb desk"
```
  arb_scanner (collector, interval 5m)
        │  publishes arb.opportunities
        ▼
  arb_controller_manager  ==  "bot manager" (manager, on_artifact)
        │  deploys / tunes / stops V2 arb controllers via bot_management
        ▼
  (controllers run deterministically — the agent only manages them)
```

| Template | kind | capabilities | produces / consumes |
|---|---|---|---|
| **Morning Brief** | collector | data_collector | → `market.morning_brief` |
| **Trade Recommender** | analyst | data_collector | ← morning_brief → `desk.recommendations` |
| **Arb Scanner** | collector | data_collector | → `arb.opportunities` |
| **Arb Controller Manager** (bot manager) | manager | bot_manager | ← arb.opportunities |
| **Portfolio Watcher** | analyst | risk_manager, data_collector | ← positions, recommendations |

Note Pipeline B's punchline matches the rationale: **V2 Controllers are the deterministic
execution layer.** The "arb controller manager" agent doesn't place orders — it *manages
controllers* that do. The LLM handles the judgment (which pairs, when to scale, when to
kill); the controller handles the mechanics. This is the cleanest division of labor and the
reason Managers are a distinct kind from Traders.

### "Each agent creates routines"
Unchanged and encouraged: agents author agent-local routines in
`trading_agents/{slug}/routines/` (already supported via `manage_routines`). In the new
model, a Collector's *job* is often "run my routine, shape the result, publish it as an
artifact." Routines stay the deterministic data muscle; the agent is the shaping/judgment
layer; the artifact is the hand-off.

---

## 7. Memory: keep the triad, add fleet memory

Unchanged: **Journal / Snapshot / Learnings** per agent (`journal.py`). These remain the
audit trail and per-agent memory — they're a strength.

Added: the **Artifact registry is fleet memory.** Where Learnings are an agent's private
lessons, published Artifacts are the fleet's shared, queryable state ("what did the scanner
see at 08:05?"). The existing Reports browser becomes the human window into that memory;
`read_artifact`/`list_fleet` are the agent window. No new memory *concept* — we're promoting
reports from a side-output to an addressable substrate.

---

## 8. Frontend: the Pentagon-style fleet view

Today: `frontend/src/pages/Agents.tsx` is a card grid; `AgentDetail.tsx` has tabs. Keep the
detail view; **replace the landing with a fleet/mission-control view.**

### 8.1 Fleet topology (the headline change)
A graph where **nodes = agents** (colored by kind, status dot = running/paused/stopped) and
**edges = A2A topics** (animated when an artifact flows). This makes the *system* legible —
you see Pipeline A and Pipeline B as actual wired graphs, watch an artifact pulse from the
arb scanner to the arb manager, and spot orphaned/stale links.

```
   ┌──────────────┐  market.morning_brief   ┌──────────────────┐
   │ Morning Brief│ ───────────────────────▶│ Trade Recommender│
   │  ● collector │                          │   ● analyst      │
   └──────────────┘                          └────────┬─────────┘
                                                       │ desk.recommendations
                                              ┌────────▼─────────┐
   ┌──────────────┐  arb.opportunities        │ Portfolio Watcher│
   │ Arb Scanner  │ ──────────┐               │   ● risk         │
   │  ● collector │           ▼               └──────────────────┘
   └──────────────┘   ┌──────────────────┐
                      │ Arb Ctrl Manager │  manages ▶ [V2 controllers]
                      │   ● manager       │
                      └──────────────────┘
```

### 8.2 Supporting panels (mission-control aesthetic)
- **Fleet status strip** — live count by status/kind, aggregate exposure, blocked agents.
- **Artifact feed** — chronological stream of published artifacts (topic, producer,
  summary, link to HTML report). This is the "what just happened across the fleet" view.
- **Control plane** — Overseer directives, kill-switch, per-agent pause/resume (the
  lifecycle endpoints already exist in `web/routes/agents.py`).
- **Agent detail** — unchanged tabs (sessions, snapshots, journal, routines), plus a new
  **Produces/Consumes** tab showing this agent's A2A wiring and recent artifacts.

### 8.3 New backend endpoints (extend `web/routes/agents.py`)
- `GET /fleet/graph` → nodes + edges (agents + topic wiring) for the topology.
- `GET /fleet/artifacts?topic=&limit=` → the artifact feed.
- `POST /fleet/directive` → publish a control-plane directive.
- `GET /agents/{slug}/wiring` → produces/consumes for the detail tab.

Telegram stays as a control/notification surface (start/stop/inject directive, receive
alerts); the rich topology lives on web — same split as today.

---

## 9. What changes in code (phased)

Each phase is independently shippable and back-compat (existing trading agents keep working
because `kind` defaults to `trader`, `capabilities` to `["execution"]`).

**Phase 1 — Capability profiles (no A2A yet).**
- Add `condor/trading_agent/profiles.py` (registry + `resolve`).
- `engine.py`: resolve profile in `__post_init__`; use it in `_create_client` (scope MCP
  servers) and `run_core_providers` (provider set).
- `prompts.py`: take `base_prompt`/`tool_preload`/role framing from the profile instead of
  the `is_dry_run` hardcode (dry-run becomes a risk_policy modifier, not a separate prompt).
- `strategy.py`: parse `kind` + `capabilities` from frontmatter (default to trader).
- *Outcome:* you can create a read-only Collector that physically can't trade. Templates:
  Morning Brief, Arb Scanner.

**Phase 2 — Artifacts + pull A2A.**
- Add `condor/trading_agent/a2a/{bus.py,artifact.py}` (file/SQLite-backed, reuse report
  store conventions).
- MCP: `publish_artifact`, `read_artifact`, `list_fleet`.
- `prompts.py`: inject `[UPSTREAM REPORTS]` from `consumes: {mode: pull}`.
- Web: `GET /fleet/artifacts`, agent `wiring` endpoint.
- *Outcome:* Trade Recommender consumes the Morning Brief. First real pipeline (pull only).

**Phase 3 — Triggers + push A2A + `/loop` integration.**
- `trigger` block parsing; cron + `on_artifact` wake sources in the loop.
- Bus `subscribe`/wake → enqueue tick on target engine.
- *Outcome:* Arb Scanner → Arb Controller Manager fires automatically. Pipeline B live.

**Phase 4 — Control plane + request/response.**
- `call_agent` MCP tool (run_once-backed); Overseer kind; `fleet.directive` → directives /
  kill-switch.
- *Outcome:* fleet-level risk governance; agent-as-tool.

**Phase 5 — Pentagon frontend.**
- `GET /fleet/graph`; new `Fleet.tsx` topology landing (replaces the flat card grid as the
  default `/agents` view; cards become a secondary list view).
- Artifact feed + control panels; Produces/Consumes tab in `AgentDetail`.

---

## 10. Open questions / risks

- **Bus durability & ordering.** Start file/SQLite (matches journal/report patterns); revisit
  if throughput demands it. Define topic retention + `ttl_sec` defaults per schema.
- **Cycle detection.** `on_artifact` triggers can loop (A→B→A). The bus needs cycle/debounce
  guards and a max fan-out per publish.
- **Cost.** More agents × more ticks × LLM calls. Collectors should prefer cheap/local models
  (pydantic-ai/ollama) and lean on routines for the heavy lifting; reserve frontier models
  for judgment-heavy Managers/Traders. The per-session `agent_key` override already supports
  this.
- **Schema governance.** Who owns `arb_opportunity_v1`? Propose a small `schemas/` registry
  with versioned JSON schemas; the bus validates on publish.
- **Permissioning across agents.** `call_agent` and `fleet.directive` are powerful — gate by
  capability + the existing role/admin model in `config_manager`.
- **Back-compat surface.** Confirm the Telegram `/agent` flow and `web/routes/agents.py`
  tolerate `kind != trader` everywhere they assume executors/PnL (e.g. performance compute).
- **Naming.** "Pentagon" is the UI inspiration (fleet/mission-control); the product is still
  Condor. Suggest "Condor Fleet" for the feature, "fleet view" for the page.

---

## 11. One-paragraph mental model (new)

> Condor is a **launcher and switchboard for a fleet of typed agents.** Each agent is a
> folder declaring a *kind* and *capabilities*; a tick loop is its heartbeat; routines and V2
> controllers are its deterministic muscle; the LLM tick is its judgment; the Journal /
> Snapshot / Learnings triad is its private memory; and **A2A artifacts are the shared
> bloodstream** — one agent publishes what it learned or decided, others consume it as input
> or are woken by it. Risk isolation by `controller_id` keeps trading agents from colliding;
> an Overseer keeps the whole fleet inside its limits. Condor just launches the agents and
> wires the pipes; the agents do the rest.

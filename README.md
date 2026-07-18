# Condor

**Create and run autonomous trading agents** — from an agentic harness
(Claude Code, Hermes Agent, OpenClaw) via MCP, or from a local web dashboard
used for setup and monitoring.

An agent is a markdown spec (`AGENT.md`): a role, a strategy, risk limits, a
model, and a venue account. Condor runs it on a tick loop — the LLM decides,
deterministic **executors** trade — with platform-enforced risk caps, durable
append-only records, and venue-truth crash recovery.

## How it works

```
   Web dashboard (local, no auth)        MCP harnesses (Claude Code, Hermes, OpenClaw)
                    \                                     /
                     +--------  AgentService  -----------+
                               /            \
                         Scheduler        Run history (append-only JSONL)
                              |
                         ModelRunner  (ACP)
                              |
                       ExecutionService  — the only supported trading-mutation path:
                              |            risk caps, leases, idempotent creates,
                              |            venue-truth reconciliation
                              |
                 Solana (Jupiter)  |  Hyperliquid  |  Polymarket
```

- **Deterministic executors, LLM decisions.** The model never holds an order
  ticket. It creates typed executors (`{order|position} × {spot|perp|pred}`)
  through `manage_executors`; the executor state machines place, poll,
  protect, and close — and survive process restarts by reconciling against
  venue truth.
- **Platform-enforced risk.** Agent-declared risk limits (max open executors,
  max exposure, drawdown) are enforced in the runtime create path — not by
  asking the model nicely. Launch overrides can only tighten them.
- **Dry-run first.** Experiments run the full loop and journal every decision
  without placing anything.
- **Human approval.** Mutating actions can be gated behind approval, surfaced
  in your harness chat or the dashboard.
- **Everything on disk is append-only JSONL** — executor records, run events,
  notifications — foldable after a crash, greppable forever.


## Development

```bash
make install
uv run pytest -q          # test suite
make run                  # run from source
cd frontend && npm run build   # build the dashboard
```

## Project structure

```
condor/
├── condor/
│   ├── agents/          # engine (tick loop), runstore, projections, prompts,
│   │   │                #   learnings, providers, consult/experiment
│   ├── executors/       # kind×instrument matrix, ops (risk-gated create), runtime,
│   │   │                #   append-only JSONL log, venue adapters
│   ├── control/         # unix-socket JSON-RPC surface for the MCP subprocess
│   ├── acp/             # Agent Client Protocol model runner
│   └── web/             # FastAPI app + dashboard routes
├── mcp_servers/condor/  # the MCP server harnesses connect to
├── frontend/            # React dashboard
├── agents/              # your agents live here
├── routines/            # generic read-only primitives (scanner, TA chart, …)
└── docs/simplification-plan.md   # the roadmap
```

## Venues

| Venue | Instruments | Notes |
|---|---|---|
| Solana (Jupiter) | spot swaps | memecoins, SOL pairs |
| Hyperliquid | perpetuals | native TP/SL, leverage |
| Polymarket | prediction markets | CLOB limit orders |

## Quickstart

```bash
git clone https://github.com/hummingbot/condor.git
cd condor
make install     # uv deps + interactive setup
make run         # start the Condor process (control socket + web dashboard)
```

Then either:

- **Harness (recommended):** register the Condor MCP server in your harness
  (`.mcp.json` is included for Claude Code) and talk to it — `"create an
  agent that trades SOL breakouts, dry-run it, then launch with $200 max
  exposure"`. Skills (`agent-builder`, playbooks) guide the flow.
- **Dashboard:** open the local web dashboard to create/monitor agents,
  watch executors, and review run history.

## Agents

An **agent** is a trading persona you define once in a plain-markdown file
(`AGENT.md`): what it trades and how it thinks (its strategy), the venue and
account it uses, and hard risk limits it can never exceed. You talk to it in
plain language — from your harness chat or the dashboard — and Condor turns
that request into the right kind of run.

### Putting an agent to work

There are three ways to engage an agent, but you never pick one by name — you
just say what you want and Condor routes it:

| You want to… | Say something like | Condor runs a… |
|---|---|---|
| Let the agent trade its own strategy | *"run the trender"*, *"start the market maker"* | **session** — its live tick loop, under its own risk limits |
| See what it would do, no real money | *"test it first"*, *"dry-run it"*, *"what would it do?"* | **experiment** — one simulated tick, reported back to you; nothing recorded |
| Ask it something, or have it act while you watch | *"is BONK tradeable right now?"*, *"have it open a small position"* | **consult** — it answers/acts now, and **you approve each trade** |

One quick tell if you're unsure: **its plan, or your task?** *"Run \<agent\>"*
means "be yourself" — a session. *"Have \<agent\> do \<one specific thing\>"*
is a one-off task — a consult. Steering words like *"…focused on SOL today"*
or *"…for 10 ticks"* just shape the same session; they don't make it a task.

Only **sessions** are runs: they're what you track, stop, and review in the
run history. An experiment's report and a consult's answer arrive in the chat
and that's the whole product — nothing to clean up afterward.

### Safety by default

- **Dry-run anything.** An experiment runs a full strategy tick and reports
  every decision it *would* have made without placing a single order — the
  way to trust an agent before it touches real money.
- **Risk limits are enforced, not requested.** Max exposure, max open
  positions, and drawdown caps are checked by the runtime on every trade; the
  model can't talk its way past them, and launch-time overrides can only make
  them *stricter*.
- **You can require approval.** Trades can be gated so each one asks you first
  (in your harness chat or the dashboard); if no one answers, the default is to
  decline.
- **Read-only routines.** Routines (scanners, TA charts, reports) only fetch
  and summarize data — every real trade goes through an executor.

### Under the hood

The rest of this section is architecture reference — skip it unless you're
hacking on Condor itself. Each agent is one `AGENT.md` spec (identity +
strategy body + `default_config` + `denomination` + optional `schedule:`),
validated and hashed (source + resolved) at every save and launch.

#### Runtime shape

- **AgentService** (`condor/agents/service.py`) — the ONE owner of CRUD +
  lifecycle: create/update/delete (tombstone), run, control
  (pause/resume/stop [--close]), shutdown (agent-scoped winddown), consult,
  experiment. Web routes, MCP tools, and control-socket handlers are thin
  adapters.
- **TickEngine** (`engine.py`) — one instance per run: pre-flight risk →
  `run_agent` (a fresh ACP client per tick, clean context window) →
  RunStore write-back. Engines are memory-only; a restart never resumes a
  run (§4.2) — executors survive independently. A run ends four ways:
  the agent declares its task complete (`complete_run(summary)` — graceful
  early exit before the `max_ticks` budget), the budget runs out, the risk
  kill-switch fires, or you stop it — and every ending posts a final summary
  report to chat (the session sibling of a consult's answer).
- **run_agent** (`run.py`) — the single execution primitive under tick /
  consult / experiment. ACP is the only model runner. Mints the run's
  execution capability (§6.2) and revokes it at run end.
- **RunStore** (`runstore.py`) — one self-contained folder per run at
  `agents/{slug}/runs/{run_id}/` (opaque ULID ids): a lifecycle JSONL
  (`run_started` / `permission` / `run_ended`) plus **one organized markdown
  file per tick** (`1.md`, `2.md`, …: tick started with prompt suffix +
  hash, every tool call in full, state snapshot, tick completion). Actual
  runs — sessions and scheduled fires — are ALL that persists; experiments
  and consults return their report/answer inline and leave nothing. All
  markdown is write-only (never parsed back); the engine's working context
  is in-memory (§4.2).
- **Approvals** (`approvals.py`) — durable permission events + one-use
  grants; resolved via `resolve_approval` from any channel; default deny on
  timeout; voided when the run dies.
- **Scheduler** (`scheduler.py`) — cron fires from `schedule:` specs deduped
  on the `scheduled_for` fire key; durable routine schedules in
  `store/schedules.json`. Missed fires are skipped, never backfilled.
- **Risk & guards** (`risk.py`, `policies.py`, `condor/executors/guards.py`)
  — declared caps enforced pre-flight and per tool call; launch overrides
  are stricter-only. Per-agent hard trading rules are **spec-declared entry
  guards** (`default_config.entry_guards`, e.g. memecoin_trender's
  post-stop-loss cooldown) enforced by vetted core primitives on the one
  executor-create path — the pattern for agent-specific custom logic.
- **Learning** — the promotion ladder (agent-builder skill,
  docs/insight-flow-simplification.md §7): run stream (automatic) →
  `learnings.md` (agent-promoted via `record_learning`; capped, errors when
  full so the agent consolidates) → `AGENT.md` (folded in via `update_agent`
  after an experiment validates it). Agents read the global user-memory
  index but never write memory.

#### Attribution

Every executor an agent creates carries `agent_slug` (who) + `agent_id`
(which run — the RunStore ULID). Exposure, PnL, and stop scopes key on
those. Venue positions are NOT partitioned by attribution on netted venues
(Hyperliquid holds one net position per coin per account) — don't run
overlapping account/instrument ownership; the lease manager (§6.2b)
rejects a second Condor actor on the same (account, instrument).

#### On-disk layout

```
agents/{slug}/
    AGENT.md           # the one spec (incl. entry_guards, risk baseline)
    runs/{ulid}/       # one self-contained folder per session/scheduled run —
                       # the ONLY run history (experiments/consults persist
                       # nothing)
        store.jsonl    # lifecycle stream: run_started / permission / run_ended
        1.md, 2.md, …  # one file per tick: tick started (prompt suffix +
                       #   sha256 of the full assembled prompt), tool calls
                       #   in full, state snapshot, tick completion
        prompt.md      # the run's prompt prefix, written once by run_agent
        journal.md     # one line per tick (generated view — never parsed)
    executors.jsonl    # append-only executor lifecycle log. AGENT-scoped,
                       # NOT per-run: executors outlive runs (detach on stop,
                       # keep writing after run_ended), recovery rebuilds the
                       # open set from it, and cross-run readers (SL-cooldown
                       # guard, venue truth, performance) fold it whole.
                       # Entries attribute to runs via agent_id (the ULID).
    learnings.md       # curated learnings, ≤40 (record_learning; consolidate
                       # with replaces= when full — never silently evicted)
    skills/  routines/ # playbooks + read-only data scripts
```

User memory lives ONLY at the repo-root `store/memory/` (global, chat-curated;
agents read its index as `[USER MEMORY]`).

The exact prompt for tick N is reconstructible (and sha256-verifiable) as:
`prompt.md` + the tick's `prompt_suffix` + its acked `directive` events.
Mutable prompt inputs (learnings / user memory / skills index) are recorded
on the stream as `context_changed` events — a baseline at tick 1, then only
when their content actually changes.

## Status / roadmap

The platform is mid-simplification (see `docs/simplification-plan.md`): the
legacy Telegram bot and Hummingbot integration that Condor grew out of are
being removed; AgentService, ExecutionService, and a hardened run store are
being consolidated. The agent platform described above is the product; the
legacy surfaces still in the tree are scheduled for deletion.

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
│   │   │                #   learnings, providers, consult/delegate
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

Autonomous LLM-driven trading agents. Each agent is one `AGENT.md` spec —
identity + strategy body + `default_config` + `denomination` + optional
`schedule:` — validated and hashed (source + resolved) at every save and
launch (§5.3 of docs/simplification-plan.md).

Four primitives operate an agent:

| Primitive | What it does |
|---|---|
| `consult` | ask the agent a question inline — no state, no disk |
| `delegate` | one-off background task, pings you when done |
| `experiment` | dry-run session — full loop, zero venue calls |
| `session` | live stateful run on a tick schedule |

**Routines are read-only by role**: they provide data that agents/executors
consume and generate reports for humans. All execution goes through
ExecutionService via executors.

### Runtime shape

- **AgentService** (`condor/agents/service.py`) — the ONE owner of CRUD +
  lifecycle: create/update/delete (tombstone), run, control
  (pause/resume/stop [--close]), shutdown (agent-scoped winddown), consult,
  delegate. Web routes, MCP tools, and control-socket handlers are thin
  adapters.
- **TickEngine** (`engine.py`) — one instance per run: pre-flight risk →
  `run_agent` (a fresh ACP client per tick, clean context window) →
  RunStore write-back. Engines are memory-only; a restart never resumes a
  run (§4.2) — executors survive independently.
- **run_agent** (`run.py`) — the single execution primitive under tick /
  delegation / consult. ACP is the only model runner. Mints the run's
  execution capability (§6.2) and revokes it at run end.
- **RunStore** (`runstore.py`) — one append-only JSONL event stream per run
  at `agents/{slug}/runs/{run_id}.jsonl` (opaque ULID ids). Markdown views
  are generated (the run's `prompt.md` + `journal.md` companions and
  on-demand exports — never parsed back); working context is a projection
  (`projections.py`).
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

### Attribution

Every executor an agent creates carries `agent_slug` (who) + `agent_id`
(which run — the RunStore ULID). Exposure, PnL, and stop scopes key on
those. Venue positions are NOT partitioned by attribution on netted venues
(Hyperliquid holds one net position per coin per account) — don't run
overlapping account/instrument ownership; the lease manager (§6.2b)
rejects a second Condor actor on the same (account, instrument).

### On-disk layout

```
agents/{slug}/
    AGENT.md           # the one spec (incl. entry_guards, risk baseline)
    runs/{ulid}.jsonl  # RunStore event streams (incl. per-tick prompt
                       # suffix + sha256 of the full assembled prompt)
    runs/{YYYY-MM-DD_HH-MM-SS}Z.artifacts/  # run companion dir, named for the
        prompt.md      # frozen prompt prefix, written once at run start
        journal.md     # one line per tick (generated view — never parsed)
        {HH-MM-SS}Z-{type}.md  # oversized event payloads (spill, markdown)
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

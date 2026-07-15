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

```
agents/{slug}/
├── AGENT.md          # the spec: role, strategy, risk limits, model, venue account
├── skills/           # agent-scoped playbooks
├── routines/         # agent-authored read-only data/report scripts
└── runs, journals    # append-only history
```

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

## Project structure

```
condor/
├── condor/
│   ├── agents/          # engine (tick loop), config, journal, providers, consult
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

## Status / roadmap

The platform is mid-simplification (see `docs/simplification-plan.md`): the
legacy Telegram bot and Hummingbot integration that Condor grew out of are
being removed; AgentService, ExecutionService, and a hardened run store are
being consolidated. The agent platform described above is the product; the
legacy surfaces still in the tree are scheduled for deletion.

## Development

```bash
make install
uv run pytest -q          # test suite
make run                  # run from source
cd frontend && npm run build   # build the dashboard
```

## Support

- **Issues**: https://github.com/hummingbot/condor/issues

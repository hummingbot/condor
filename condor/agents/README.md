# Agent Core

Autonomous LLM-driven trading agents. Each agent is one `AGENT.md` spec —
identity + strategy body + `default_config` + `denomination` + optional
`schedule:` — validated and hashed (source + resolved) at every save and
launch (§5.3 of docs/simplification-plan.md).

## Runtime shape

- **AgentService** (`service.py`) — the ONE owner of CRUD + lifecycle:
  create/update/delete (tombstone), run, control (pause/resume/stop
  [--close]), shutdown (agent-scoped winddown), consult, delegate. Web
  routes, MCP tools, and control-socket handlers are thin adapters.
- **TickEngine** (`engine.py`) — one instance per run: pre-flight risk →
  `run_agent` (a fresh ACP client per tick, clean context window) →
  RunStore write-back. Engines are memory-only; a restart never resumes a
  run (§4.2) — executors survive independently.
- **run_agent** (`run.py`) — the single execution primitive under tick /
  delegation / consult. ACP is the only model runner. Mints the run's
  execution capability (§6.2) and revokes it at run end.
- **RunStore** (`runstore.py`) — one append-only JSONL event stream per run
  at `agents/{slug}/runs/{run_id}.jsonl` (opaque ULID ids). Markdown views
  are generated exports (`exports.py`); working context is a projection
  (`projections.py`).
- **Approvals** (`approvals.py`) — durable permission events + one-use
  grants; resolved via `resolve_approval` from any channel; default deny on
  timeout; voided when the run dies.
- **Scheduler** (`scheduler.py`) — cron fires from `schedule:` specs deduped
  on the `scheduled_for` fire key; durable routine schedules in
  `store/schedules.json`. Missed fires are skipped, never backfilled.
- **Risk** (`risk.py`, `policies.py`) — declared caps enforced pre-flight
  and per tool call; launch overrides are stricter-only.

## Attribution

Every executor an agent creates carries `agent_slug` (who) + `agent_id`
(which run — the RunStore ULID). Exposure, PnL, and stop scopes key on
those. Venue positions are NOT partitioned by attribution on netted venues
(Hyperliquid holds one net position per coin per account) — don't run
overlapping account/instrument ownership; the lease manager (§6.2b)
rejects a second Condor actor on the same (account, instrument).

## On-disk layout

```
agents/{slug}/
    AGENT.md           # the one spec
    runs/{ulid}.jsonl  # RunStore event streams (+ .artifacts/)
    executors.jsonl    # append-only executor lifecycle log
    learnings.md       # flat curated learnings
    store/memory/      # agent-scoped memory
    skills/  routines/ # playbooks + read-only data scripts
```

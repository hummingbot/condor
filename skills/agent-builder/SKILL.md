---
name: agent-builder
description: "Create, consult, test, launch, monitor, update, and safely delete Condor agents using the current typed MCP tools."
compatibility: "Requires the Condor MCP server (mcp__condor__* tools)"
metadata: {"condor-source": "builtin", "condor-created": "2026-06-18"}
---

# Agent builder

Use this skill when the user wants to create or operate a Condor agent. An agent has
one durable specification at `agents/{slug}/AGENT.md`. The same agent can be consulted
for an answer and run on a tick loop; there is no separate strategy or bot object.

## Operating rule

Operate Condor only through the connected Condor MCP tools. Do not import Condor's
Python modules, edit runtime stores, or invoke private HTTP/control endpoints from the
host assistant. Repository file paths in this guide explain ownership and persistence;
they are not an alternate control surface.

## Build progressively

1. Establish the agent's role and the situations in which it should be consulted.
2. Create the smallest useful read-only agent and consult it once.
3. Add agent-scoped routines when the agent needs structured data.
4. Add a bounded experiment before any live autonomous run.

Do not begin with a long questionnaire. Ask only for decisions that materially change
the safety or usefulness of the agent.

## Create

Call `create_agent` with the role in `name`, a concise `description`, a precise
`when_to_consult`, and a short system prompt in `instructions`.

Tool scope is enforced by the platform. An empty `tools` list is unrestricted and can
therefore reach execution. A trading-capable agent must have `risk_limits` and a
`denomination`. To make an agent explicitly read-only, omit `manage_executors` from a
nonempty tool allowlist. If it can trade, bind `account` to a configured display name or
custody address; Condor resolves and persists the canonical custody address.

Example:

```text
create_agent(
  name="Market Regime Analyst",
  description="Classifies the current market regime from structured observations",
  when_to_consult="When the user asks whether conditions are trending or ranging",
  agent_key="claude-code",
  tools=["manage_routines", "manage_memory"],
  instructions="You are a market-regime specialist. Lead with the classification..."
)
```

After creation, call `consult(agent="<slug>", task="...")` with a realistic question.
If the answer exposes a prompt problem, update only the necessary fields with
`update_agent` and consult again.

## Add routines

Use `manage_routines` for agent-scoped data processing:

```text
manage_routines(action="create", agent_slug="<slug>", name="regime_inputs", code="...")
manage_routines(action="run", agent_slug="<slug>", name="regime_inputs", config={...})
```

Inspect the result, refine the routine, then update the agent instructions so they name
the routine and explain how to interpret its output. Keep exchange access and secrets in
the platform interfaces, not in agent markdown or routine source.

## Configure autonomous runs

Autonomous behavior belongs in the same AGENT.md body and `default_config`. Define the
tick objective, inputs, decision rules, risk boundaries, and error handling. If the agent
can call `manage_executors`, fetch the current executor schema before writing executor
examples and include the exact required fields.

Risk limits are an authored baseline. Launch-time risk overrides may only tighten that
baseline. Launch overrides are limited to trading context, maximum tick count,
experiment mode, and stricter risk limits; change model, frequency, account, or strategy
behavior with `update_agent`, where it becomes part of the durable specification.

Always test trading behavior with a bounded experiment:

```text
run_agent(
  agent_slug="<slug>",
  dry_run=true,
  trading_context="Observe SOL/USDC on the configured venue",
  config={"max_ticks": 1}
)
```

Use `get_run(run_id="...", include_events=true)` to verify the decision, tool attempts,
and risk state. An experiment must not place or cancel venue orders.

For a live run, call `run_agent` without `dry_run`, preferably with a small authored or
launch-time `max_ticks` for the first session. Never widen risk at launch.

## How agents learn (the promotion ladder)

Agent knowledge climbs three rungs, each an explicit judgment
(docs/insight-flow-simplification.md §7):

1. **Run stream** (`agents/{slug}/runs/*.jsonl`) — everything, recorded
   automatically every tick. Nothing to manage.
2. **`learnings.md`** — the agent itself promotes a durable operational fact at the
   moment of insight via `record_learning`; capped at 40, and when full the tool
   errors so the agent consolidates (`replaces=`) instead of losing old knowledge.
3. **AGENT.md** — when a learning proves out across runs, fold it into the spec
   body with `update_agent`, validated FIRST by a `dry_run=true` experiment. The
   spec is hashed on every save, so this evolution is auditable. A learning in the
   spec *is* the agent; a learning in the list must be re-read and re-believed
   every tick.

Agents never write user memory — `[USER MEMORY]` is read-only advisory context from
the global chat-curated store; user facts are saved from chat with `manage_memory`.

## Monitor and control

- `list_agents()` lists definitions and live summaries.
- `get_agent(agent_slug="...")` returns the current durable specification.
- `list_runs(agent_slug="...")` lists sessions, experiments, consults, and delegations.
- `get_run(run_id="...", include_events=true)` returns one durable event stream.
- `manage_executors(action="snapshot", agent_id="...")` shows attributed orders and
  inventory for a run.
- `control_run(run_id="...", verb="pause|resume|stop", close=false)` controls one run.
- `shutdown_agent(agent_slug="...")` performs an agent-scoped emergency winddown.

Stopping with `close=false` preserves attributed inventory; `close=true` requests closure
of Condor-owned inventory only. Confirm durable state with `get_run` and an executor
snapshot rather than relying on an in-memory status indicator.

## Delete safely

Call `delete_agent` only after stopping its runs and resolving its attributed financial
scope. Deletion is a tombstone: history remains readable and the slug is not reusable.
The service rejects deletion when durable executors, live orders, or nonzero attributed
inventory remain.

## Writing guidance

Keep `when_to_consult` action-oriented: name the verbs and nouns a user will say and
state boundaries where adjacent agents overlap. Keep instructions compact and explicit:
role, exclusions, required evidence, response shape, and safety rules. Do not put mutable
launch details in the prompt when they belong in `default_config` or `trading_context`.

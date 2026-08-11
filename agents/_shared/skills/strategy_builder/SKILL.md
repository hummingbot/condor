---
name: strategy_builder
description: Give YOURSELF a loop — write a tick playbook (a strategy) under your own slug, dry-run it, and start it. The shared contract for how a strategy is authored, validated and launched; read by any agent that wants to act autonomously instead of only being consulted.
when_to_use: You are an agent and the user wants you to act on a loop — run every N seconds, watch a condition, report on a schedule, or trade autonomously. Also read this before editing or re-launching a strategy you already own. NOT for creating or deleting other agents (that is Condor's agent_builder).
created: '2026-08-11T00:00:00Z'
source: chat
---

# Strategy Builder

You are an **agent**. You can already be consulted, delegated to, and looped — from the
moment you existed. This playbook covers the last of those: giving your loop a **specific
tick playbook** instead of the generic default.

A **strategy** is a tick system prompt the engine runs in a **session**, at a frequency
the user sets. It lives at `agents/{your_slug}/strategies/{strategy_slug}/strategy.md` and
it is yours — you author it, under your own slug.

> **Scope.** This is about giving *yourself* a loop. Creating, editing or deleting *other*
> agents belongs to Condor (`agent_builder`). If the user wants a whole new specialist,
> say so and let them ask Condor.

## When a strategy is worth it

`start_agent(strategy_id="<your_slug>")` already works with no strategy — it ticks a
default playbook driven by your AGENT.md. That default is deliberately generic. Write a
strategy when the loop needs to be **specific and disciplined**: a fixed analysis order, a
decision rule, executor schemas, risk limits.

**The loop does NOT have to trade.** Define the tick task however the user wants:

- read a routine's output and **decide whether to trade** (create/stop executors),
- or just **send a report / notification**,
- or watch a condition and act only when it's met.

## Step 1 — Prepare (only if it trades)

BEFORE writing the strategy, fetch the schema for every executor type the loop will use —
`manage_executors(executor_type="grid_strike")`, etc. — and embed the required
fields/types directly into the instructions. The tick LLM has no other way to learn them.
Same for any controller config it manages (`manage_controllers`).

If the loop should reason over structured data, make sure the routine exists first
(`manage_routines(action="list", agent="<your_slug>")`). Routines are agent-scoped, so any
strategy you own can call them. Need a new one? Do NOT write it inline — hand it to a
background worker: `delegate(action="start", agent="condor", task="build a routine that …
for agent <your_slug>")`.

## Step 2 — Create the strategy

```
manage_trading_agent(
    action="create_strategy",
    agent_slug="<your_slug>",              # yourself
    name="BRL MM",
    description="…",
    instructions="<tick system prompt>",
    # agent_key omitted → inherits your model; overridable at launch
    config={"connector_name": "binance", "frequency_sec": 60,
            "total_amount_quote": 100, "execution_mode": "loop"}
)
```

Returns the composite key `"<your_slug>.<strategy_slug>"` — that is the `strategy_id` for
everything after.

The tick instructions MUST include:

- **Objective** — what one tick is for.
- **Analysis** — which routine to call *by name* and how to read its output.
- **Decision logic** — act / report / hold, with the condition for each.

…and, only if it trades:

- **Executor config** — the FULL schema: every required field, type, range, ordering rule.
- **Parameter inference** — how to derive prices/side/TP from routine output + market data.
- **Risk rules** — max position, position limits, stop behaviour.
- **Error recovery** — on a failed create, re-fetch the schema, fix, retry once, journal it.

**Generic vs specific:**
- GENERIC (default): pair/connector are NOT in the instructions — they arrive at launch via
  `trading_context`. Refer to "the configured trading pair"; keep a sensible `default_config`.
- SPECIFIC: pair/connector baked in (e.g. an ETH/BTC ratio play).

## Step 3 — Dry run before live (if it trades)

```
manage_trading_agent(action="start_agent", strategy_id="<your_slug.strategy_slug>",
    config={"execution_mode": "dry_run",
            "trading_context": "Trade BTC-USDT on binance_perpetual",
            "frequency_sec": 60, "total_amount_quote": 100,
            "risk_limits": {"max_position_size_quote": 200, "max_open_executors": 3}})
```

Review with `trading_agent_journal_read(agent_id=…, section="run:1")`: routines called
right, decision logic sound, conditional language ("would place…"), no real create/stop
calls, risk rules respected. Do not go live until the user is satisfied.

> A `dry_run` / `run_once` is an **experiment** — it writes a snapshot, not a journal
> session. Read it back the same way; don't expect a numbered run to persist.

## Step 4 — Go live

Offer `run_once` (single live tick), `loop` (continuous), or `loop` + `max_ticks`. Confirm
the model, start it, confirm it's running, and give the user the monitoring commands.
**Always include risk limits when the loop can trade.**

## Monitoring what you own

1. `manage_trading_agent(action="list_agent_definitions")` — agents and the strategies they own.
2. `manage_trading_agent(action="list_agents")` — running loop instances.
3. `manage_trading_agent(action="agent_status", agent_id=…)` — instance status.
4. `trading_agent_journal_read(agent_id=…, section="summary"|"runs"|"run:N")`.

## Reference

**Model:** the strategy's `agent_key` defaults to yours; override per launch with
`config={"agent_key": "…"}`. Never invent one — call `get_available_models` and pick from
what the operator actually has, or leave it inherited. A pydantic-ai key
(`ollama:`/`openai:`/`groq:`/`lmstudio:`/`openrouter:`/`custom@…`) enforces the `tools`
allowlist; an ACP key (`claude-code`/`claude-acp`/`gemini`/`copilot`) runs unrestricted,
with mutations still confirmation-gated.

**Server:** leave `server_name` empty unless the user pins it. A strategy you own runs on
whichever server your agent resolves.

**Editing & deleting:** `update_strategy(strategy_id=…, instructions=…)` to revise the tick
playbook; `delete_strategy(strategy_id=…)` to remove it. Stop any running instance first.

## Rules

- Author strategies **under your own slug only**. Another agent's fleet is not yours to edit.
- Fetch executor/controller schemas BEFORE writing instructions that use them.
- One strategy per loop job — don't overload a tick with unrelated objectives.
- A loop doesn't have to trade. When it does: risk limits always, dry run always.
- Show the user the dry-run journal before proposing to go live.
- Don't write routine code inline — delegate it to a Condor worker.

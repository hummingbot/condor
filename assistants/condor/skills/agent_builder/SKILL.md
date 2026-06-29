---
name: agent_builder
description: Create and operate autonomous trading agents the minimal way — create the agent from just its role + purpose, prove it's alive by consulting it, then progressively improve it with routines and (optionally) a loop strategy.
when_to_use: The user wants to create, edit, dry-run, launch, monitor, or delete an autonomous trading agent — whether it's used purely by consulting it or also runs a strategy on a loop.
created: 2026-06-18
source: builtin
---

You are helping the user build or operate an **autonomous trading agent**. Agents live
under `agents/{slug}/` and are distinct from you (the interactive Condor assistant). You
drive them via `manage_trading_agent`, `manage_routines`, `trading_agent_journal_read`,
and `consult`.

## Mental model — start minimal, improve in layers

An **Agent** is a specialist with an **essence**: a domain it understands and a role it
plays. It is defined in `agents/{slug}/AGENT.md` (its brain/system prompt). There is only
ONE kind of thing — an Agent. "Expert" is not a separate type; it's just an agent being
consulted.

The whole point of this skill is to build the agent in the **smallest useful step first,
then layer capability on only when the user wants it.** Do NOT front-load routines,
strategies, executors, or model questions. The progression is:

1. **Create the agent from just its role + what it's for.** Nothing else required. The
   moment it exists it is already consultable.
2. **Consult it to prove it's alive.** Ask it something inside its specialty and show the
   answer. This is the agent working end-to-end.
3. **Improve it with routines** — give it structured market data of its own. Define one,
   create it, run it, look at the output together. This is what turns a guessing LLM into
   a real specialist.
4. **(Optional) Let it run on a loop** — a strategy the engine runs on a tick. The loop
   does NOT have to trade: it can read a routine's output and decide to trade, send a
   report, or do nothing — at a frequency the user sets.

Each layer is independently valuable. Most agents are worth creating and consulting long
before they ever get a routine, and many never need a loop at all.

```
agents/{slug}/
  AGENT.md                         # identity + role (the brain) — step 1
  routines/*.py                    # agent-scoped analysis scripts — step 3
  skills/{name}/SKILL.md           # the agent's own reusable playbooks
  strategies/{slug}/strategy.md    # OPTIONAL loop playbook — step 4
  sessions/session_N/              # run journals/snapshots (created at runtime)
```

Label each message with the current step, e.g. `[Step 2 — Consult it]`.

## Step 1 — Create the agent (minimal)

When the user asks to create an agent, do NOT open with a config questionnaire
(exchange, pair, strategy, model…). In a sentence, frame how agents work here (create →
consult → improve with routines → optionally loop), then settle just two things in a
short conversation:

- **Role / domain** — what is this agent the specialist in? (e.g. "spread & inventory
  judgment for BRL market making", "executor selection for a given regime")
- **What it's used for** — the kind of question Condor should be able to ask it. This
  becomes `when_to_consult`.

That's enough to create it. Pick a sensible default model (easiest thing to change
later) and create:

```
manage_trading_agent(
    action="create_agent",
    name="Executor Manager",
    description="Expert in deploying and tuning Hummingbot executors",
    agent_key="ollama:qwen3:32b",          # default model; change anytime
    when_to_consult="When the user wants to deploy, tune, or stop an executor",
    tools=[],                              # leave open unless the user named tools
    instructions="<AGENT.md body — the agent's system prompt>"
)
```

The **AGENT.md body (`instructions`)** is the brain — write it as the agent's own system
prompt, kept tight: **who it is** (its domain + what it explicitly does NOT handle),
**what it knows** (durable domain knowledge), and **how it answers** (lead with the
recommendation, key: value not prose). You can keep it short now and enrich it later with
`update_agent`. Note it owns scoped memory (`manage_memory`) and skills (`manage_skill`).

`create_agent` returns `agent_slug` — use it for everything after.

Then tell the user plainly: **the agent is created. Now let's consult it to check it's
alive.**

## Step 2 — Consult it to prove it's alive

Test it the way Condor will use it:

```
consult(agent="<agent_slug>", task="…a real question in its specialty…", context="…")
```

Show the answer. This proves the agent runs end-to-end. If the persona or answer is off,
fix the AGENT.md with `update_agent(agent_slug=…, instructions=…)` and consult again.

When the consult looks good, **stop and tell the user the agent already works as a
consultable expert** — and that the next way to make it sharper is to give it routines so
it reasons over real structured data instead of guessing.

## Step 3 — Improve it with routines

A routine is how the agent pre-processes raw market data into the specific view its
specialty needs (a band scanner, a regime classifier, an inventory snapshot). Offer this
as the upgrade, then guide the user through it one routine at a time:

1. **Define** — agree on what this routine should output and why the agent needs it.
2. **Create** — write it with the `routine_builder` skill for the API. Routines live at
   the agent level and are shared across consults and any future loop. Pass the **agent
   slug** as `strategy_id` (it accepts a bare agent slug or `agent_slug.strategy_slug`):
   ```
   manage_routines(action="create_routine", strategy_id="<agent_slug>",
                   name="band_scanner", code="<python>")
   ```
3. **Analyze the output** — run it and read it together; iterate until it's clean and
   useful:
   ```
   manage_trading_agent(action="run_routine", strategy_id="<agent_slug>",
                        name="band_scanner", config={…})
   ```

Then update the AGENT.md so the agent knows to call the routine by name and how to read
it, and consult it again to confirm it now reasons over that data. Repeat for each
routine the agent needs. **Stop here unless the user wants the agent to act on its own on
a loop.**

## Step 4 — (Optional) Let it run on a loop

Only if the user wants the agent to act autonomously: a **strategy** is the tick playbook
the engine runs in a **session**. Make clear the loop does NOT have to trade — define the
tick task however the user wants:

- read routine X's output and **decide whether to trade** (create/stop executors),
- or just **send a report / notification**,
- or watch a condition and act only when it's met,

…running at a **frequency the user sets** (`frequency_sec`).

If the loop creates executors, BEFORE writing the strategy fetch the schema for every
executor type it will use — `manage_executors(executor_type="grid_strike")`, etc. — and
embed the required fields/types directly into the instructions; the tick LLM has no other
way to learn them. Same for any controller config it manages (`manage_controllers`).

```
manage_trading_agent(
    action="create_strategy",
    agent_slug="<agent_slug>",             # the agent must already exist
    name="BRL MM",
    description="…",
    instructions="<tick system prompt>",
    agent_key="ollama:qwen3:32b",          # default model; overridable at launch
    config={"connector_name": "binance", "frequency_sec": 60,
            "total_amount_quote": 100, "execution_mode": "loop"}
)
```

Strategy instructions (the tick system prompt) MUST include: **Objective**; **Analysis**
(which routine to call by name and how to read it); **Decision logic** (act / report /
hold); and — only if it trades — an **Executor config** with the FULL schema (every
required field, type, range, ordering rule), **Parameter inference** (how to derive
prices/side/TP from routine output + market data), **Risk rules** (max position, position
limits, stop behaviour), and **Error recovery** (on a failed create, re-fetch the schema,
fix, retry once, journal it).

**Dry run before live** (if it trades):
```
manage_trading_agent(action="start_agent", strategy_id="<agent_slug.strategy_slug>",
    config={"execution_mode": "dry_run", "agent_key": "ollama:llama3.1",
            "trading_context": "Trade BTC-USDT on binance_perpetual",
            "frequency_sec": 60, "total_amount_quote": 100,
            "risk_limits": {"max_position_size_quote": 200, "max_open_executors": 3}})
```
Review with `trading_agent_journal_read(agent_id=…, section="run:1")`: routines called
right, decision logic sound, conditional language ("would place…"), no real create/stop
calls, risk rules respected. Don't go live until the user is satisfied.

**Go live:** offer `run_once` (single live tick), `loop` (continuous), or `loop` +
`max_ticks`. Confirm the live model, start, confirm it's running, give monitoring
commands. Always include risk limits when a loop agent can trade.

## Monitoring existing agents
1. `manage_trading_agent(action="list_agent_definitions")` — all agents + capabilities
   (consultable, loopable, owned strategies). Only list that shows consult-only agents.
2. `manage_trading_agent(action="list_agents")` — running loop instances.
3. `manage_trading_agent(action="agent_status", agent_id=…)` — instance status.
4. `trading_agent_journal_read(agent_id=…, section="summary"|"runs"|"run:N")`.

## Reference

**Consultable rule:** `consultable = when_to_consult is set` — any model. The model only
changes *how* the consult runs: a pydantic-ai key (`ollama:…`/`openai:…`/`groq:…`/
`lmstudio:…`) enforces the `tools` allowlist; an ACP key (`claude-code`/`gemini`/
`copilot`) runs unrestricted, with mutations still confirmation-gated. A `claude-code`
loop agent is consultable too — set `when_to_consult` and it shows up for Condor.

**Model selection:** Set per session, not baked in. The agent/strategy `agent_key` is the
default; override at launch via `config={"agent_key": "…"}`.
- ACP (subprocess CLI): `claude-code`, `gemini`, `copilot`
- Pydantic-AI local: `ollama:llama3.1`, `ollama:qwen3:32b`, `lmstudio:<model>`
- Pydantic-AI cloud: `openai:gpt-4o`, `groq:llama-3.3-70b-versatile`
- Custom endpoint: `openai:<model>` + `model_base_url` in config.
Default URLs: Ollama=localhost:11434, LM Studio=localhost:1234.

**Generic vs Specific strategies:**
- GENERIC (default): pair/connector are NOT in the instructions — passed at launch via
  `trading_context`. Refer to "the configured trading pair"; keep sensible `default_config`.
- SPECIFIC: pair/connector baked into the instructions (e.g. an ETH/BTC ratio play).

**Agent tools, memory & skills:** `tools` on the AGENT.md is a tool-name allowlist
enforced on pydantic-ai consults and loops (empty = unrestricted; not enforceable on ACP
keys). An agent keeps its own domain memory (`manage_memory`) and reusable playbooks
(`manage_skill`/`agents/{slug}/skills/`) — the agent OWNS skills; it is not itself a skill.

**Editing & deleting:** read the current brain with `get_agent(agent_slug=…)`, edit with
`update_agent(agent_slug=…, instructions=…)`. `delete_agent` refuses while the agent still
owns strategies — delete those first (`delete_strategy`).

## Rules
- **Minimal first.** Create the agent from just role + purpose; never open with a config
  questionnaire. Layer routines and loops on only when the user wants them.
- After creating, immediately steer to a **consult** to prove it's alive before anything
  else.
- Only the step label as a header. Be direct; status as key: value.
- Every agent is consultable (always set `when_to_consult`).
- Create the AGENT.md FIRST — routines and strategies require an existing agent_slug.
- One routine per analysis task; run it and show the output before moving on.
- A loop doesn't have to trade — it can report or watch. Always include risk limits when
  it can trade, and dry-run before going live.
- Guide one step at a time and offer concrete proposals.

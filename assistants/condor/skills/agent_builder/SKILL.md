---
name: agent_builder
description: Create and operate autonomous trading agents — define an agent's essence + routines (AGENT.md), which Condor can always consult, then optionally give it a strategy so it can loop, dry-run, and go live.
when_to_use: The user wants to create, edit, dry-run, launch, monitor, or delete an autonomous trading agent — whether it's used purely by consulting it or also runs a strategy on a loop.
created: 2026-06-18
source: builtin
---

You are helping the user build or operate an **autonomous trading agent**. Agents
live under `agents/{slug}/` and are distinct from you (the interactive Condor
assistant). You drive them via `manage_trading_agent`, `manage_routines`,
`trading_agent_journal_read`, and `consult`.

## Mental model — an Agent is an area of expertise, not a strategy

Before touching any tool, get this idea across to the user, because it shapes everything
that follows: **an Agent is a specialist with an essence — a domain it understands
deeply and processes the market through.** You are not configuring a bot; you are
defining *who this expert is and how it sees the world.*

An **Agent** is the brain/identity, defined in `agents/{slug}/AGENT.md`. There is only
ONE kind of thing — an Agent. "Expert" is not a separate type; it's just an agent being
consulted. Everything else hangs off the essence. An agent has two capabilities, and it
can have either or both:

- **Be consulted** — Condor (or you) asks it for specific judgments inside its
  specialty: "is this band safe?", "should I tighten spreads?", "which executor type
  fits this regime?". It runs its own brain to completion, pulls its routines/tools, and
  returns an answer. It does NOT act on its own. **Every agent is consultable** as long
  as it has a `when_to_consult` trigger — regardless of model. A pydantic-ai `agent_key`
  (`ollama:…`/`openai:…`/`groq:…`) runs the consult with its tool allowlist enforced; an
  ACP key (`claude-code`/`gemini`/`copilot`) runs it unrestricted, with every mutation
  still gated by the user's confirmation.
- **Run a strategy on a loop** — the agent is instantiated in a **session** and runs on
  a tick via the engine. Inside that session it can: run its **routines** to pre-process
  and read the market, use its **tools** to fetch data, **create/stop executors**, and
  **create/modify/stop the controllers it owns**. This is how the agent acts, not just
  advises. (Mechanically: it owns ≥1 **strategy** under
  `agents/{slug}/strategies/{strategy_slug}/strategy.md`.) A loop agent with a
  `when_to_consult` trigger is *also* consultable — same brain, two ways in.

**The core of a great agent is its essence + its routines — build those first.** The
routines are how the agent pre-processes raw market data into the specific view its
specialty needs (a band scanner, a regime classifier, an inventory snapshot). Without
that, both consulting and looping are just an LLM guessing. So the build order is:

**define the essence (AGENT.md) → give it routines that encode how it reads the market →
THEN, only if it should act autonomously, add a strategy so it can loop.**

A strategy is an add-on, not the starting point. Most agents are worth building and
using as consultable experts long before they ever loop.

```
agents/{slug}/
  AGENT.md                         # identity + domain knowledge (the brain)
  routines/*.py                    # agent-scoped analysis scripts (shared)
  skills/{name}/SKILL.md           # the agent's own reusable playbooks
  strategies/{slug}/strategy.md    # OPTIONAL — only loop agents have these
  strategies/{slug}/learnings.md
  sessions/session_N/              # run journals/snapshots (created at runtime)
```

## Creation Workflow

Label each message with the current phase, e.g. `[Phase 2 — Create Agent]`. Phases
1–4 build a working, consultable agent; phases 5–7 add a strategy so it can also loop.

### Phase 1 — Design (conversation only, no tools)

**Open by explaining, not interrogating.** When the user asks to create an agent, do
NOT dump a list of config questions (expert-vs-loop, exchange, pair, strategy style,
model) on them. That puts the cart before the horse. Instead, in a few sentences, frame
how agents work here (use the Mental model above in your own words):

- An agent is defined by its **essence** — its area of expertise, the slice of the
  market it specializes in and how it interprets it. That is what we design first.
- Once it has an essence, Condor can **consult** it for the specific judgments it's a
  specialist in. And it can optionally own **strategies** that run on a loop — in a
  session where it runs routines, fetches data, and creates/stops executors and
  controllers it manages.
- The core that makes an agent good is its **essence and its routines** — how it
  pre-processes and understands market data in its own specific way. We nail those
  first; a looping strategy is an add-on we add later only if it should act on its own.

Then have a focused conversation to draw out the **essence**, one thread at a time —
not a form to fill in:
- What is this agent the specialist in? What market view or judgment is it the expert
  on? (For an MM agent: spreads, inventory skew, band safety, regime — what's its angle?)
- What questions should Condor be able to ask it? (→ `when_to_consult` — always set this;
  every agent is consultable)
- What does it need to look at to answer well? (→ the routines that pre-process the
  market for it, and the `tools` it needs)

Only after the essence is clear, and only if the user wants the agent to act on its
own, move on to strategy specifics (logic, entry/exit, risk params, timeframe, GENERIC
vs SPECIFIC — see Reference). Settle the model last; it's the easiest thing to change.

Propose a written summary of the essence and defaults. Do NOT proceed until the user
approves.

### Phase 2 — Create the Agent (AGENT.md)
Create the identity first — routines and strategies hang off it.

```
manage_trading_agent(
    action="create_agent",
    name="Executor Manager",
    description="Expert in deploying and tuning Hummingbot executors",
    agent_key="ollama:qwen3:32b",          # pydantic-ai (allowlist enforced on consult)
    when_to_consult="When the user wants to deploy, tune, or stop an executor",
    tools=["manage_executors", "get_market_data", "get_portfolio_overview"],
    instructions="<AGENT.md body — see below>"
)
```

Returns `agent_slug` — use it for everything that follows.

**The AGENT.md body (`instructions`) is the agent's brain.** Write it as the agent's
own system prompt, not a description. Include:
1. **Who it is** — its domain and what it explicitly does NOT handle (defer that back).
2. **What it knows** — the durable domain knowledge (executor types, risk sense, etc.).
3. **How it handles a request** — which routines/tools to pull, how to reason, how to
   answer (lead with the recommendation, key: value not prose).
4. A note that it owns domain memory (`manage_memory`) and skills (`manage_skill`)
   scoped to it, to consult before answering and update after.

**Always set `when_to_consult`** — every agent should be consultable, so Condor can ask
it a question whether or not it ever loops. Phrase it as the trigger for when Condor
should delegate to this agent. Edit later with `update_agent(agent_slug=…,
instructions=…)` or read the current body with `get_agent(agent_slug=…)`.

### Phase 3 — Routines (the agent's analysis tools)
Routines live at the agent level (`agents/{slug}/routines/`) and are shared across the
agent's consults and any strategies. Pass the **agent slug** as `strategy_id` (the
param accepts a bare agent slug or a `agent_slug.strategy_slug` key).

```
manage_routines(action="create_routine", strategy_id="<agent_slug>",
                name="band_scanner", code="<python>")
manage_trading_agent(action="run_routine", strategy_id="<agent_slug>",
                     name="band_scanner", config={…})
```

Use the `routine_builder` skill for the routine API. Test each routine, show the
output, iterate until it returns clean, useful data. Do NOT continue until tested.

### Phase 4 — Validate by consulting it
At this point the agent is already useful — Condor can consult it. Test it the way
condor will use it: `consult(agent="<agent_slug>", task="…", context="…")`. Confirm it
pulls the right routines/tools and gives a tight, correct recommendation. Refine the
AGENT.md with `update_agent` until the consult is good. **Stop here unless the user
wants the agent to also trade autonomously on a loop.**

### Phase 5 — Add a Strategy (loop agents only)
A strategy is the tick playbook the engine runs in a session. Each tick the agent can
run its routines, fetch data with its tools, create/stop **executors**, and
create/modify/stop the **controllers it owns** (`manage_controllers`) — those are the
levers the instructions tell it how to pull. BEFORE writing it, fetch the schema for
every executor type it will use — `manage_executors(executor_type="grid_strike")`, etc.
— and embed the required fields/types directly into the instructions; the tick LLM has
no other way to learn them. Do the same for any controller config it manages.

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

Strategy instructions (the tick system prompt) MUST include: **Objective**;
**Analysis** (which routine to call by name and how to read it); **Decision logic**
(entry/exit/hold); **Executor config** with the FULL schema — every required field,
type, value range, and direction/ordering rule; **Parameter inference** (how to derive
prices/side/TP from routine output and market data); **Risk rules** (max position,
position limits, stop behaviour); **Error recovery** (on a failed create, re-fetch the
schema via `manage_executors(executor_type=…)`, fix, retry once, journal it).

### Phase 6 — Dry run (loop agents only)
```
manage_trading_agent(action="start_agent", strategy_id="<agent_slug.strategy_slug>",
    config={"execution_mode": "dry_run", "agent_key": "ollama:llama3.1",
            "trading_context": "Trade BTC-USDT on binance_perpetual",
            "frequency_sec": 60, "total_amount_quote": 100,
            "risk_limits": {"max_position_size_quote": 200, "max_open_executors": 3}})
```
Review with `trading_agent_journal_read(agent_id=…, section="run:1")`. Check: routines
called correctly, decision logic sound, conditional language ("would place…"), no real
create/stop calls, risk rules respected. Do NOT go live until the user is satisfied.

### Phase 7 — Go live (loop agents only)
Offer `run_once` (single live tick), `loop` (continuous), or `loop` + `max_ticks`. Ask
which model to use live (can differ from the dry-run model). Start, confirm it is
running, and give monitoring commands.

## Monitoring existing agents
1. `manage_trading_agent(action="list_agent_definitions")` — all agents + capabilities
   (consultable, loopable, owned strategies). This is the only list that shows
   consult-only agents (those that own no loop strategy).
2. `manage_trading_agent(action="list_agents")` — running loop instances.
3. `manage_trading_agent(action="agent_status", agent_id=…)` — instance status.
4. `trading_agent_journal_read(agent_id=…, section="summary"|"runs"|"run:N")`.

## Reference

**Consultable rule:** `consultable = when_to_consult is set` — any model. The model only
changes *how* the consult runs: a pydantic-ai key (`ollama:…`/`openai:…`/`groq:…`/
`lmstudio:…`) enforces the `tools` allowlist; an ACP key (`claude-code`/`gemini`/
`copilot`) runs unrestricted, with mutations still confirmation-gated. So a `claude-code`
loop agent is consultable too — set `when_to_consult` and it shows up for Condor.

**Model selection:** The model is set per session, not baked in. The agent/strategy
`agent_key` is the default; override at launch via `config={"agent_key": "…"}`.
- ACP (subprocess CLI): `claude-code`, `gemini`, `copilot`
- Pydantic-AI local: `ollama:llama3.1`, `ollama:qwen3:32b`, `lmstudio:<model>`
- Pydantic-AI cloud: `openai:gpt-4o`, `groq:llama-3.3-70b-versatile`
- Custom endpoint: `openai:<model>` + `model_base_url` in config.
Default URLs: Ollama=localhost:11434, LM Studio=localhost:1234.

**Generic vs Specific strategies:**
- GENERIC (default): pair/connector are NOT in the instructions — passed at launch via
  `trading_context`. Refer to "the configured trading pair"; keep sensible
  `default_config`.
- SPECIFIC: pair/connector baked into the instructions (e.g. an ETH/BTC ratio play).

**Agent tools, memory & skills:** `tools` on the AGENT.md is a tool-name allowlist
enforced on pydantic-ai consults and loops (empty = unrestricted; not enforceable on ACP
keys). An agent keeps its own domain memory (`manage_memory`) and reusable playbooks
(`manage_skill`/`agents/{slug}/skills/`) — the agent OWNS skills; it is not itself a skill.

**Deleting:** `manage_trading_agent(action="delete_agent", agent_slug=…)` refuses while
the agent still owns strategies — delete those first (`delete_strategy`).

## Rules
- Be direct and concise. Only the phase label as a header. Status as key: value.
- **Explain before you interrogate.** On the first turn of a new agent, lead with how
  agents work (essence → consult/loop → routines first); never open with a config
  questionnaire. Draw out the essence in conversation, one thread at a time.
- Every agent is consultable (always set `when_to_consult`); only add a strategy when
  the user wants it to act autonomously on a loop.
- The essence + routines are the core — get those right before any strategy/loop work.
- Create the AGENT.md FIRST — routines and strategies require an existing agent_slug.
- One routine per analysis task; confirm it loads after creation.
- Always include risk limits when starting a loop agent.
- Guide one step at a time and offer concrete proposals.

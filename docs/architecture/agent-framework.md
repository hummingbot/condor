# Condor agent framework — data ontology & technical schema

> **Superseded in part (2026-07-11):** refactor-01b and refactor-02 are now
> implemented. Where this document describes per-strategy state
> (`strategies/{sslug}/sessions|learnings|dry_runs`), composite
> `{agent_slug}.{strategy_slug}_{N}` ids, flat `delegations/{task_id}.md`
> transcripts, ungated delegations, or `run_once` as an experiment mode, the
> current system differs: all history lives at `agents/{slug}/sessions/` with
> `meta.yml` (kind + strategy), ids are `{slug}_{N}`, delegations are risk-gated
> sessions, and run_once is `loop` + `max_ticks: 1`. See
> [refactor-01b](refactor-01b-agent-history-multi-strategy.md) and
> [refactor-02](refactor-02-unified-run-primitive.md), and
> `condor/agents/README.md` for the current layout.

Architectural reference for how Condor's assistant/agent/skill/routine/strategy
system fits together, as of PR #135 (`feature/FEAT-003-stores-por-asistente`,
merged `877bb09`). This restructured `condor/trading_agent/` into `condor/agents/`
and split the monolithic `assistants/condor.md` into a proper per-assistant
directory, introducing the two-tier `assistants/` vs `agents/` split described
below.

## 1. Two top-level entity types: assistant vs. agent

Not two flavors of the same thing — two different schemas in two different
directories, with hard-coded, non-overlapping discovery.

| | `assistants/condor/` | `agents/{slug}/` |
|---|---|---|
| Frontmatter | `label`, `description`, `agent_key` | `name`, `description`, `agent_key`, `tools`, `when_to_consult`, `server_required`, `server_name`, `created_by`, `created_at` |
| Loaded by | `handlers/agents/_shared.py` (`_parse_assistant`) | `condor/agents/agent.py` (`Agent` dataclass, `AgentStore`) |
| Role | Coordinator — routes the user to skills/agents, manages memory | Domain specialist — advisory knowledge and/or an autonomous trading loop |
| Discoverable via `consult`/`delegate`? | **No** | Yes |

`AgentStore._DATA_ROOT` (`condor/agents/agent.py:40`) only ever scans `agents/`.
`condor/memory/paths.py` hard-codes the split: `agent_slug=None` resolves to
`assistants/condor/...`, any real slug resolves to `agents/{slug}/...` — *"the
chat lives under `assistants/condor/` and trading agents under
`agents/{slug}/`, which are different top-level dirs."*

Consequence: **Condor cannot consult or delegate to itself.** It is the single
coordinator that calls `consult()`/`delegate()` *on* domain agents; it is not a
peer entry in the same registry.

Currently there are exactly two domain agents:

- `agents/market_making_expert/` — autonomous trading specialist, runs strategies.
- `agents/routine_builder/` — on-demand routine-authoring specialist, no strategies.

### Routing decision tree: skill -> agent -> consult/delegate -> raw tools

This entire routing decision is **prompt-driven, not code-driven** — there is no
Python router; Condor (the LLM) applies this ordered checklist itself using its
injected `[SKILLS]` / `[AGENTS]` indexes (`assistants/condor/AGENT.md:37-78`):

```
User request
|
+-- Is it routine create/edit/fix/debug?
|     `-- YES --> consult(agent="routine_builder", ...)
|                 (hard rule — bypasses the skill check entirely;
|                  routine code touches live trading data/APIs)
|
+-- (no) Does a [SKILLS] playbook match this task?
|     `-- YES --> manage_skill(action="read", name="...")
|                 follow its steps; if references_routine, run that
|                 routine directly (don't reimplement it)
|                 --> answer inline. DONE
|                 (cheapest path: no subprocess, no separate session)
|
+-- (no) Does an [AGENTS] domain match (when_to_consult)?
|     `-- YES --> route to that domain agent; pick CONSULT or DELEGATE:
|                 |
|                 +-- Task is quick (~< 1-2 min): a read/lookup, a
|                 |   small single-step mutation, quick analysis —
|                 |   AND the user is waiting/watching
|                 |     `-- CONSULT  (blocking)
|                 |         - runs the Agent's own brain (memory + skills)
|                 |           to completion, on its configured model
|                 |         - mutating tool calls -> confirmation prompt
|                 |           in the user's Telegram chat; blocks until
|                 |           resolved
|                 |         - returns answer text -> Condor relays it inline
|                 |
|                 `-- Task is longer (~> 1-2 min) or multi-step: full bot
|                       deployment, tune+backtest+deploy, routine creation,
|                       complex debugging, anything waiting on a backtest —
|                       AND it's fire-and-forget
|                         `-- DELEGATE  (async)
|                             - detached background asyncio.Task,
|                               returns a task_id immediately
|                             - mutating tool calls auto-approved, no
|                               per-action confirmation (the user's chosen
|                               "full auto-approve, no sandbox" model)
|                             - transcript persisted to
|                               agents/{slug}/delegations/{task_id}.md
|                             - user notified via send_notification when done
|
`-- (no match anywhere) --> raw tools directly
      (get_market_data, manage_executors, manage_controllers,
       place_order, manage_bots, ...)
```

Both CONSULT and DELEGATE are entry points into the *same* execution engine
(`_run_agent_to_completion`, shared by `condor/agents/consult.py` and
`condor/agents/delegate.py` — delegate's own comment: *"NOT a new engine...
reuses 100% of consult's client/toolset/prompt wiring"*). They differ in
exactly one axis: **who approves mutating tool calls, and whether you wait.**

| | Consult | Delegate |
|---|---|---|
| Execution | Synchronous, blocks the caller | Background `asyncio.Task`, returns a `task_id` immediately |
| Mutating tool calls | Routed to a permission callback → confirmation prompt in your Telegram chat | `permission_callback=None` → agent auto-approves its own tool calls |
| Output | Returned directly to the caller | Transcript persisted to `agents/{slug}/delegations/{task_id}.md`; user notified via `send_notification` on completion |
| Mental model | "Watch me while I do this" | "Go do this unattended, tell me when done" |

Stated rule of thumb (`AGENT.md:77-78`): *"if the user will be waiting and
watching, consult; if it's fire-and-forget, delegate."* Consult trades speed
for supervision; delegate trades supervision for hands-off execution — it is
a deliberate authorization choice, not just an async version of the same thing.

## 2. Skills — private per owner, no cross-agent sharing

A **skill** is a `SKILL.md` playbook: domain instructions, optionally pointing
at a routine via `references_routine:` frontmatter.

```
builtin_skills_root(agent_slug=None)     -> assistants/condor/skills/
builtin_skills_root(agent_slug="market_making_expert") -> agents/market_making_expert/skills/
```

`SkillStore` (`condor/memory/skills.py`) binds to exactly one directory for its
whole lifetime. There is no global skills directory and no mechanism for one
agent's skill to reference another agent's skill or another agent's routines.
"Shared" in the code's own docstring means *shared across the users of one
assistant* — not shared *across* assistants/agents.

```
assistants/condor/skills/{skill}/SKILL.md         <- Condor's own skills only
agents/market_making_expert/skills/{skill}/SKILL.md  <- MM expert's own skills only
agents/routine_builder/skills/{skill}/SKILL.md       <- routine_builder's own skills only
```

A skill's `references_routine` is validated only against the *same* scope's
routines — an agent-scoped skill checks that agent's own `routines/` dir, never
the global library.

### Condor's own skills vs. an agent's skills

Same class, same `SKILL.md` schema (`condor/memory/skills.py`) — the difference
is contextual, not structural:

- **Injection framing.** Condor's skills are injected under `[SKILLS — check
  here BEFORE handling a known flow with raw tools]`, alongside an `[AGENTS —
  consult these BEFORE doing domain work with raw tools]` index
  (`handlers/agents/_shared.py:576-598`). An agent's skills are injected under
  `[DOMAIN SKILLS — playbooks you can follow]` with **no** `[AGENTS]` section
  (`_shared.py:646-655`) — an agent never sees a list of other agents to
  consult. Condor's skills sit next to "or delegate this instead"; an agent's
  skills are terminal — it's expected to execute the domain itself.
- **What they cover.** Condor's own skills (`log_analyzer`, `agent_builder`,
  `hyperliquid_tokenized_perps`) are things Condor does directly, inline, in
  the current turn. An agent's skills (`pmm_mister_deploy`,
  `capital_allocation`, `routine_cookbook`) are deep, tool-coupled domain
  playbooks assuming that agent's own tool access.
- **`source:` provenance tag** — `builtin` (repo-shipped) / `chat` (authored
  live by Condor) vs. `agent:{slug}` (authored live while running as that
  agent), set automatically by `mcp_servers/condor/tools/skills.py:74`
  (`source = f"agent:{agent_slug}" if agent_slug else "chat"`). Metadata only,
  not enforced differently.

### Why isn't a stateless specialist like `routine_builder` just a Condor skill?

`routine_builder` has no `TickEngine` loop, so it might look like it could be a
plain skill instead of a full Agent. It's a full Agent because of what an
Agent gives you beyond instructions text:

- **Delegation.** A skill is inline instructions read into whichever session
  is already running — no independent lifecycle. An Agent can be
  `delegate()`-d: a background task that runs unattended, auto-approves its
  own tool calls, and notifies the user on completion (see
  `agents/routine_builder/delegations/*.md`). Routine authoring is an
  iterative test-and-fix loop (`routine_builder/AGENT.md:20`: *"You always
  test after creating and fix errors immediately... never leave a broken
  routine"*) — a natural fit for background hand-off, not something to run
  inline mid-chat.
- **Hard routing rule, not advisory.** Condor's system prompt states *"ROUTINES
  ARE SPECIAL: any request to CREATE, EDIT, FIX, DEBUG, or design a routine
  MUST go through the routine_builder agent... do NOT hand-roll it with raw
  manage_routines."* A skill only nudges ("check this before hand-rolling");
  nothing stops Condor from ignoring it. Routine code executes against live
  trading data/APIs, so it's walled off harder than a skill would allow.
- **Isolated context and reference library.** As an Agent it gets its own
  skills dir (`routine_cookbook` + 5 companion files) pulled on demand,
  without competing for space in Condor's own skill index/context.
- Minor note: `routine_builder/AGENT.md:6` sets `tools: []`, which per
  `condor/agents/agent.py:52` means *unrestricted* (all discovered tools), not
  zero — so this isn't about needing a narrower tool sandbox than Condor; the
  isolation is about context and lifecycle, not tool access.

## 3. Routines — one global library + isolated per-agent libraries

A **routine** is executable Python (`manage_routines`), not just instructions —
this is a materially different artifact type from a skill, and it gets a
materially different sharing model:

```
routines/                         <- global, owned by Condor
agents/{slug}/routines/            <- local to that agent only
```

Resolution rule (`routines/base.py`, `mcp_servers/condor/tools/routines.py`):

- **A domain agent session sees only its own `agents/{slug}/routines/`** — never
  the global library, never another agent's.
- **Condor sees the global library, plus a read-only, prefixed overview of every
  agent's routines** (`{agent_slug}/{routine_name}`) for inspection/running via
  chat or the web dashboard.

So sharing is one-directional and overview-only: Condor can *see* what
`market_making_expert` has, but `routine_builder` can never *call*
`market_making_expert`'s `market_analyzer` routine, and neither can the global
library.

### Why routines and skills don't follow the same ontology

This asymmetry is real, and worth naming explicitly:

| | Skills | Routines |
|---|---|---|
| Artifact type | Instructions/knowledge, read into a prompt | Executable code, invoked as a tool call |
| Coupling | Tightly coupled to the owning agent's `tools` allowlist and own routines | Portable — a report-builder or chart routine has no dependency on *which* agent runs it |
| Sharing tier | None (fully siloed per owner) | Two-tier: global + per-agent-local, with Condor bridging both |

The asymmetry is *defensible* — a skill written for `market_making_expert`
assumes tools (`manage_controllers`, `manage_bots`) and routines that
`routine_builder` doesn't have, so unrestricted cross-agent skill sharing could
silently break. But the current design doesn't offer the safe middle ground
that routines already prove out: a shared *generic* skills tier (e.g. "how to
write a good report") that any agent could opt into, the same way any agent's
routines are at least visible to Condor. Absent that, a genuinely cross-cutting
skill has to be copy-pasted into every agent's folder today, which is real
duplication/drift risk over time. **This is a legitimate area to revisit**, not
a bug — the fix would likely be adding a global `skills/` tier mirroring the
existing `routines/` global-library pattern, rather than changing skills'
per-agent privacy.

This gap has since acquired a concrete business rationale, not just an
ontological one — see
[`docs/strategy/business-strategy.md`](../strategy/business-strategy.md) §11a:
if Condor moves toward more strategy creators building on the same engine
(more Botcamp Solutions mandates, or eventually a white-label/tokenized
model), a shared skills tier is what lets each one avoid rebuilding basic
playbooks from scratch — the same role Enzyme's Adapters/Policies and GLAM's
ACL/integration model play for on-chain vault managers. That doc also
concludes that if agents/strategies are ever tokenized, the **strategy**
layer (`agents/{slug}/strategies/{sslug}/`) is the right unit — not the
assistant or the agent — since it already has its own isolated config,
session journal, and track record.

## 4. Strategy — an agent's tick-loop playbook, not a trading bot

`condor/agents/strategy.py` defines `Strategy` as *"a tick-loop playbook that
lives under its owning Agent."* Layout:

```
agents/{agent_slug}/
    AGENT.md                  # the brain: tools, when_to_consult, model
    routines/                 # shared by ALL of this agent's strategies
    skills/                   # shared by ALL of this agent's strategies
    strategies/
        {strategy_slug}/
            strategy.md        # tactics + config for this one playbook
            learnings.md        # cross-session learnings
            sessions/session_N/ # per-run journal
            dry_runs/           # experiment snapshots
```

A strategy's identity is the composite pair `(agent_slug, slug)`; MCP tools pass
it around as the opaque key `"{agent_slug}.{slug}"`. Crucially, the agent's
memory/skills/routines — the "brain" — live **one level up** and are shared
across every strategy that agent runs.

### Why `market_making_expert` has strategies and `routine_builder` doesn't

A strategy only makes sense for an agent driven by `TickEngine`
(`condor/agents/engine.py`) — the recurring loop that spins up a fresh LLM
session each tick to read market/risk state and decide deploy / update / hold /
stop against one or more Hummingbot controllers. `market_making_expert` is that
kind of autonomous trading agent, so `agents/market_making_expert/strategies/pmm_mister_operator/`
exists to hold its playbook.

`routine_builder` is a pure consult/delegate specialist: it's invoked on demand
to author routine code for other agents and then returns — there's no
persistent market loop for a strategy to attach to. Its own `skills/routine_cookbook`
supports *how it answers a request*, not an autonomous trading cycle. Hence no
`strategies/` directory for it, and none is expected.

### Can one agent run multiple strategies? Yes

`StrategyStore.list(agent_slug)` returns a list, not a singleton, and
`StrategyStore.list_all()` spans every agent for flat overviews. The model is:
**an agent is a shared brain (tools, skills, routines, default model) that can
run several independent tick-loop playbooks concurrently** — e.g.
`market_making_expert` could run `pmm_mister_operator` against one pair and a
differently-parameterized strategy against another, each with its own config,
session journal, and dry-run history, while both draw on the same underlying
domain skills and routines.

### Strategy vs. Hummingbot V2 controller + config

These sit at different layers of the stack and are frequently confused because
both eventually "run a market-making bot":

| | Agent strategy | Hummingbot V2 controller + config |
|---|---|---|
| What it is | A recurring LLM-driven policy/playbook | A deterministic trading process with a fixed parameter set |
| Where it lives | `agents/{slug}/strategies/{sslug}/strategy.md` | Hummingbot-api backend, managed via `manage_controllers` / `manage_bots` |
| What it decides | Regime detection, capital allocation across possibly several controllers, when to deploy/tune/stop, risk/kill-switch judgment | Spreads, `total_amount_quote`, `target_base_pct`, etc. — executes tick-by-tick until changed |
| Cardinality | One strategy can own/orchestrate many controllers or bots (e.g. splitting a portfolio across 10 controllers) | One controller config runs inside one bot process |
| Started via | `TickEngine`, on a recurring schedule | `/bots` (web dashboard) or `manage_bots deploy` |

Concretely, `pmm_mister_operator/strategy.md` each tick: runs its routines
(market_analyzer, mm_dashboard) → assesses regime & inventory → decides
DEPLOY/UPDATE/HOLD/STOP → calls `manage_controllers`/`manage_bots` to act. The
strategy is the policy layer *deciding what to do with* controllers; the
controller+config is the thing actually placing orders.

## 5. Shared default behavior: shutdown policy

The one place genuine sharing happens by inheritance rather than a registry.
`condor/agents/shutdown.py` resolves a policy by walking, most-specific first:

```
agents/{slug}/strategies/{sslug}/shutdown.md   (per-strategy override)
agents/{slug}/shutdown.md                       (per-agent override)
agents/_defaults/shutdown.md                    (repo-wide default)
```

Every kill-switch/emergency stop, regardless of which agent or strategy
triggered it, then always runs the same three-stage sequence: a deterministic
close-out first (no LLM involved — guaranteed to act even if the model or
market is misbehaving), a bounded (300s), fail-open LLM cleanup pass second
using whichever `shutdown.md` body matched, and a verify-and-retry check last
that loudly alerts if anything that should be closed is still open.

## 6. Session & turn mechanics — process model and context lifecycle

How a chat session is actually spawned, kept alive, and fed prompts turn after
turn — this is orthogonal to everything above (assistants/agents/skills/
routines/strategies are *content*; this section is the *runtime* that serves
any of them a turn at a time).

### Process model: one backend process, one OS subprocess per session

Condor itself is a single OS process, single thread, one asyncio event loop
(`main.py`: `asyncio.run(_run_dual(application))`), running the FastAPI/uvicorn
web server and the Telegram bot's polling loop as concurrent asyncio tasks on
that one loop. There is no thread-per-request or thread-per-session model.

Each chat session (a Telegram chat, or a web "slot") gets its own **separate OS
subprocess**, not a thread. `get_or_create_session()` (`handlers/agents/session.py:114-245`)
keys sessions by `chat_id` in a module-level, in-memory-only dict `_sessions`
("subprocesses can't survive restarts"). Creating one calls
`asyncio.create_subprocess_shell(command, ..., start_new_session=True)`
(`condor/acp/client.py:370-379`) — a real fork/exec into its own process group.
`command` resolves to the `claude-agent-acp` bridge, which itself launches a
real Claude Code CLI session, which in turn spawns the MCP servers listed in
`mcpServers` (`condor`, `mcp-hummingbot`) as *its own* children — not Condor's.
The tree is four levels deep per active session:

```
Condor backend process  (1 process, 1 thread, asyncio event loop)
  |
  +-- claude-agent-acp subprocess        (own process group)
        |
        +-- claude   (the real Claude Code CLI)
              |
              +-- mcp-hummingbot MCP server subprocess
              +-- condor MCP server subprocess
```

(`reap_stale_acp_trees`'s own docstring names this exact
`claude-agent-acp -> claude -> MCP` chain when reaping orphans at startup.)

Handshake happens once, over that subprocess's stdin/stdout as JSON-RPC 2.0:
`initialize` -> `session/new` (returns a `sessionId` + advertised models) ->
`session/set_model` to pin the requested model (the bridge ignores the
`ANTHROPIC_MODEL` env var, so model selection has to go over the protocol).

**No OS threads inside Condor's process.** `_read_loop()` / `_drain_stderr()`
are `asyncio.create_task`s doing non-blocking `readline()` on the subprocess's
pipes — cooperative multitasking on the single event loop. Any number of
sessions (capped at `MAX_SESSIONS_PER_USER=5` per user) are multiplexed as
asyncio tasks on that one thread; the actual LLM calls and tool execution
happen entirely inside each session's separate child OS process. So: **same
thread for orchestration, a dedicated OS process per session for execution.**

The subprocess is reused turn after turn — never recreated between prompts —
as long as `client.alive` (`self._process.returncode is None`). It's torn down
by: explicit `destroy_session()`, a background health monitor polling every 15s
for dead subprocesses (force-clears a stuck `is_busy`, notifies the user), or
the whole Condor process restarting (orphaning the tree until the next
startup's `reap_stale_acp_trees` sweep).

### Context lifecycle: injected once, never rebuilt

`build_initial_context()` (`handlers/agents/_shared.py:443-610`) assembles the
system prompt + server/permission block + `[USER MEMORY]` + `[SKILLS]` +
`[AGENTS]` (+ mode-specific extra) **exactly once per session** — either eager
(sent as a blocking `client.prompt()` right after handshake, Telegram) or lazy
(stashed on `session.pending_context`, prepended to the first real user
message, web — so `start_session` returns instantly).

From the second turn onward, Condor sends **only the raw new user text** via
`session/prompt` — no re-sending of the system prompt, indexes, or prior
turns, and no local copy of conversation history is kept anywhere in
`AgentSession`/`_sessions`. All growth of "what the agent knows" — its own
prior replies, tool results, reasoning — lives entirely inside the long-lived
subprocess's own transcript, which Condor never reads back:

```
session start:  [system prompt + memory + skills + agents index] + turn 1 text
                                    |  (sent once, as/with the first prompt)
                                    v
                     claude-agent-acp subprocess (owns the growing transcript)
                                    ^  ^  ^
   turn 2 text  ---------(raw)-----+  |  |
   turn 3 text  ------------(raw)-----+  |
   turn N text  ---------------(raw)-----+
```

A consequence: a skill/memory/agent created *mid-session* won't appear in that
session's injected index — only a fresh session re-snapshots current state.

### Two prompts in a row: rejected, not queued

Two guards, checked in order:

1. **WS handler** (`chat_ws.py:_handle_send_message:322-324`) checks
   `session.is_busy` first; if true, replies `{"event": "error", "message":
   "Agent is busy"}` immediately — the second message never reaches
   `prompt_stream`.
2. **Defense-in-depth inside `AgentSession.prompt_stream`** — acquires an
   `asyncio.Lock` with a 30s timeout in case something races past the
   `is_busy` check; times out to `RuntimeError("Agent is busy and not
   responding...")`, self-healing (force-clearing `is_busy`) if the subprocess
   is confirmed dead by then.

Under normal use there is no queueing to reason about: a second prompt sent
mid-turn gets an immediate "Agent is busy" bounce at the WebSocket layer.

### Cancellation: local detach, not a real stop

`abort_prompt` -> `session.abort()` sets an `asyncio.Event` and calls
`client.abort_prompt()`, which pops/cancels the locally-tracked response
`Future` and drains the buffered event queue. Its own docstring names the
limitation: *"the ACP subprocess will keep running (there's no protocol-level
cancel), but the next prompt_stream call will start clean."* **Aborting does
not stop the underlying Claude Code process** from finishing whatever it was
mid-way through for that turn — it keeps executing server-side; Condor just
stops listening and relaying its output.

What actually unblocks the session for a new prompt is `chat_ws.py` explicitly
`.cancel()`-ing the tracked asyncio `Task` for that turn and awaiting it
(shielded, 3s timeout) so `prompt_stream`'s `finally` runs and releases the
lock. The next `prompt_stream()` call defensively re-cancels any lingering
request future and re-clears the event queue before sending a fresh
`session/prompt` **on the same subprocess/session** — cancellation never kills
or restarts the process; only `destroy_session()` (SIGTERM, then SIGKILL after
3s) actually terminates the tree.

## Summary diagram

```
assistants/condor/                      agents/{slug}/
  AGENT.md  (coordinator, no tools)        AGENT.md  (tools, when_to_consult)
  skills/   (private to Condor)            skills/   (private to this agent)
                                            routines/ (private to this agent —
routines/  (global, Condor-owned) <---------- overview only, not callable) 
  (Condor sees global + overview            strategies/{sslug}/
   of every agent's local routines)           strategy.md   (tick-loop playbook)
                                              learnings.md, sessions/, dry_runs/
                                            delegations/{task_id}.md (delegate transcripts)

Condor  --consult/delegate-->  Agent  --TickEngine tick-->  Strategy
                                                              |
                                                              v
                                                manage_controllers / manage_bots
                                                              |
                                                              v
                                          Hummingbot V2 controller + config (the bot)
```

# Condor agent framework — the simplified architecture

Architectural reference for the agent system as of 2026-07-11, after
[refactor-01b](refactor-01b-agent-history-multi-strategy.md) (agent-level
history), [refactor-02](refactor-02-unified-run-primitive.md) (one run
primitive), and [refactor-05](refactor-05-skill-evolution.md) (portable
skills) — all implemented and live. (Refactor-05's Phase 3 automatic
curation loop was implemented, then removed 2026-07-11 as over-complex;
skill improvement is human-directed in chat.) This supersedes the
descriptive parts of [agent-framework.md](agent-framework.md); that
document's §6 (session & turn mechanics — the chat process model) is
unchanged and still authoritative.

The one-sentence version: **an Agent is one identity with one history; every
way of invoking its brain is the same primitive under a different permission
policy; it captures what it learns as learnings; and its skills are portable
artifacts any host harness can install.**

## 1. Ontology — three things, not five

| Entity | Is | Is NOT |
|---|---|---|
| **Agent** (`agents/{slug}/AGENT.md`) | The unit of identity, attribution, and accumulation: tools allowlist, consult trigger, server pin, **risk baseline**, plus ALL operational history | A per-task construct; there is exactly one history per agent |
| **Strategy** (`strategies/{sslug}/strategy.md`) | A pure **playbook template**: tick tactic + `default_config`. A start-time selector recorded as session metadata | A state owner. No sessions/learnings/config.yml/shutdown.md live under it |
| **Session** (`sessions/session_N/`) | One run of the agent's brain, of any kind: `tick_loop`, `delegation`, `consult` — same envelope, same numbering | Strategy-scoped. Sessions belong to the agent; the strategy is a `meta.yml` field |

Supporting artifacts (owned by the agent, shared across all its playbooks):
**skills** (markdown procedures, three tiers — §6), **routines** (executable
Python), **learnings** (`learnings.md`, the capped semantic capture pool),
**store/** (per-user memory). The memory triple is deliberate and matches
what the field converged on independently: journal = episodic, learnings =
semantic, skills = procedural.

The chat coordinator (`assistants/condor/`) remains a separate schema — it
routes to agents and is not itself consultable. (Its skills moved to the
repo-root `skills/` in refactor-05; the rest of the assistants layer is a
pending cleanup, refactor-06.)

## 2. Identity & storage — one dir, one numbering, metadata not addresses

Session identity is `{agent_slug}_{N}` (`{agent_slug}_e{N}` for
experiments — a.k.a. dry runs). The strategy slug is **not part of the address** — which
playbook a session ran is metadata, so per-playbook track records are a
filter over one comparable list, not separate trees.

```
agents/{slug}/
    AGENT.md                    # identity + domain knowledge
                                #   frontmatter: tools, when_to_consult,
                                #   server_required/name, risk_limits (baseline)
    learnings.md                # agent-level; entries [strategy]-prefixed;
                                #   Promoted section = folded into a skill
    shutdown.md                 # optional winddown override (walk: agent → _defaults)
    skills/  routines/  store/  # the shared brain
    strategies/
        {sslug}/strategy.md     # playbook + default_config — NOTHING else
    sessions/
        session_1/              # kind: tick_loop
            meta.yml            #   kind, strategy, status, model, timestamps
            config.yml          #   frozen launch config
            journal.md          #   summary / decisions / ticks / executors
            snapshots/          #   full per-tick dumps
        session_2/              # kind: delegation | consult
            meta.yml            #   kind, status, task, risk_limits (delegations)
            transcript.md       #   full reasoning + tool calls + result
    experiments/
        experiment_N.md         # experiments only — scratch that never touches capital
```

Allocation is mkdir-atomic (`allocate_session_dir`), so concurrent starts
never collide. `run_once` is not a storage mode: it maps to an ordinary
`tick_loop` session with `max_ticks: 1` — journal, frozen config, risk
pre-flight, and a place in the track record. `_eN` is reserved for true
experiments (mutations cancelled by the gate; a flat snapshot, never a session).

Boundary worth memorizing: **experiments = scratch that never touches capital;
sessions = anything that does (or could).**

## 3. One execution primitive: `run_agent` + the policy lattice

Every way an agent's brain gets invoked is one function
(`condor/agents/run.py`) parameterized by a permission policy
(`condor/agents/policies.py`). There are no parallel execution stacks.

```
run_agent(agent, prompt, *, permission_policy, ...) -> RunResult
  .--------------------------------------------------------------.
  |  resolve model (caller passes the triad winner:               |
  |    config > strategy > agent) --- healthcheck / fallback      |
  |            |                                                  |
  |  build MCP servers (server pin > ambient; agent_slug scope)   |
  |            |                                                  |
  |  make client: pydantic-ai (tools allowlist enforced)          |
  |               OR ACP subprocess (claude-acp / gemini / ...)   |
  |            |                                                  |
  |  permission_policy --> client's permission_callback           |
  |            |                                                  |
  |  stream events --> fold ONCE into BOTH views                  |
  |    (events = chronological transcript; tool_calls = tick view)|
  |    under asyncio.timeout(timeout_s)                           |
  |            |                                                  |
  |  finally: client.stop()  (reap subprocess, always)            |
  '--------------------------------------------------------------'
        RunResult(text, tool_calls, events, duration, error, model)
```

The policy lattice — the ONE axis that genuinely differs between run kinds:

```
                strictest
                    |
     human_gate(chat_id)          consult: dangerous calls -> Approve/Reject
                    |             in the user's Telegram chat.
                    |             FAILS CLOSED: no bot or no chat_id ->
                    |             deny_gate (safe tools pass, mutations
                    |             cancelled + logged). Never None.
                    |
     risk_gate(limits, state)     ONE shared policy for anything that can
                    |             trade. Caller picks the state seed:
                    |               tick:       journal-derived (real exposure/
                    |                           count carried across ticks)
                    |               delegation: RiskState() at zero (caps act
                    |                           as a per-run budget)
                    |             Blocks: place_order (always), uncapped bot
                    |             deploys, executor creates past caps.
                    |             experiment=True additionally cancels ALL
                    |             mutations.
                    |
     AUTO (None)                  serverless specialists (routine_builder)
                loosest           — enforcement lives elsewhere (tier guards).
```

A `None` permission callback means the client auto-approves everything — so
a gate that cannot be built must return `deny_gate`, never `None`. This was
a real, live bug class; it is now tested against.

## 4. The three run kinds — one primitive, three call sites

### 4.1 Consult — synchronous, human-gated ("watch me do this")

```
chat / web POST /agents/{slug}/consult
     |
     v
run_consult(slug, task)                       condor/agents/consult.py
     |  build_agent_context: identity + [DOMAIN MEMORY] + [DOMAIN SKILLS] + task
     v
run_agent(policy=human_gate(chat_id), timeout=900s)
     |                    |-- dangerous call? -> Telegram Approve/Reject,
     |                    |   blocks until tapped (registry is process-global)
     |                    `-- no chat/bot? -> deny_gate: mutation cancelled
     v
persist kind:consult session (transcript + meta), retention cap 20
     |
     v
return answer text inline (caller was blocking the whole time)
```

### 4.2 Delegation — background, unattended, **risk-gated**

```
delegate(action="start", agent=..., task=..., [risk_limits={...}])
     |
     v
start_delegation()                            condor/agents/delegate.py
     |  resolve policy FIRST (loud error before anything runs):
     |    trading agent (server_required)?
     |       limits = per-call risk_limits override   <- REPLACES baseline
     |             or AGENT.md risk_limits baseline
     |       neither? -> ValueError (say the numbers out loud)
     |       policy = risk_gate(limits, RiskState())   # zero seed = per-run budget
     |    serverless specialist? policy = AUTO
     |
     |  allocate sessions/session_N NOW (kind:delegation, status:running
     |  -> a crash leaves an inspectable husk); task_id == session id
     |
     |  detached asyncio.Task ------------------> caller gets task_id immediately
     v
run_agent(policy, event_sink -> live dt.events, timeout=900s)
     |            |-- place_order            -> blocked
     |            |-- deploy w/o loss cap    -> blocked
     |            `-- executor creates       -> counted against the budget
     v
finally: transcript.md + finalize meta (done|error|stopped)
     |
     v
notify user on Telegram (the notification IS the return path)
```

A zero baseline (`{max_position_size_quote: 0, max_open_executors: 0}`) is
the **read-only pattern**: the agent gets live market data but every
order-shaped action is blocked by construction (`funding_rate_watcher`,
`backtest_lab`).

### 4.3 Tick loop — `TickEngine` is a scheduler, not an engine

```
TickEngine (owns loop/pause/max_ticks, the _engines registry entry,
            directive injection — and NO client/stream code)
============================================================================
  every frequency_sec, while running and not paused:
       |
       |  PRE-FLIGHT (tick-only)
       |    providers: fetch executors/positions (controller_id == agent_id)
       |    journal readback: learnings / summary / recent decisions
       |    risk_state = RiskEngine.get_state(journal)
       |       |-- should_shutdown? --> run_shutdown() ----.
       |       `-- is_blocked?      --> journal + skip      |  SHUTDOWN
       v                                                    |  ESCALATION
  prompt = build_tick_prompt(agent, strategy, config,       |  (scheduler-level
           core data, journal context, risk state)          |   hook, not in the
       |                                                    |   primitive):
       v                                                    |  deterministic
  run_agent(policy=risk_gate(limits, risk_state),           |  close (no LLM) ->
            timeout=300s, on_client=hold-for-cancel)        |  bounded LLM
       |                                                    |  cleanup ->
       v                                                    |  verify + alert
  POST-RUN (tick-only)                                      |  -> stop
    journal.record_tick / record_snapshot /                 |
    save_full_snapshot / write_summary                      |
       |                                                    |
  session end (stop / max_ticks reached):                   |
    finalize meta.yml                                       |
```

Launch resolution: `start_agent(agent_slug, strategy=...)` — the strategy is
optional with exactly one playbook, a loud error listing options with
several. Risk limits resolve **request config > strategy default_config >
AGENT.md baseline > schema defaults**.

## 5. Self-improvement: capture, then human curation

The pen is never held by the in-run agent. Ticks are told skills are
read-only; their write channel is learnings (one-line facts, deduped,
`[strategy]`-provenance-prefixed, capped at 20).

Folding learnings into skills is **human-directed, in chat**: review an
agent's learnings, then use `manage_skill(action="patch")` — a delta edit
(old→new string, must match exactly once) that stamps provenance
(`condor-updated-by` + an appending `condor-changelog`) — and optionally
mark the consumed learning via the journal's `promote_learning` (moves it
to the `## Promoted` section: it stops occupying the capped active pool
but stays on record). Shared-tier writes remain chat-only
(`scope="shared"`); agents get a loud read-only error.

Why delta patches: full-rewrite self-editing measurably collapses playbooks
(ACE's brevity bias / context collapse), so `patch` is the preferred edit
even for humans. An automatic session-end curation pass (refactor-05
Phase 3: evidence-gated delta patches by the agent itself under a
restricted tool profile) was implemented, live-validated, and then
**removed** (2026-07-11) — the machinery outweighed the benefit at this
scale. The capture side and the patch/promote primitives it used remain.

## 6. Skills — one portable format, three tiers, two estates

All skills are agentskills.io-conformant `SKILL.md` (hyphenated `name`
matching the dir, single-line frontmatter, `description` carries the routing
trigger, Condor extras as flat `condor-*` metadata strings). Progressive
disclosure: index line → body → companion files.

```
HOST (Claude Code / OpenClaw / Hermes — opened in the condor repo)
│    host's native skill index; /slash invocation
│
├── HOST-FACING            skills/<name>/SKILL.md        (repo root)
│     "how to drive Condor via MCP": agent-builder, log-analyzer, ...
│     ONE dir serves every consumer: Condor chat (builtin root),
│     Claude Code (.claude/skills symlinks), OpenClaw (<ws>/skills scan),
│     Hermes (tap layout). compatibility: gates on the Condor MCP;
│     each carries the rule: operate Condor ONLY via mcp__condor__* tools.
│
╌╌╌  MCP boundary (mcp_servers/condor)  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
│
└── AGENT-INTERNAL         consumed only by Condor's own runs
      agents/{slug}/skills/      local tier   (the agent's own playbooks)
      agents/_shared/skills/     shared tier  (chat-writable only, via
                                  manage_skill(scope="shared"); agents get
                                  a loud read-only error; local shadows
                                  shared on a name clash)
      resolution: local > shared — index marks "[shared — read-only]"
```

Index isolation is structural (no host scans `agents/*/skills/`); file-level
isolation does not exist under repo-as-workspace and is not claimed — git
visibility and the MCP-only operating rule are the actual mitigations.

## 7. Routines — unchanged shape, agent-keyed

Executable Python, two tiers: global `routines/` (chat-owned) +
`agents/{slug}/routines/` (agent-local, invisible to other agents). All MCP
params are `agent_slug` now (the composite `strategy_id` key survives only
in strategy CRUD). Authoring remains a hard routing rule: routine
create/edit/fix goes through the `routine_builder` agent — typically as a
delegation, which is also how the framework builds its own examples
(`grid_backtest`, `funding_logger` were authored and tested by it).

The routine/skill/agent decision framework, with worked examples
(backtesting, data collection), lives in
[usage-patterns.md](usage-patterns.md): routine = deterministic *how*,
skill = judgment *how*, agent = *ownership/accumulation*; a loop only for
scheduled work.

## 8. Risk model — one gate, many seats

```
                      where the numbers come from
                      ----------------------------
tick session          request config > strategy default_config
                      > AGENT.md risk_limits baseline > schema defaults
delegation            per-call risk_limits override (REPLACES, never merges)
                      > AGENT.md baseline; NEITHER -> loud error at start
read-only agent       baseline {0, 0} -> every order-shaped action blocked
                      by construction (funding_rate_watcher, backtest_lab)

                      what the gate checks (risk.py risk_gate)
                      ----------------------------------------
place_order           blocked outright, always
manage_bots deploy    must declare bounded max_global_drawdown_quote
                      <= position limit (platform-enforced kill switch)
executor create       count + exposure vs caps; approvals accumulate into
                      the running state within the run
experiment            ALL mutations cancelled (a.k.a. dry run)

                      escalation (tick-only, scheduler-level)
                      ---------------------------------------
soft:  drawdown > max_drawdown_pct        -> tick blocked, journaled
hard:  drawdown > shutdown_drawdown_pct   -> run_shutdown():
       deterministic close (policy from agents/{slug}/shutdown.md ->
       agents/_defaults/shutdown.md) -> bounded LLM cleanup -> verify+alert
```

Known, accepted gap: no aggregate exposure cap *across* concurrent sessions
of one agent — each session's budget is its own (the platform-side deploy
loss cap is the cross-cutting bound).

## 9. Routing (chat) — updated decision tree

Unchanged in spirit from the original doc; the one material change is that
DELEGATE is no longer "full auto-approve, no sandbox":

```
User request
|
+-- routine create/edit/fix?  --> routine_builder (hard rule)
+-- [SKILLS] playbook match?  --> read it, follow it, answer inline
+-- [AGENTS] domain match?    --> consult (quick, user watching, human-gated)
|                                 or delegate (long/fire-and-forget,
|                                 RISK-GATED for trading agents; per-call
|                                 risk_limits override available; session +
|                                 transcript + notification)
`-- nothing matches           --> raw tools
```

## 10. Summary diagram

```
 HOSTS (Claude Code / OpenClaw / Hermes / Condor chat+web)
   |            install natively: skills/<name>/SKILL.md  (host-facing)
   |  mcp__condor__* tools
   v
╌ MCP boundary ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
   |
   |   consult(human_gate)   delegate(risk_gate|AUTO)   start_agent
   |          \                    |                    /
   |           v                   v                   v
   |          .-------------------------------------------.
   |          |            run_agent + policies            |
   |          '-------------------------------------------'
   |                               |
   v                               v
 agents/{slug}/          sessions/session_N (meta.yml: kind+strategy+status)
   AGENT.md (risk baseline)  ├ tick_loop:  journal + snapshots + frozen config
   strategies/{sslug}/       ├ delegation: transcript (+ per-run risk budget)
     strategy.md (playbook)  └ consult:    transcript (retention 20)
   skills/ (local>_shared)
   learnings.md   <── capture (in-run); folded into skills by the
   routines/  store/          user in chat (manage_skill patch, provenance)

 tick sessions --controller_id == {slug}_N--> Hummingbot executors/bots
                                              (the isolated virtual portfolio)
```

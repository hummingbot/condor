# Condor agent framework — the simplified architecture

Architectural reference for the agent system as of 2026-07-11, after
[refactor-01b](refactor-01b-agent-history-multi-strategy.md) (agent-level
history), [refactor-02](refactor-02-unified-run-primitive.md) (one run
primitive), and [refactor-05](refactor-05-skill-evolution.md) (portable
skills + curated self-improvement) — all implemented and live. This
supersedes the descriptive parts of
[agent-framework.md](agent-framework.md); that document's §6 (session &
turn mechanics — the chat process model) is unchanged and still authoritative.

The one-sentence version: **an Agent is one identity with one history; every
way of invoking its brain is the same primitive under a different permission
policy; everything it learns flows learnings → skills through a curated,
git-audited loop; and its skills are portable artifacts any host harness can
install.**

## 1. Ontology — three things, not five

| Entity | Is | Is NOT |
|---|---|---|
| **Agent** (`agents/{slug}/AGENT.md`) | The unit of identity, attribution, and accumulation: tools allowlist, consult trigger, server pin, **risk baseline**, plus ALL operational history | A per-task construct; there is exactly one history per agent |
| **Strategy** (`strategies/{sslug}/strategy.md`) | A pure **playbook template**: tick tactic + `default_config`. A start-time selector recorded as session metadata | A state owner. No sessions/learnings/config.yml/shutdown.md live under it |
| **Session** (`sessions/session_N/`) | One run of the agent's brain, of any kind: `tick_loop`, `delegation`, `consult`, `curation` — same envelope, same numbering | Strategy-scoped. Sessions belong to the agent; the strategy is a `meta.yml` field |

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

Session identity is `{agent_slug}_{N}` (`{agent_slug}_e{N}` for dry-run
experiments). The strategy slug is **not part of the address** — which
playbook a session ran is metadata, so per-playbook track records are a
filter over one comparable list, not separate trees.

```
agents/{slug}/
    AGENT.md                    # identity + domain knowledge
                                #   frontmatter: tools, when_to_consult,
                                #   server_required/name, risk_limits (baseline)
    learnings.md                # agent-level; entries [strategy]-prefixed;
                                #   Promoted section = consumed by curation
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
        session_2/              # kind: delegation | consult | curation
            meta.yml            #   kind, status, task, risk_limits (delegations)
            transcript.md       #   full reasoning + tool calls + result
    dry_runs/
        experiment_N.md         # dry runs only — scratch that never touches capital
```

Allocation is mkdir-atomic (`allocate_session_dir`), so concurrent starts
never collide. `run_once` is not a storage mode: it maps to an ordinary
`tick_loop` session with `max_ticks: 1` — journal, frozen config, risk
pre-flight, and a place in the track record. `_eN` is reserved for true dry
runs (mutations cancelled by the gate; a flat snapshot, never a session).

Boundary worth memorizing: **dry_runs = scratch that never touches capital;
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
  |  build MCP servers (server pin > ambient; agent_slug scope;   |
  |    optional --tool-profile — call-time tool restriction       |
  |    that binds even ACP models)                                |
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
                    |             dry_run=True additionally cancels ALL
                    |             mutations.
                    |
     AUTO (None)                  serverless specialists (routine_builder)
                loosest           and curation — where the enforcement lives
                                  elsewhere (tier guards, tool profiles).
```

A `None` permission callback means the client auto-approves everything — so
a gate that cannot be built must return `deny_gate`, never `None`. This was
a real, live bug class; it is now tested against.

**Tool profiles** are the second enforcement line: the condor MCP subprocess
accepts `--tool-profile` and refuses out-of-profile tools at call time
(`middleware.py`). This binds ACP models, which cannot be client-side
allowlisted. The `curation` profile permits only skills/journal/memory
tools; unknown profiles fail closed.

## 4. The four run kinds — one primitive, four call sites

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
    finalize meta.yml -> _maybe_curate() --------------> §5 curation
```

Launch resolution: `start_agent(agent_slug, strategy=...)` — the strategy is
optional with exactly one playbook, a loud error listing options with
several. Risk limits resolve **request config > strategy default_config >
AGENT.md baseline > schema defaults**.

### 4.4 Curation — the sleep-time pass (see §5)

Runs as its own `kind: curation` session under AUTO + the `curation` tool
profile. Not schedulable by the user directly against markets — it never
touches them.

## 5. Self-improvement: capture → curate → promote

The pen is never held by the in-run agent. Ticks are told skills are
read-only; their write channel is learnings (one-line facts, deduped,
`[strategy]`-provenance-prefixed, capped at 20).

```
      IN-RUN (capture)                 BETWEEN RUNS (curate)               HUMAN (promote)
 .----------------------.      .--------------------------------.      .------------------.
 | tick/delegation runs |      | trigger: tick session end       |      | notification     |
 |  journal.md entries  |      |  (curate_on_stop, gated: >=3    |      | carries          |
 |  learnings.md bullets|----->|  unpromoted learnings AND >=2   |      | PROMOTION        |
 |  transcripts         |      |  tick sessions; per-agent       |      | PROPOSALS        |
 '----------------------'      |  in-flight lock), or /curate,   |      |    |             |
                               |  or MCP curate_skills           |      |    v             |
                               |                                 |      | user confirms in |
                               | kind:curation session, AUTO +   |      | chat -> skill    |
                               | 'curation' tool profile         |      | copied to        |
                               |                                 |      | _shared tier     |
                               | inputs: ACTIVE learnings only + |      | (or host-facing) |
                               |  last-5 tick session digests +  |      '------------------'
                               |  skills index ([shared] marked) |
                               |                                 |
                               | mandates (store-enforced):      |
                               |  - patch ONLY (old->new string, |
                               |    must match exactly once;     |
                               |    full rewrites are human-only)|
                               |  - >=2-session evidence         |
                               |  - dedup before adding          |
                               |  - LOCAL tier only (shared      |
                               |    writes rejected by store)    |
                               |  - changing nothing is a GOOD   |
                               |    outcome                      |
                               |                                 |
                               | every patch stamps provenance   |
                               | (condor-updated-by / changelog);|
                               | consumed learnings move to the  |
                               | Promoted section;               |
                               | pass ends with a git commit     |
                               | scoped to agents/{slug}/skills  |
                               '--------------------------------'
```

Why this shape: full-rewrite self-editing measurably collapses playbooks
(ACE's brevity bias / context collapse); single-episode writes overfit and
poison (Reflexion, memory-management studies); so the loop is delta-only,
evidence-gated, provenance-stamped, and git-audited — guardrails on by
construction, not configuration. Phase 4 (outcome-weighted retention via
skills-sha in session meta) is designed but deferred until the loop has
history.

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
      agents/{slug}/skills/      local tier   (agent-writable via curation)
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
dry_run               ALL mutations cancelled

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
   |           \                   |                   /        curate_skills
   |            v                  v                  v          (AUTO+profile)
   |          .-------------------------------------------.        |
   |          |            run_agent + policies            |<------'
   |          '-------------------------------------------'
   |                               |
   v                               v
 agents/{slug}/          sessions/session_N (meta.yml: kind+strategy+status)
   AGENT.md (risk baseline)  ├ tick_loop:  journal + snapshots + frozen config
   strategies/{sslug}/       ├ delegation: transcript (+ per-run risk budget)
     strategy.md (playbook)  ├ consult:    transcript (retention 20)
   skills/ (local>_shared)   └ curation:   transcript (retention 10)
   learnings.md  ────────────────────────────┐
   routines/  store/                         │ capture
                                             v
                       curation pass: delta patches -> local skills
                       (provenance + scoped git commit; promotion to
                        _shared/host-facing only with the user, in chat)

 tick sessions --controller_id == {slug}_N--> Hummingbot executors/bots
                                              (the isolated virtual portfolio)
```

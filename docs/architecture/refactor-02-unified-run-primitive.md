# Refactor 02 — One run primitive under tick / delegation / consult

Status: **proposed** · Branch: `spike/simpler-agent-framework` · Depends on:
[refactor-01](refactor-01-agent-strategy-merge.md) (agent/strategy merge +
unified sessions). Refactor-01 unifies what runs *leave behind*; this one
unifies *how they run*.

## 1. Goal

Today there are three ways an agent's brain gets invoked — tick loop,
delegation, consult — implemented as **two independent execution stacks**
(`engine.py` has its own; consult/delegate share one). Collapse them into a
single `run_agent()` primitive, parameterized by a permission policy and
lifecycle hooks, so that:

- the duplicated client-factory / prompt-dispatch / event-collection code
  exists once;
- the tick loop becomes a thin scheduler *composing* that primitive with risk
  hooks and journal write-back, instead of a parallel engine;
- risk gating becomes a policy any run kind can opt into — closing the gap
  where **delegations to trading agents currently run with zero risk
  enforcement** (see §3).

## 2. Today: three call paths, one duplicated core

```
        CONSULT                    DELEGATE                    TICK LOOP
   (user waits, gated)        (fire-and-forget)          (recurring, risk-gated)
          |                          |                           |
          v                          v                           v
   run_consult()              start_delegation()          TickEngine._loop()
   consult.py:52              delegate.py:77                engine.py:275
          |                          |                    every frequency_sec:
          |                          |                           |
          +-----------+--------------+                    TickEngine._tick()
                      |                                     engine.py:326
                      v                                          |
        _run_agent_to_completion()                               |
              consult.py:79                                      |
                      |                                          |
     .-----------------------------.            .-----------------------------.
     | build_agent_context()       |            | build_tick_prompt()         |
     |   identity + memory index   |            |   identity + playbook       |
     |   + skills index + task     |            |   + config + risk state     |
     |                             |            |   + executor data (providers)|
     | client factory (its own):   |            |   + journal readback        |
     |   pydantic-ai OR ACP,       |            |                             |
     |   build_mcp_servers_*()     |  ~same     | client factory (its own):   |
     |                             |  code,     |   pydantic-ai OR ACP,       |
     | prompt / prompt_stream      |  written   |   build_mcp_servers_*()     |
     |   (event_sink optional)     |  twice     |                             |
     | await client.stop()         |            | _collect_stream(), 300s cap |
     '-----------------------------'            | await client.stop()         |
                      |                         '-----------------------------'
                      v                                          |
        consult: return text inline                              v
        delegate: transcript + notify              journal: record_tick,
                                                   snapshot, summary
```

Both boxes build the same MCP-server wiring
(`build_mcp_servers_for_agent`/`_for_session`), make the same
pydantic-ai-vs-ACP decision, drive the same stream, and reap the same client —
independently (`consult.py:139-206` vs `engine.py:552-621`). The *real*
differences are entirely at the edges: what goes into the prompt, who approves
tool calls, and what happens after.

## 3. Where safety lives today (and where it doesn't)

Answering the load-bearing question directly: **yes — risk pre-flight,
per-call risk gating, and shutdown escalation are all exclusive to the tick
loop today.**

| Safety mechanism | Tick loop | Consult | Delegation |
|---|---|---|---|
| Pre-run risk state (exposure/drawdown from journal, soft block, kill-switch) — `engine.py:363-381` | ✅ | — | — |
| Per-tool-call gate — `auto_approve_with_risk_check` (`risk.py:211`): executor count/position caps, bot deploys must declare a bounded `max_global_drawdown_quote`, `place_order` blocked outright | ✅ | human approves each mutation instead | **none** — `permission_callback=None`, ACP auto-approves *everything* |
| Shutdown escalation (`shutdown.md` winddown, deterministic floor → LLM cleanup → verify) — `shutdown.py:336` | ✅ (risk kill-switch or manual) | — | — |

Consult is fine: the human confirmation prompt *is* its gate. The delegation
row is a real gap, not a design choice that still holds. `permission_callback=
None` was chosen when delegation meant routine authoring (FEAT-006's "full
auto-approve, no sandbox"), but `market_making_expert`'s own `when_to_consult`
now says *"when the user wants to deploy a new PMM Mister bot — use
delegate."* That delegation can `manage_bots(deploy)` without any declared
loss cap, create executors with no position/count limit, and even call
`place_order` — the exact calls the tick callback blocks or bounds.

Two structural notes that shape the fix:

- **Stateful risk needs a tracker.** `RiskEngine.get_state(tracker)` computes
  exposure/drawdown from the session journal and fails closed without one
  (`risk.py:76-92`). Delegations have no journal and don't tag
  `controller_id`, so drawdown-style limits can't apply to them cheaply.
- **Most per-call checks are stateless.** The `place_order` block, the
  bot-deploy `max_global_drawdown_quote` requirement, the `update_config`
  cap, and executor create caps (counting up from a zero baseline = a
  per-run budget) need only `RiskLimits` — no journal. So delegations can get
  a meaningful risk policy without inheriting the whole tick apparatus.
- **Shutdown stays tick-scoped on purpose.** The winddown closes *a session's*
  positions, found by `controller_id == agent_id` scoping
  (`shutdown.py:_fetch_positions`), using the engine's providers, journal, and
  notify channel. A delegation deploys *persistent* infrastructure (a bot that
  outlives the run) — there is no session-scoped position set to wind down.
  Shutdown is therefore a **scheduler-level hook**, not part of the generic
  primitive (§5.3).

## 4. Target: the `run_agent` primitive

One function owns the middle of the diagram; everything kind-specific becomes
a parameter.

```
run_agent(agent, prompt, *, permission_policy, event_sink=None,
          timeout_s, server_name=None, session=None) -> RunResult

  .--------------------------------------------------------------.
  |                        run_agent core                        |
  |                                                              |
  |  resolve model (config > agent) --- healthcheck/fallback     |
  |            |                                                 |
  |  build MCP servers (server pin > ambient, agent_slug scope)  |
  |            |                                                 |
  |  make client:  pydantic-ai (allowlist enforced)              |
  |                OR ACP (claude-code/gemini/...)               |
  |            |                                                 |
  |  permission_policy --> client's permission_callback          |
  |            |                                                 |
  |  start --> stream events --> event_sink --> collect text     |
  |            |            (asyncio.timeout(timeout_s))         |
  |  finally: client.stop()   (reap subprocess, always)          |
  |            |                                                 |
  |  session hook: transcript/meta.yml persisted (refactor-01)   |
  '--------------------------------------------------------------'
                              |
                              v
        RunResult(text, tool_calls, events, duration, error)
```

The three kinds become three *call sites* of the same core:

```
                          permission_policy      context builder       after the run
                          -----------------      ---------------       -------------
consult      = run_agent( human_gate(chat_id),        consult_context(),  return text inline )
delegation   = run_agent( auto | risk_gate(zero),     consult_context(),  notify user        )
tick         = run_agent( risk_gate(journal state),   tick_context(),     journal write-back )
```

`RunResult` carries `tool_calls` for everyone (today only ticks fold tool
calls; delegate folds them into `events`; consult discards them) — which is
also what lets refactor-01's session transcripts be uniform.

### 4.1 Permission policies — an explicit lattice

The one-axis difference the framework doc already names ("who approves
mutations") becomes a first-class object instead of three hardwired callbacks:

```
                 strictest
                     |
        human_gate(chat_id)            <- consult today (confirmation.py)
                     |
        risk_gate(limits, state)       <- ONE shared policy (today's
                     |                    auto_approve_with_risk_check).
                     |                    Caller chooses the state seed:
                     |                      tick:       RiskEngine.get_state(journal)
                     |                                  (real exposure/count/drawdown
                     |                                   carried over from prior ticks)
                     |                      delegation: RiskState() at zero
                     |                                  (same caps act as a
                     |                                   per-run budget)
                     |
        auto                           <- delegate today (permission_callback=None)
                 loosest
```

`risk_gate` is *not* two policies. Its per-call checks are already
state-light: the `place_order` block and `check_bot_action` (bounded bot
deploys, `update_config` cap) read only `RiskLimits`; only
`check_executor_action` reads `RiskState` — and its within-run accumulation
(a second create is checked against running totals) is shared, desirable
behavior in both contexts. The tick's extra safety lives *outside* the
callback anyway: the pre-run `is_blocked`/`should_shutdown` check happens in
`_tick`, and drawdown limits never trigger from a zero baseline. Dry-run
blocking is a tick-only constructor option (`dry_run=True` cancels all
mutations).

Recommendation: delegations to agents with `server_required: true` (i.e. that
can touch live trading) default to
`risk_gate(agent.default_config.risk_limits, RiskState())`; `auto` remains for
serverless specialists like `routine_builder`. Per the no-silent-fallbacks
rule, an agent with no risk limits configured that receives a trading
delegation should error loudly at `start_delegation` rather than quietly
running unbounded.

## 5. The three call paths, recomposed

Same primitive in the middle of all three; everything that differs is visible
at the edges — who waits, which gate, what happens afterward, and whether it
recurs.

### 5.1 Consult — synchronous, human-gated (the user is watching)

```
   condor chat  /  web POST /agents/{slug}/consult
        |
        v
   run_consult(slug, task, context)
        |
        |   load Agent (AgentStore) — unknown slug -> error + consultable index
        v
   prompt = consult_context(agent, user_id, task, context)
            identity body + [DOMAIN MEMORY] + [DOMAIN SKILLS] + task
        |
        v
   result = await run_agent(agent, prompt,
                permission_policy = human_gate(chat_id),
                timeout_s = 900)                      # today: none — see §7
        |                        |
        |                        |  mutating tool call?
        |                        v
        |                 Approve / Reject prompt in the user's
        |                 Telegram chat; the run BLOCKS until tapped
        |                 (confirmation registry is process-global,
        |                  so it resolves even while condor's own
        |                  chat session is busy awaiting this consult)
        v
   session hook: persist kind=consult transcript      (refactor-01 §10.1)
        |
        v
   return result.text INLINE to the caller
        '--- caller was blocked the whole time; no notification needed
```

### 5.2 Delegation — background, fire-and-forget

```
   condor chat  /  web POST /agents/{slug}/delegate
        |
        v
   start_delegation(task) ---> DelegateTask in _delegations registry
        |                            |          (in-memory, dies with process)
        |   task_id returned         |
        |   IMMEDIATELY -------------+---> caller may poll delegate(action="get")
        |                                  or cancel via stop_delegation
        v
   detached asyncio.Task: _run(dt)
        |
        v
   allocate sessions/session_N/  (kind=delegation, meta.yml status=running —
        |                         a crash leaves an inspectable husk)
        v
   prompt = consult_context(agent, user_id, task)     # SAME builder as consult
        |
        v
   result = await run_agent(agent, prompt,
                permission_policy = risk_gate(limits, RiskState())  # zero seed
                                    | auto (serverless specialists),
                event_sink = fold into dt.events,
                timeout_s = 900)
        |                        |
        |                        |  mutating tool call?
        |                        v
        |                 auto-approved UNLESS it breaches caps:
        |                   place_order            -> blocked
        |                   deploy w/o loss cap    -> blocked
        |                   executor create        -> per-run budget check
        v
   finally: write transcript.md, finalize meta.yml (done | error | stopped)
        |
        v
   notify user on Telegram (result snippet)
        '--- nobody was waiting; the notification IS the return path
```

### 5.3 Tick loop — recurring, risk-gated, journal-threaded

`TickEngine` stops being an execution engine and becomes a **scheduler that
composes hooks around `run_agent`**:

```
 TickEngine (scheduler + registry entry; still owns start/stop/pause/resume)
 ============================================================================
   every frequency_sec, while running and not paused:
        |
        |  PRE-FLIGHT (tick-only, unchanged semantics)
        |    providers: fetch executors/positions        engine.py:338
        |    journal readback: learnings/summary/recent  engine.py:356
        |    risk_state = RiskEngine.get_state(journal)  engine.py:363
        |       |-- should_shutdown? --> run_shutdown() ---.
        |       |-- is_blocked?      --> journal + skip     |   SHUTDOWN
        v                                                   |   ESCALATION
   prompt = tick_context(agent, config, data, journal)      |   (scheduler-
        |                                                   |    level hook,
        v                                                   |    NOT in the
   result = await run_agent(agent, prompt,                  |    primitive;
                permission_policy=risk_gate(risk, state),   |    see §3)
                event_sink=..., timeout_s=300,              |
                session=this tick session)                  |
        |                                                   |
        v                                                   |
   POST-RUN (tick-only)                                     |
     journal.record_tick / record_snapshot /                |
     save_full_snapshot / write_summary      engine.py:492  |
        |                                                   |
        v                                                   v
   sleep(frequency_sec)                            deterministic close ->
                                                   LLM cleanup (bounded) ->
                                                   verify + alert -> stop
```

What survives in `TickEngine` after the collapse: the loop/pause/max_ticks
logic, the `_engines` registry entry (load-bearing for stop-all at
`main.py:652` and web liveness), directive injection, the pre-flight, the
post-run journal block, and `_run_shutdown`. What leaves: `_create_client`,
`_collect_stream`, `_active_client` reaping, the model-resolution triad — all
absorbed by `run_agent` (which owns client reaping in its `finally`; the
engine keeps only a handle to cancel the in-flight run on `stop()`).

This is also the enabling move for the deferred external-scheduling design
(strategy-engine doc §1.5–1.6): once a tick is literally "pre-flight +
`run_agent` + write-back", an external cron can drive the same three steps
through tool calls, and the bespoke loop becomes optional rather than
foundational.

## 6. Code change inventory

| File | Change |
|---|---|
| `condor/agents/run.py` (new) | `run_agent()` core + `RunResult`; absorbs `consult._run_agent_to_completion`'s client wiring and `engine._create_client`/`_collect_stream`. Owns model resolution (config > agent), healthcheck/fallback, MCP-server building, streaming, timeout, client reaping, and the refactor-01 session-persistence hook. |
| `condor/agents/policies.py` (new, or fold into `risk.py`) | `human_gate(chat_id)` (wraps `confirmation.permission_callback`), `risk_gate(limits, state, dry_run=False)` (today's `auto_approve_with_risk_check`, unchanged logic — the state seed is the caller's choice, journal-derived for ticks, zero for delegations), `auto`. |
| `condor/agents/consult.py` | Shrinks to: load agent → `build_agent_context` → `run_agent(policy=human_gate)`. Fallback-note logic moves into `run_agent`'s model resolution. |
| `condor/agents/delegate.py` | Runner calls `run_agent(policy=risk_gate(limits, RiskState()) or auto, event_sink=...)`; registry/notify/timeout unchanged. Policy selection per §4.1. |
| `condor/agents/engine.py` | Deletes `_create_client`, `_collect_stream`, `_active_client` plumbing (~150 lines); `_tick` becomes pre-flight → `run_agent(policy=risk_gate)` → journal write-back. Lifecycle, registry, shutdown untouched. |
| `condor/agents/risk.py` | `auto_approve_with_risk_check` becomes `risk_gate` essentially as-is: `execution_mode: str` param → `dry_run: bool`; the checks themselves are untouched. No behavior change for ticks. |
| `condor/agents/prompts.py`, `handlers/agents/_shared.py` | `build_tick_prompt` / `build_agent_context` unchanged in role — they are the two named context builders feeding the one primitive. |
| tests | New: `run_agent` happy-path/timeout/reap tests; policy-lattice tests (esp. zero-seeded `risk_gate` blocking uncapped deploys and `place_order` in a delegation); tick regression tests should pass unchanged — that's the acceptance bar. |

Net effect: one execution stack, ~2 files added, ~200+ duplicated lines
removed, and every future run-level feature (cost caps, tracing, token
budgets) lands once instead of twice.

## 7. Tradeoffs & edge cases

- **Behavior-preserving for ticks and consults by construction** — the
  acceptance bar is that existing tests pass unchanged. The only intended
  behavior change is delegation risk gating (§4.1), which is a tightening;
  call it out prominently since a delegation that previously "worked" (e.g. an
  uncapped deploy) will now be cancelled with a reason.
- **Timeout semantics differ today** (tick: 300s around the stream; delegate:
  900s `wait_for` around the whole run; consult: none). `run_agent` takes
  `timeout_s` explicitly; consult should gain a real timeout (recommend the
  delegate default) instead of hanging a Telegram chat forever — small
  behavior change, strictly an improvement.
- **A zero-seeded `risk_gate` budget is per-delegation, not global.** Two
  concurrent delegations each get the full executor budget. Cross-run
  exposure tracking needs the tick apparatus (journal/controller_id) — out of
  scope; the bot deploy cap (`max_global_drawdown_quote`, enforced
  platform-side) is the real loss bound and *is* enforced.
- **Dry-run blocks live in `risk_gate`** (execution-mode aware) and stay
  tick-only; delegations have no dry-run mode today.
- **`run_once` vs delegation, post-collapse.** Once both are single background
  `run_agent` calls under a zero-ish risk seed (run_once's `_NullTracker`
  state and delegation's `RiskState()` are the same gate), the old storage
  made them look nearly convergent — differing mainly in that the
  *attributed, capital-touching* run_once landed journal-less in `dry_runs/`
  while the *unattributed* delegation got a proper session. Refactor-01 §4
  fixes the storage (run_once = ordinary tick session with `max_ticks: 1`),
  which keeps the boundary semantic and crisp: **tick (any duration) =
  standing playbook + injected market state + journal + `controller_id`
  attribution + track record; delegation = ad-hoc task + transcript,
  unattributed.**
- **Cancellation:** `TickEngine.stop()`'s backstop reap of a mid-await client
  moves into `run_agent`'s `finally`; the engine keeps a cancellable task
  handle. Verify the cancelled-mid-await path with a test — it's the
  subtlest part of the current engine code (`engine.py:159-183`).
- **`event_sink` unification:** delegate's fold-into-events and tick's
  fold-into-tool_calls both come from `fold_tool_call_event`; `run_agent`
  does the folding once and returns both views in `RunResult`.

## 8. Sequencing

1. Extract `run_agent` + `RunResult`; port **consult** to it (smallest
   surface, human gate unchanged).
2. Port **delegate** (still `policy=auto`) — pure refactor, no behavior change.
3. Port **tick** — delete the engine's client code; regression-test against
   existing tick tests.
4. Flip delegation defaults to zero-seeded `risk_gate` per §4.1 (the one
   deliberate behavior change, in its own PR with its own tests).

Do this after refactor-01 lands: the session-persistence hook in `run_agent`
assumes the unified `sessions/` envelope, and the file churn overlaps heavily
(engine/consult/delegate are all touched by both).

## 9. Open decisions (recommendations inline)

1. **Default policy for trading delegations** — zero-seeded `risk_gate` from
   the agent's `default_config.risk_limits` (*recommend*), vs keeping `auto`
   with an explicit per-call opt-in. The current gap is real (§3); silent
   unbounded-capital delegations contradict the risk posture everywhere else.
2. **Should consult get a timeout?** *Recommend yes* (900s) — see §7.
3. **Expose `permission_policy` on the `delegate` MCP tool** (e.g.
   `policy="auto"` override for a trusted long job)? *Recommend not yet* —
   keep the lattice internal until a concrete need appears.

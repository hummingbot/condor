# Technical design: the Strategy Engine and Shared Intelligence

Companion to [`docs/architecture/agent-framework.md`](./agent-framework.md)
(the assistant/agent/skill/routine/strategy ontology and session mechanics
this doc assumes) and [`docs/strategy/roadmap-v2.md`](../strategy/roadmap-v2.md)
§0/Phase 1/Phase 2 (the product plan this doc gives technical shape to).
This doc answers two questions concretely: how does Tier 2 (the Strategy
Engine) actually work, and why doesn't "MCP-first" make it optional; and how
would Tier 3 (the Shared Intelligence store) actually be built, such that
self-hosted installs get it on equal footing with hosted ones.

## 1. The Strategy Engine (Tier 2): architecture

### 1.1 Component diagram

```
┌───────────────────────────────────────────────────────────────────────┐
│                    Tier 1 — Tool Interface (ephemeral)                 │
│         one per chat/harness session; dies when the session ends       │
│                                                                          │
│   Self-hosted: Claude Code / Cursor / any MCP harness the user runs.   │
│   Hosted (Phase 2): Condor's own runtime / OpenClaw / Hermes, chosen   │
│   at provisioning time, co-located on the customer's box, reachable   │
│   by the customer over Telegram (not a client-side harness choice).   │
│                    │ spawns via stdio — always, self-hosted or hosted  │
│                    ▼                                                    │
│   mcp_servers/condor/server.py  (FastMCP)                              │
│     manage_trading_agent(action="start_strategy" | "update_strategy"  │
│                            | "stop" | ...)                              │
│     consult(agent=..., task=...)                                       │
│     delegate(action="start" | "get" | "stop")                          │
│     trading_agent_journal_read / trading_agent_journal_write            │
│     manage_routines / manage_skill / manage_memory   (self-contained,   │
│                                                        no backend call) │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ condor_client.call_main_api(method, path, body)
                           │ mints a JWT for the calling identity,
                           │ HTTP → 127.0.0.1:{WEB_PORT}/api/v1/...
                           │ (self-hosted or hosted: always loopback —
                           │  Tier 1 and Tier 2 are co-located on the same
                           │  box in both deployment shapes)
                           ▼
┌───────────────────────────────────────────────────────────────────────┐
│                Tier 2 — Strategy Engine (persistent process)           │
│   one long-running process per install — self-hosted (user's own       │
│   machine/VPS) or per hosted customer's single-tenant box (§12) —      │
│   survives every Tier 1 session's lifecycle, does not restart when a   │
│   chat session ends or a different harness connects                    │
│                                                                          │
│   condor/web/routes/agents.py  (FastAPI)                               │
│     start_strategy(slug, sslug)  ─────────►  TickEngine registry        │
│     get_delegation_status(task_id)  ◄──────  DelegateTask registry      │
│     (both in-process, in-memory — this is *why* the backend has to     │
│      be one persistent process, not spun up per Tier 1 session)        │
│                                                                          │
│   ┌───────────────────────────────────────────────────────────────┐   │
│   │  TickEngine  (one live instance per running strategy)           │   │
│   │    strategy: Strategy    — loaded ONCE from strategy.md          │   │
│   │    journal:  JournalManager                                     │   │
│   │                                                                   │   │
│   │    loop, every strategy.default_config.tick_interval_s:          │   │
│   │      1. prompt = strategy.instructions (static playbook text)   │   │
│   │              + journal.read_learnings()  (learnings.md, curated)│   │
│   │              + recent session journal.md context                │   │
│   │      2. decision = run a FRESH LLM session against that prompt, │   │
│   │         via strategy.agent_key ("claude-code" | "ollama:model"  │   │
│   │         | "lmstudio:model" | ...) — not a continuation of any   │   │
│   │         prior tick's conversation                                │   │
│   │      3. execute the decision via Hummingbot/Gateway MCP tools    │   │
│   │      4. journal.record_tick(decision, executor results)         │   │
│   │      5. (periodically) fold durable insights into learnings.md  │   │
│   │      until the shutdown-policy sequence fires (deterministic →  │   │
│   │      LLM-decide → verify, architecture doc §5)                   │   │
│   └───────────────────────────────────────────────────────────────┘   │
│                                                                          │
│   On-disk state (survives process restarts, per strategy):             │
│     agents/{slug}/strategies/{sslug}/strategy.md         (static)      │
│     agents/{slug}/strategies/{sslug}/learnings.md        (curated)     │
│     .../sessions/session_N/journal.md                    (per-tick)    │
│     .../sessions/session_N/snapshots/snapshot_N.md       (full replay) │
│     .../dry_runs/experiment_N.md                     (backtest/dry-run)│
└───────────────────────────────────────────────────────────────────────┘
```

### 1.2 Why "MCP-first" stops at the frontend, not here

Walk the request path for a concrete example — a user asks Claude Code
"how's my USDM strategy doing, should we widen spreads?":

1. Claude Code (Tier 1's harness) calls `mcp__condor__consult(agent=
   "usdm_expert", task="...")`.
2. That's the *same* `consult` tool Condor's own Telegram bot would have
   called for the identical question — the tool implementation doesn't
   know or care which harness invoked it.
3. `consult`'s handler calls `condor_client.call_main_api("POST",
   "/agents/usdm_expert/consult", ...)`, which mints a JWT and hits
   `127.0.0.1:{WEB_PORT}` — **Tier 2, the backend web API, which was
   already running before this Claude Code session started and keeps
   running after it ends.**
4. The backend looks up (or creates) the `usdm_expert` strategy's live
   `TickEngine`/journal state, answers using its actual accumulated
   history, and returns.

Now suppose instead the user asks Claude Code to "start the USDM PMM
strategy" and closes their laptop five minutes later. The `start_strategy`
call creates a `TickEngine` **inside Tier 2's process**, which keeps ticking
on its own schedule — no Tier 1 session, no harness, no chat process needs
to be alive for it to keep running. Three days later, a *different* person
opens Cursor, points it at the same Condor MCP server, and calls
`delegate(action="get", task_id=...)` or `consult`s the same agent — they
reach the exact same running `TickEngine` and its exact same accumulated
journal, because that state never lived in any harness's session in the
first place. It lived in Tier 2 the entire time.

This is the concrete meaning of `roadmap-v2.md` §0's claim: **swapping
which harness you use changes nothing about Tier 2**, because Tier 2 was
never part of any harness's session to begin with — it's the one thing in
this whole architecture that isn't ephemeral per-chat state.

### 1.3 The self-hosted vs. hosted difference is only *where the whole stack* runs

Correcting an assumption from an earlier pass: a hosted customer's harness
is **not** remote from their strategy engine. Tier 1 and Tier 2 are
co-located in both deployment shapes — the difference is only whose
machine that co-located pair runs on, and (for hosted) which harness gets
provisioned there.

- **Self-hosted (today, unchanged)**: the user starts Tier 2 themselves
  (e.g. `docker compose up`, or a systemd unit) on their own machine or
  VPS. Tier 1 is spawned by whatever harness they use, and both tiers
  share the same box, talking over loopback/stdio.
- **Hosted (Phase 2)**: Condor operates Tier 2 on a single-tenant box per
  customer (business-strategy.md §12 — no shared multi-tenant fleet for
  this tier specifically, since it holds capital-adjacent state: exchange
  keys and live `TickEngine`s). **A harness — Condor's own runtime,
  OpenClaw, or Hermes — is co-located on that same box**, chosen at
  provisioning time (`roadmap-v2.md` Phase 0), still talking to the MCP
  server over stdio exactly as self-hosted does. No network MCP transport
  is needed in either shape. The customer's actual access point is
  **Telegram** (or another messaging channel), routed to whichever on-box
  harness is running via that harness's own Telegram/messaging integration
  (OpenClaw and Hermes both ship mature Telegram adapters; Condor has its
  own) — not the customer's own local Claude Code/Cursor reaching into a
  remote endpoint. The request path in §1.2 is otherwise identical — the
  on-box harness still just calls `consult`/`delegate`/
  `manage_trading_agent`, which still proxy into the same persistent Tier 2
  process, now over loopback on a Condor-operated box instead of the
  customer's own.

### 1.4 Correction: does Tier 2 need to be *Condor's own bespoke engine*?

§1.1–1.3 described `TickEngine` as if the custom Python class were itself
the load-bearing thing. Reading the actual implementation
(`condor/agents/engine.py`) says otherwise, and it's worth being precise
about what's really required versus what's just today's implementation
choice.

`_tick()` (`engine.py:326-539`) does five things: assemble context (data
providers, `journal.read_learnings()`/`get_recent_decisions()`/
`read_summary()`, risk state), build a prompt, **spawn a fresh client per
tick** — the code's own comment: *"Create a fresh agent client per tick
(clean context window)"* (`engine.py:435`) — stream it, and write the
result (`journal.record_tick`/`record_snapshot`/`save_full_snapshot`/
`write_summary`, plain file writes). Every tick is already a disposable,
one-shot agent session, not a continuous conversation — mechanically
identical to firing off a fresh `claude -p "..."` invocation. That means
this decomposes into two genuinely separable pieces, only one of which is
hard to replace:

- **The scheduling trigger** (`_loop`'s `while self._running: await
  self._tick(); await asyncio.sleep(freq)`, `engine.py:275-324`) — this is
  ordinary, replaceable infrastructure. A cron entry, a systemd timer, or —
  concretely, given `fork-vs-build.md`'s composition option — **Hermes-
  agent's own cron subsystem** (`cronjob_tools.py`, a first-class feature
  there, not something Condor would need to build) could fire a one-shot
  harness invocation on the same schedule with zero new Condor runtime
  code. Some persistent trigger is unavoidable for unattended 24/7
  execution — that's a physical fact about "wake up periodically forever,"
  true for cron's own daemon as much as for `TickEngine` — but it does not
  have to be *Condor's own* bespoke process.
- **Context assembly, risk logic, and journaling** (`ProviderRegistry`,
  `RiskEngine`/`RiskLimits`/the kill-switch check, `build_tick_prompt`, the
  journal writes) — real domain logic, but nothing here requires living
  inside a persistent process either. It's already partially exposed as
  MCP tools (`trading_agent_journal_read`/`_write`); the rest (assemble
  tick context, check risk state and decide shutdown-vs-continue) could be
  packaged as composite tools or routines any one-shot harness invocation
  calls at the start and end of its own turn. That's harness-agnostic
  content, not Condor-proprietary runtime — consistent with
  `mcp-first-value-add.md` §4's point that the durable asset is the
  accumulated journal/learnings content and domain logic, not a scheduling
  wrapper around it.

**The one real gap a naive "just add a cron job" replacement introduces,
concretely, from the code**: `_loop` is strictly serial by construction —
tick, *then* sleep, *then* next tick — so overlapping ticks are impossible
today. Default `frequency_sec` is 60 (`engine.py:276`), but a single tick
can legitimately run up to `asyncio.timeout(300)` (`engine.py:445`) — five
minutes. An unguarded cron entry firing every 60s would happily spawn a
second tick on top of a still-running one, two sessions reasoning about the
same strategy's state and potentially placing conflicting orders
concurrently. Avoiding this needs an explicit lock/PID file a new
invocation checks before proceeding — real, necessary, and not present by
default in the naive version, but a small, well-understood piece of
engineering, not a reason to keep a full custom engine. The other
conveniences a persistent process buys today (`stop()`'s
`asyncio.Task.cancel()` for a fast mid-tick interrupt, `pause`/`resume`,
`inject_directive`'s in-memory queue) have process-based equivalents
(kill-by-stored-PID, a flag file, a small directive file a running tick
checks and clears) that are smaller than they first appear.

**Revised conclusion**: Tier 2 is correctly described as "not optional
under MCP-first" — *some* scheduling trigger plus the risk/journal/content
logic must exist regardless of which harness a human uses. But it is not
correctly described as "Condor must own a bespoke persistent engine
process" — that was overstated in §1.1–1.3. The actually durable,
hard-to-replicate part is narrower: the accumulated journal/learnings
content and the risk/domain logic, not the loop around it. The loop itself
sits in the same "hosted-ops convenience, real but soft and contestable"
category business-strategy.md §10 already named for other layers — it just
hadn't been drawn precisely enough inside Tier 2 until this correction.

**Multi-strategy installs sharpen this further, and reveal a real gap.**
`_engines` (`engine.py:41`, `dict[str, TickEngine]`) is not just a
bookkeeping convenience for a single running strategy — grepping its actual
callers shows `get_all_engines()` is load-bearing in two places that assume
*every* live strategy on an install is enumerable from one in-memory dict
inside one process:

- `main.py:652-658`'s `post_shutdown` handler iterates every engine
  `get_all_engines()` returns and calls `.stop()` on each — this is,
  concretely, today's entire "stop everything gracefully" mechanism for an
  install running N strategies at once. There is no per-strategy `stop_all`
  tool; there's this one loop, over one process's one registry.
- `condor/web/routes/agents.py:309-317, 374-390` uses `get_all_engines()`
  to decide, across *all* running strategies at once, which sessions are
  still "live" (so their PnL must be refetched past the cache TTL) versus
  "closed" (immutable, servable from a frozen cache) — one membership test
  against one in-memory set, not a per-strategy lookup repeated N times.

Both are real, currently-working capabilities that the per-strategy
pluggable tick contract in §1.5–1.6 does not reproduce, because both depend
on exactly one process holding every live `TickEngine` in memory at once —
which stops being true the moment even a single strategy's ticks are farmed
out to an external scheduler, and matters more the more strategies an
install actually runs. A one-strategy install barely notices the gap; a
ten-strategy install with some on native, some on OpenClaw, and some on
Hermes feels it immediately — there is no longer anywhere that knows "here
is everything currently running for this install" without being told to go
look, and no single call that stops all of it.

This does **not** overturn §1.4's core conclusion — a single strategy's
tick sequence is still a disposable, externally-firable unit of work, and
the scheduling loop is still commodity, replaceable one strategy at a time.
What it changes is the *scope* of "what has to exist regardless of
scheduler": alongside the per-strategy tick contract, a multi-strategy
install also needs an install-wide **registry/reconciliation layer** that
the native, single-process design got for free and the pluggable design
does not yet have. See §1.6's new Step 6.

The per-strategy pieces already in §1.5–1.6 don't need to change on account
of this, for one reason worth being explicit about: the `.tick.lock` file
(§1.6 Step 1) is already scoped to the **strategy**, not the process or the
session — `agents/{slug}/strategies/{sslug}/.tick.lock` — which is exactly
right for a world with many concurrently-ticking strategies, each
independently lockable regardless of which scheduler drives it. Multiple
strategies ticking concurrently is also not new behavior introduced by
external scheduling: today's native `_loop` is only serial *within* one
`TickEngine` (`engine.py:275-324`); separate `TickEngine` instances already
run as independent, concurrent asyncio tasks in the same process. What's
new is only the missing cross-strategy view and the missing single
stop-lever, not a new overlap or concurrency risk.

### 1.5 Making the scheduling trigger pluggable: a portable tick contract

**Status: designed, deferred — see §1.6.** The design below stands on its
own merits (§1.4's conclusion holds regardless), but Condor's own
`TickEngine` loop stays the sole scheduler for the current phase; this
section is reference material for when that's revisited, not active work.
Separately, and unaffected by this deferral: OpenClaw, Hermes, and Claude
Code are already usable today as ordinary Tier 1 MCP harnesses — this
section is about who schedules a strategy's recurring *ticks*, not about
which harness a human or hosted box uses to talk to Condor.

Taking §1.4 to its conclusion: if the scheduling loop is commodity, Tier 2's
design should stop assuming it owns the scheduler and instead expose **one
portable unit of work** — a "Condor tick" — that any of several schedulers
can fire, with `TickEngine`'s own `_loop` demoted to "the batteries-included
default," not the only path. Checked directly against three real scheduler
implementations (Hermes-agent's cron subsystem, OpenClaw's cron jobs, and
Claude Code's scheduled-tasks family), the fit is good but not uniform —
worth designing against the real differences rather than assuming they're
interchangeable.

**The portable contract**: a single prompt/instruction, parameterized by
`(agent_slug, strategy_slug)`, that does exactly what `_tick()` does today
(`engine.py:326-539`) via tool calls rather than Python method calls:

1. **Acquire a lock** — a new tool (e.g. `manage_trading_agent(action=
   "tick_lock_acquire", ...)`) backed by a `.tick.lock` file (PID + timestamp)
   under the strategy's own directory. Skip/exit cleanly if already held.
   This has to be **Condor's own mechanism, not a given scheduler's**,
   because it's the only thing that stays correct uniformly across every
   target below, including the case of two different schedulers
   accidentally pointed at the same strategy at once — a real new failure
   mode once scheduling is pluggable rather than singular.
2. **Assemble context** — reuse `trading_agent_journal_read` (already an
   existing MCP tool) for learnings/recent-decisions/summary, plus a new
   composite tool exposing `RiskEngine`'s kill-switch check
   (`engine.py:367`) so any caller — not just `TickEngine`'s own Python
   code — can decide shutdown-vs-continue before reasoning.
3. **Reason and act** via the existing Hummingbot/Gateway MCP tools —
   unchanged.
4. **Write results** — `trading_agent_journal_write` (already exists) for
   the tick/snapshot/summary records `_tick()` currently writes directly.
5. **Release the lock.**

Every step above is already an existing MCP tool, an existing tool that
just needs a small new sibling action, or a thin new tool — none of it is
new architecture, it's repackaging `_tick()`'s existing logic as
tool-callable steps instead of private method calls on a Python object.

**Per-scheduler fit, checked against each one's actual docs:**

- **OpenClaw cron** (`docs.openclaw.ai/automation/cron-jobs`) is the
  closest match. Its `--session isolated` execution style — *"launches a
  fresh agent turn with isolated `cron:<jobId>` session"* — is mechanically
  identical to `TickEngine`'s own "fresh client per tick" design
  (`engine.py:435`), not an approximation of it. Its `maxConcurrentRuns`
  (default 8) and `--run-id`-based dedup ("reusing a `--run-id` while the
  original run is still active reports the duplicate as in-flight instead
  of starting a second run") give a second, scheduler-level line of defense
  against overlap if `--run-id` is set deterministically per strategy (e.g.
  `condor-tick-{agent_slug}-{strategy_slug}`) — on top of, not instead of,
  Condor's own file lock. Concretely:
  ```
  openclaw cron create "*/2 * * * *" \
    "Run the Condor tick for usdm_expert/pmm_mister_operator" \
    --name "condor-tick-usdm_expert-pmm_mister_operator" \
    --session isolated --tools <condor-mcp-tool-allowlist> \
    --run-id condor-tick-usdm_expert-pmm_mister_operator
  ```
- **Hermes-agent's cron subsystem** (`cronjob_tools.py`, `croniter`) is
  architecturally the same shape (a persistent gateway process firing
  scheduled agent runs) — the same portable tick prompt fires there with no
  Condor-side changes, only a Hermes-side cron job definition pointed at
  Condor's MCP server, consistent with `fork-vs-build.md` §6's composition
  option (an unforked Hermes install consuming Condor's tools).
- **Claude Code was considered and is explicitly out of scope.** Checked
  against `code.claude.com/docs/en/scheduled-tasks`, it's a genuinely split
  case: `/loop` is session-scoped (stops when the terminal exits, only
  fires while idle, recurring tasks expire after 7 days regardless) and
  Cloud Routines rule themselves out (no local file access, 1-hour minimum
  interval, incompatible with live Hummingbot/Gateway access on a 60–120s
  cadence). Desktop scheduled tasks would have been the workable target,
  but **product decision: support only OpenClaw and Hermes as external
  schedulers.** Both are architecturally uniform (persistent gateway
  process + cron subsystem + isolated-session execution) in a way Claude
  Code's three-way split isn't, which keeps the adapter surface to
  maintain smaller and more consistent.
- **Condor's own `TickEngine`** remains available and is still the right
  zero-config default for a self-hosted user who hasn't set up any external
  scheduler — this isn't a plan to delete it, it's a plan to stop treating
  it as the only implementation of "the scheduling trigger."

**Net design change**: ship a `condor schedule <agent>/<strategy> --via
{native|openclaw|hermes}` helper that emits the right config/command for
whichever target is chosen, all pointed at the same portable tick contract
above. See §1.6 for the concrete build plan.

### 1.6 Implementation plan: pluggable scheduling (OpenClaw + Hermes) — deferred

**Status: designed, not scheduled.** Condor's own `TickEngine` loop remains
the one and only scheduler for running strategies for now
(`roadmap-v2.md`'s Condor Refactor section) — nothing below is being built
in the current phase. It's kept here, fully specified, as the plan to pick
up if a concrete need arises (a hosted customer whose box already runs
OpenClaw/Hermes for other reasons, or dogfooding hitting a real limit of
the native loop), rather than being built speculatively ahead of that need.

**This deferral is scoped to scheduling only, not to harness support.**
OpenClaw, Hermes, and Claude Code remain fully usable *today*, with no new
work, as Tier 1 harnesses — pointing any of them at Condor's MCP server to
call `consult`/`delegate`/`manage_trading_agent` (§1.2's request-path walk)
works exactly as it does for any other MCP client. What's deferred is
narrower: *who fires a running strategy's recurring ticks*. Put concretely,
using §1.5's own scheduler list — OpenClaw's and Hermes' cron subsystems
firing `strategy_tick` on a schedule is deferred; OpenClaw or Hermes acting
as the interactive harness that answers a `consult` question or starts a
strategy is not, and needs none of the work below.

Sequenced as additions on top of the existing `TickEngine`/journal code —
nothing below removes or breaks the current `execution_mode="loop"` path.

**Step 1 — new granular MCP tools** (`mcp_servers/condor/tools/`), each a
small wrapper around logic `_tick()` already has inline:

- `tick_lock_acquire(agent_slug, strategy_slug) -> {acquired, holder_pid,
  held_since}` / `tick_lock_release(agent_slug, strategy_slug)` — backed by
  a `.tick.lock` file at the **strategy** level (`agents/{slug}/strategies/
  {sslug}/.tick.lock`, not per-session — the thing being protected is "one
  strategy, one in-flight tick," regardless of session numbering),
  containing `{pid, hostname, acquired_at, scheduler, run_id}`. Acquisition
  is an atomic create (`O_EXCL`); a lock older than a generous ceiling
  (e.g. 10 minutes, comfortably above `_tick()`'s existing 300s timeout,
  `engine.py:445`) is treated as abandoned (a crashed invocation that never
  released it) and stolen, with the steal recorded in the journal as an
  error entry so it's visible, not silent. This has to be Condor's own
  mechanism regardless of scheduler — it's the only thing that stays
  correct even if two different schedulers, or native `TickEngine` and an
  external one, are accidentally pointed at the same strategy at once.
- `check_risk_state(agent_slug, strategy_slug) -> RiskState.to_dict()` —
  today `RiskEngine.get_state()` is called against a live, in-process
  `self.journal` (`engine.py:363`); this tool needs to reopen a
  `JournalManager` against the strategy's current `session_dir` from disk
  and construct a fresh `RiskEngine(risk_limits)` from the strategy's saved
  config, then call `get_state()` against that freshly-loaded journal. This
  is the one piece worth explicitly verifying during implementation:
  `RiskEngine.get_state()`'s inputs (exposure/open-executor-count/drawdown,
  the same shape `_NullTracker` mimics) need to be fully reconstructible
  from what the journal already persists to disk — very likely true given
  the journal already tracks these per snapshot, but confirm against
  `journal.py` before assuming it.
- Extend `trading_agent_journal_write` (already exists) to cover the
  tick/snapshot/summary write shapes `_tick()` currently calls directly
  (`record_tick`, `record_snapshot`, `save_full_snapshot`, `write_summary`,
  `engine.py:494-531`), if it doesn't already.

**Step 2 — a new `execution_mode: external_schedule`**, alongside the
existing `loop`/`dry_run`/`run_once` (`engine.py:105-106`,`276-277`):
`start_strategy(..., execution_mode="external_schedule")` runs the
one-time session setup `TickEngine.__post_init__` already does today
(allocate `session_num`, create `session_dir`, save the config snapshot,
construct the `JournalManager`, `engine.py:100-135`) but does **not** spawn
`_loop()` as an asyncio task — it returns the session identity and leaves
subsequent ticks to arrive as external tool calls. A matching
`stop_strategy`/`close_session` action mirrors `TickEngine.stop()`'s
`journal.close()` (`engine.py:159-183`) for a clean finish. Status
reporting (`get_info()`, `engine.py:678-725`) needs a disk-based code path
for this mode — reconstructed from the last-written summary
(`journal.get_summary_dict()`), the lock file (is a tick currently in
flight?), and the most recent journal entry's timestamp — since there's no
live `TickEngine` object in memory to read attributes off of the way
`loop`-mode strategies have today.

**Step 3 — author the portable tick playbook as a global skill**, e.g.
`skills/strategy_tick.md` (repo-global tier, not agent-local) — a natural
fit for and a concrete forcing function on business-strategy.md §11a's
"generalize skills to global+local" proposal (already planned for Phase 1
regardless): one generic playbook, parameterized by `(agent_slug,
strategy_slug)`, callable by any agent's strategy rather than duplicated
per agent. Content: acquire lock → read learnings/recent-decisions/summary
(`trading_agent_journal_read`) → `check_risk_state` (if `should_shutdown`,
run the shutdown skill instead and stop here) → reason and act via
Hummingbot/Gateway tools → write results (`trading_agent_journal_write`) →
release lock. This is `_tick()`'s existing five steps, repackaged as
tool-callable playbook content instead of private Python method calls —
no new domain logic, just a new home for it.

**Step 4 — the `condor schedule` CLI helper**, scoped to the two supported
targets:
- `condor schedule <agent>/<strategy> --via openclaw --interval 2m` calls
  `start_strategy(execution_mode="external_schedule")` to init the session,
  derives the tool allowlist from the strategy's own `skills:` frontmatter
  (already exists) plus the Hummingbot/Gateway tools it needs, and emits
  (or directly shells out to) the `openclaw cron create` invocation:
  ```
  openclaw cron create "*/2 * * * *" \
    "Run the Condor tick for {agent_slug}/{strategy_slug}" \
    --name "condor-tick-{agent_slug}-{strategy_slug}" \
    --session isolated --tools <allowlist> \
    --run-id "condor-tick-{agent_slug}-{strategy_slug}"
  ```
  the deterministic `--run-id` giving a second, scheduler-level overlap
  guard on top of Condor's own file lock (`docs.openclaw.ai/automation/
  cron-jobs`'s own dedup semantics: reusing an in-flight `--run-id` reports
  the duplicate as in-flight rather than starting a second run).
- `condor schedule <agent>/<strategy> --via hermes --interval 2m` — same
  session-init step, then registers the equivalent job against Hermes'
  cron subsystem (`cronjob_tools.py`). The exact registration call needs
  verifying against Hermes' actual tool/API signature at implementation
  time rather than assumed here — treat this as the one open unknown in
  this plan, not a solved detail.
- `condor schedule <agent>/<strategy> --via native` — today's
  `execution_mode="loop"` path, unchanged, still the default.

**Step 5 — validate before touching live capital**: dry-run the OpenClaw
path first against a QA bot (already a planned Phase 1 dogfooding item),
not a client mandate — confirm the lock/steal logic, the risk-check
tool's correctness against `TickEngine`'s existing behavior, and status
reporting, before pointing an external scheduler at anything running real
capital. Validate the Hermes path the same way, separately, since its
cron registration path is the one unverified integration point above.

**Step 6 — a cross-strategy registry and "stop everything," for installs
running more than one strategy.** Not needed for a single-strategy
install — native `TickEngine`'s in-memory `_engines` dict (`engine.py:41`)
already covers that case unchanged. Required once an install runs several
strategies, potentially split across native/OpenClaw/Hermes (per §1.4's
correction above), to replace what `get_all_engines()` gives away for free
today (`main.py:652-658`'s stop-all loop, `agents.py:309-317, 374-390`'s
liveness check for perf caching):

- **Registry**: a small on-disk index — e.g. one
  `.condor/schedule_registry.json` per install — recording, per
  `(agent_slug, strategy_slug)`: which scheduler currently owns its ticks
  (`native` / `openclaw` / `hermes`), the external job identifier if any
  (the OpenClaw cron job name, Hermes' job id), and last-known state
  (sourced from the existing `.tick.lock` plus the latest journal entry,
  the same disk-based fallback Step 2's `get_info()` path already needs).
  Written once by `condor schedule ... --via ...` at registration time;
  read whenever an aggregate view is needed. For native-mode strategies the
  registry entry is redundant with `_engines` and mostly there for a
  uniform read path — the in-memory dict stays authoritative for those.
- **Aggregate status**: a `list_running_strategies()`-style tool/CLI
  command standing in for what `get_all_engines()` gives the current code
  for free — native entries read the in-memory registry as today;
  externally-scheduled entries read the on-disk registry plus, optionally,
  a live call to that scheduler's own job-listing API (`openclaw cron
  list`, Hermes' equivalent) to catch drift — e.g. a job someone disabled
  outside Condor's own tooling, which the on-disk registry alone wouldn't
  detect.
- **Stop-all**: a `condor schedule stop-all` (or equivalent MCP/API action)
  that walks the registry and calls the right stop path per backend —
  native `stop_strategy`, `openclaw cron disable <name>`, Hermes' cron-
  disable equivalent — restoring, across mixed scheduler backends, the
  single-lever behavior `main.py:652-658` gives for free today within one
  process.

This is new surface on top of Steps 1–5, not a re-derivation of them —
worth sequencing as its own item precisely because the per-strategy tick
contract alone does not scale to a multi-strategy, multi-scheduler install
without it.

## 2. Shared Intelligence (Tier 3): architecture

### 2.1 Component diagram

```
┌────────────────────────────┐        ┌─────────────────────────────┐
│ Install A — self-hosted      │        │ Install B — hosted (Phase 2) │
│  Tier 2: user's own box      │        │  Tier 2: Condor single-      │
│   TickEngine → learnings.md  │        │  tenant customer box          │
└──────────────┬──────────────┘        │   TickEngine → learnings.md   │
               │                        └───────────────┬───────────────┘
               │ opt-in per strategy: `share_learnings: true`
               │ in strategy.md frontmatter (off by default)
               ▼                                        ▼
      ┌──────────────────────────────────────────────────────────┐
      │   Redaction pipeline — runs INSIDE Tier 2, before          │
      │   anything leaves the install (self-hosted or hosted)     │
      │     - strip wallet/account identifiers, exact position     │
      │       sizes, exchange account IDs (structured strip)       │
      │     - generalize free text (a dedicated LLM redaction      │
      │       pass — reuses the same `agent_key`-driven session     │
      │       machinery as a tick, not new infra)                  │
      │     - attach tags: agent_type, exchange/venue, regime       │
      │     - attach reputation signals: strategy tenure, uptime,   │
      │       real-capital flag, optional Swig wallet pubkey        │
      └───────────────────────┬────────────────────────────────────┘
                              │ outbound HTTPS, per-install contributor
                              │ token (pseudonymous, not tied to a
                              │ specific user identity)
                              ▼
      ┌────────────────────────────────────────────────────────────┐
      │   Tier 3 — Condor Learnings API                              │
      │   ONE central, ordinary multi-tenant service — not gated       │
      │   by §12's single-tenant rule, because this tier holds no       │
      │   capital and no exchange credentials, only redacted text/stats │
      │                                                                   │
      │   POST /v1/learnings/submit                                      │
      │     → validate against the redaction contract, store             │
      │   storage: structured fields (tags, stats) + a vector index       │
      │            (embeddings over redacted text, for semantic search)   │
      │   reputation ledger: running score per pseudonymous contributor   │
      │                                                                     │
      │   GET/POST /v1/learnings/query {tags, agent_type, regime, ...}     │
      │     - aggregate-stats results: open to any opted-in install         │
      │     - textual-learning results: gated on contributor reciprocity     │
      └───────────────────────┬──────────────────────────────────────┘
                              │
                              ▼
   any Tier 2 instance (self-hosted OR hosted) calls the new MCP tool
   `query_shared_learnings(tags, agent_type, regime, ...)` — from inside a
   TickEngine's own tick reasoning, or from an interactive consult session
   via any Tier 1 harness — same tool either way
```

### 2.2 Why self-hosted installs get this on equal footing

The load-bearing architectural fact: **Tier 3 is always one central,
Condor-operated service, regardless of where Tier 2 runs.** A self-hosted
Tier 2 talks to Tier 3 over an ordinary outbound HTTPS call — no different
in kind from a self-hosted install already calling an external market-data
API today. There's no dependency on Condor hosting the *execution* layer
for a self-hosted user to participate in the *intelligence* layer — the two
tiers are decoupled by design, which is exactly what makes "self-hosted
gets this too" a real, cheap-to-honor design choice rather than a
concession that costs Condor something.

### 2.3 Concrete mechanics

**Opt-in, in `strategy.md`'s frontmatter** (architecture doc §4's existing
format):

```yaml
name: usdm_pmm_operator
agent_key: claude-code
share_learnings: true          # off by default; opts this strategy in
learnings_endpoint: default    # "default" = Condor's public Tier 3;
                                # overridable to a private org endpoint
                                # for a team that wants sharing scoped to
                                # its own strategies only
```

**Submission payload shape** (illustrative):

```json
{
  "contributor_id": "pseudonymous-install-token",
  "agent_type": "market_making",
  "venue": "orca",
  "regime_tags": ["high_funding_rate", "low_liquidity"],
  "reputation_signals": {
    "tenure_days": 47,
    "uptime_pct": 99.2,
    "real_capital": true,
    "swig_wallet_pubkey": "optional, for on-chain verification"
  },
  "content": {
    "kind": "textual_learning",
    "text": "Widening spreads ~2x during sustained high-funding-rate periods reduced adverse selection without materially cutting fill rate."
  }
}
```

**Redaction is a pipeline stage, not a formality** — it runs inside Tier 2
before submission, using the same `agent_key`-driven LLM session machinery
the tick loop already uses (no new runtime needed, just a different
prompt): strip anything structurally identifying (wallet addresses,
account IDs, exact position sizes) via deterministic rules first, then a
generalization pass on free text, consistent with the confirmation-gated
philosophy the rest of the architecture already follows (`consult` vs.
auto-approved `delegate`, architecture doc §1) — sharing is the kind of
action that should default to reviewable, not silently automatic.

**Reciprocity and reputation, concretely**:

- Two response tiers per query: aggregate statistics (e.g. "N strategies
  tagged `market_making` + `high_funding_rate` report spread-widening as a
  common response") available to any opted-in install; the richer textual
  learnings reserved for installs with a positive net-contribution balance
  — a simple ledger (submissions accepted minus queries against the
  textual tier), not a payment system.
- **Reputation weighting could eventually use Swig as a non-forgeable
  signal** — noted here as a design option, not a near-term plan: Swig
  integration is currently deferred out of the active roadmap
  (business-strategy.md §9's status note, `roadmap-v2.md`). If it's
  revisited later, a contributor could optionally attach their strategy's
  Swig wallet public key, and Tier 3 could verify basic on-chain facts —
  account age, transaction count/volume in the relevant program — entirely
  from public chain data, with no private information disclosed, as a
  real-capital/tenure signal harder to fake than a self-reported
  `real_capital: true` flag. Until then, reputation weighting relies on the
  self-reported/tenure signals above only.

**The new MCP tool** is a thin addition to the existing surface:

```
query_shared_learnings(tags: list[str], agent_type: str,
                        regime: list[str] | None = None) -> dict
```

Callable identically from a `TickEngine`'s own tick reasoning (cold-start
case: a new strategy queries before it has any private history of its own)
or from an interactive `consult` session via any Tier 1 harness — same tool
surface, same backend, whether the caller is a fully automated tick loop or
a human asking Condor a question through Claude Code.

### 2.4 Risk containment, restated as design constraints

- **Herding**: query results are *context*, not commands — a `TickEngine`'s
  own LLM session interprets a shared learning contextually against its
  own strategy's playbook (architecture doc §4), it doesn't mechanically
  execute on it. Consider rate-limiting how quickly a newly published
  learning becomes queryable, to avoid many strategies converging on a
  brand-new, unvalidated signal simultaneously.
- **Leakage**: the redaction pipeline is the actual control; treat it as
  security-relevant code requiring the same scrutiny as anything touching
  exchange credentials, not routine feature work.
- **Regulatory**: legal review of the paid contributor-tier mechanism
  (investment-adviser-adjacent, `business-strategy.md` §13) has to happen
  before Tier 3 goes live with real submissions, not after.

### 2.5 Implementation plan

**Step 1 — stand up the Tier 3 service.** A new FastAPI service (reusing
the same patterns as `condor/web/routes/agents.py`, but a separately
deployed process — this is deliberately not another route module on a
per-install Tier 2 backend, since it's one central service regardless of
how many installs exist). Data model:

- `contributors`: `contributor_id` (a pseudonymous token generated locally
  at first opt-in and stored in the install's own config — never derived
  from a Telegram user_id, email, or exchange account identifier),
  `reputation_score`, `tenure_days`, `real_capital_verified`,
  `swig_wallet_pubkey` (optional), `created_at`.
- `submissions`: `submission_id`, `contributor_id`, `agent_type`, `venue`,
  `regime_tags`, `kind` (`aggregate_stat` | `textual_learning`), `content`,
  `embedding` (for `textual_learning` rows only), `redaction_version`
  (which redaction-pipeline version processed it — needed for auditability
  if the redaction logic is later found to be insufficient and past
  submissions need review), `created_at`.
- `query_ledger`: `contributor_id`, `submissions_accepted`,
  `textual_queries_used`, `textual_queries_quota` — a simple counter-based
  reciprocity mechanism, not a payment system.

Storage: structured tables can live in whatever the existing backend
already uses (this repo has a root `condor.db`, suggesting SQLite is the
current default) for the `contributors`/`query_ledger`/aggregate-stat rows;
`textual_learning` rows need a vector-searchable index for the semantic
query case (§2.1) — either a SQLite vector extension or a small dedicated
vector store, decided alongside Phase 0's local-LLM-hosting research
(`roadmap-v2.md` Phase 0) rather than assumed here, since both are
"pick a serving/storage stack" decisions best made together.

**Step 2 — the redaction pipeline** (runs inside Tier 2, per install, not
inside Tier 3):

1. Deterministic strip (a plain routine, no LLM call): regex/structural
   removal of wallet addresses, exchange account identifiers, and exact
   numeric position sizes — replace absolute sizes with a relative
   descriptor (e.g. "large relative to this strategy's typical size")
   rather than dropping the observation entirely.
2. LLM generalization pass on free text, reusing the same `agent_key`-
   driven session machinery a tick already uses (so a local-model install,
   per Phase 2's design, redacts locally too — no dependency on a frontier
   API call for something that runs periodically, not per-tick).
3. **Staged review before first submissions, not full manual gating
   forever**: for a strategy's first N shared entries, write the redacted
   candidate to a local `pending_share/` file for the operator to glance at
   (dashboard or CLI diff) before it's sent — consistent with the existing
   confirmation-gated philosophy (`consult` vs. auto-approved `delegate`).
   Once redaction quality is validated for a given strategy, allow
   switching to auto-submit — full manual review forever would kill the
   flywheel this is meant to build; fully silent from day one is too much
   risk given real named clients (business-strategy.md §1).
4. Trigger cadence: watch `learnings.md` for new entries specifically, not
   the raw per-tick journal — `learnings.md` is already the curated
   distillation layer (architecture doc §4); most ticks don't produce a
   durable "learning" worth sharing, and submitting on every tick would be
   noisy and mostly wasted.

**Step 3 — the MCP tool** (`mcp_servers/condor/tools/learnings.py`, new
file): `query_shared_learnings(tags, agent_type, regime, tier="aggregate"|
"textual") -> dict`, calling Tier 3's `GET/POST /v1/learnings/query` with
the install's contributor token. Submission is deliberately *not* an
LLM-invoked tool call per tick — it's the redaction pipeline's own
periodic job (step 2.4 above), decoupled from the tick loop entirely.

**Step 4 — Swig-based reputation verification, deferred**: not currently
planned, since Swig integration itself is out of the active roadmap for
now (business-strategy.md §9's status note). If Swig is revisited later,
the natural follow-on is a small on-chain-read utility — reusing existing
Gateway/Solana connectivity — that, given an optionally-attached Swig
wallet pubkey, fetches account age and transaction volume in the relevant
program from public chain data and folds it into `reputation_score`. Not a
Tier 3 launch blocker either way; reputation scoring launches on the
self-reported/tenure signals in §2.3 alone.

**Step 5 — sequencing, lowest-risk first**:
1. Stand up Tier 3 with the query path only, seeded with synthetic data —
   validate the query tool and semantic-search quality before any real
   submissions exist.
2. Build the redaction pipeline and dogfood submission using Condor's own
   QA bots' learnings first (already a planned Phase 1 dogfooding item) —
   zero client-data exposure, since QA bots don't operate real client
   mandates.
3. Legal review of the contributor-tier/paid-access mechanism
   (business-strategy.md §13) — a hard gate before step 4.
4. Enable opt-in for real dogfooded strategies (USDM, etc.) only after
   redaction quality is validated in step 2 and legal review (step 3) is
   complete.
5. Open opt-in broadly to self-hosted installs — the "self-hosted gets
   this too" commitment from §2.2, honored last precisely because it's the
   easiest to honor once steps 1–4 have de-risked the pipeline: a self-
   hosted install talks to the same, already-validated Tier 3 service over
   the same outbound HTTPS call, no separate integration work required.

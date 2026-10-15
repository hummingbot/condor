---
id: FEAT-137
title: Persist a per-session last reading and last successful action, so quiet and dead can be told apart
summary: _last_tick_at is set at tick start and lives in memory, and actions.latest_action records the last tool call rather than its result, so nothing says when a session last got a reading or last did something that worked. Persist both through record_tick and show them beside the bot list.
status: done
effort: M
risk: low
brain: developer
new_files:
  - tests/test_liveness_timestamps.py
touched_files:
  - condor/runtime/loops.py
  - condor/agents/engine.py
  - condor/agents/actions.py
  - condor/agents/performance.py
  - condor/agents/providers/executors.py
  - condor/agents/fleet_map.py
  - condor/web/routes/agents.py
  - frontend/src/lib/api.ts
  - frontend/src/lib/agent-attribution.ts
  - frontend/src/components/perf/AgentScopeHeader.tsx
  - tests/test_session_pnl_attribution.py
depends_on:
  - "[[CORR-725]]"
commits:
  - "599101f4 feat(agents): record when a session last read and last did something that worked (FEAT-137)"
created: 2026-10-14
closed: 2026-10-15
---

## Objective

Every session records when it last obtained a reading and when it last completed
an action that had an effect — an order placed, a cancel sent, an executor
created — and both are visible beside the bot list.

## Context and constraints

This is proposal 4 of GitHub issue
[#272](https://github.com/hummingbot/condor/issues/272), and the signal
[#64](https://github.com/hummingbot/condor/issues/64)'s race comment asks for
first: "**Per-strategy last successful action** (order placed, cancel sent,
executor created) next to each bot, so quiet and dead can be told apart."

It is the other half of [[CORR-725]]. That item stops "no reading" being scored
as zero; it cannot by itself distinguish a market maker that is legitimately
quiet from one whose strategy died, because neither produces a reading that
differs. The timestamp is what separates them, and it is why
[[CORR-725]] carries a criterion that an idle-but-healthy strategy must not
alert.

Confirmed at `45e60d60`:

- `_last_tick_at` (`condor/agents/engine.py:564`) is set at tick **start**, so
  it advances even when every call in the tick failed, and it lives in memory,
  so it does not survive a restart.
- `actions.latest_action()` (`condor/agents/actions.py:352`) records the last
  tool *call*, not its result — a refused or failed call advances it.
- `LoopSupervisor.record_tick` (`condor/runtime/loops.py:144`) is the existing
  per-tick persistence seam, writing into `status.json`.

So the two quantities that matter are precisely the two nothing records: a tick
that *obtained data*, and a call that *succeeded*.

## Alternatives considered

1. **Reuse `_last_tick_at`.** Rejected on the measurement above: it is set at
   tick start, so a session whose every call fails looks as fresh as a healthy
   one — which is the #272 failure mode exactly.
2. **Reuse `latest_action()`.** Rejected: it records the call, not the outcome,
   so a tick spent being refused advances it.
3. **Infer liveness from the performance reading's own timestamp.** Rejected:
   the #272 case is an empty performance object, so there is no timestamp to
   read — the absence is the thing being measured.
4. **Record both quantities at the points that know the outcome, persist them
   through `record_tick`.** Chosen.

## Decision

Alternative 4. `last_reading_at` is stamped when a tick obtains a non-empty
reading; `last_successful_action_at` is stamped when a mutating call returns
success. Both are persisted through `record_tick` into `status.json` and exposed
beside the bot list.

## Design

- The two stamps are written at the points that know the outcome — the reading
  stamp where [[CORR-725]]'s tri-state is resolved (so `ok` stamps and
  `empty`/`missing` do not), the action stamp where a mutating tool call's
  result is observed.
- "Successful action" is defined by an explicit set of mutating effects — order
  placed, cancel sent, executor created, bot deployed — rather than "any call
  that did not raise", so a successful *read* does not make a dead strategy look
  alive. This is the distinction alternative 2 fails on, and it is the one worth
  stating in the doc.
- Both are persisted through `record_tick` (`loops.py:144`), the existing seam,
  so they survive a restart and are readable without a live engine.
- Exposed on the agent/bot payload and rendered beside the bot list as ages.
  [[FEAT-141]] is the fleet view that uses them; this item is the signal and its
  single-bot display.

## Implementation plan

1. Define the mutating-effect set and stamp `last_successful_action_at` where
   those results are observed.
2. Stamp `last_reading_at` on an `ok` reading, using [[CORR-725]]'s tri-state.
3. Persist both through `record_tick` into `status.json`.
4. Expose on the route payload; render ages beside the bot list.
5. Tests per the criteria below.

## Acceptance criteria

- [x] A tick that obtains a non-empty reading advances `last_reading_at`; a tick whose reading is empty or missing does not
- [x] A tick whose every call fails advances neither stamp, where `_last_tick_at` advances today
- [x] A successful read-only call does not advance `last_successful_action_at`; an order placed, a cancel sent, an executor created and a bot deployed each do
- [x] Both stamps survive a process restart, read back from `status.json` with no live engine
- [x] Both are shown beside the bot list as ages
- [x] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Risks and notes

- Verified at `45e60d60` with `git show HEAD:<path>` for all three
  citations, including that `_last_tick_at` is assigned at tick start.
- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60`
  with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0
  failed in 98.1s, EXIT=0**. Provenance, not a threshold.
- `depends_on: [[CORR-725]]` — the reading stamp is defined by that item's
  tri-state, and building it first would mean guessing what counts as a reading.
- The third criterion is the one that makes this worth building rather than
  reusing an existing field. Both cheap reuses fail on outcome-vs-attempt, and
  that is the whole distinction the race needed.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.24
  `CORR-633 [done]`, below the 0.50 fallback bar.

- **Shipped in `599101f4`.** Two stamps, each written where the outcome is
  known, and nowhere else. `last_reading_at` is set in `_gather_tick_context`
  when the executors provider reports `reading == "ok"`; `empty`, `missing` and
  an absent key (the provider's own error path) all leave it where it was.
  `last_successful_action_at` is set in `_persist_session` from
  `actions.last_effect_at` over the same folded rows the action log is written
  from, so the stamp and the log cannot disagree about what the session did.
  Both are persisted by `LoopSupervisor.record` into `status.json` beside
  `pins`, re-written every tick.
- **`AgentPerformance.reading`** is the new carrier of [[CORR-725]]'s tri-state
  at the agent level — the same shape as `fees_known` and `liveness_known`, so
  existing readers are untouched. It defaults to `ok` because an executor-only
  agent's book comes from the executor table, which either answered or raised;
  the bot-mode loop in `fetch_agent_performance_batch` resets it to `missing`
  and raises it with `_best_reading` per **live** bot aggregate. A stopped
  instance's frozen snapshot deliberately does not count: that is last week's
  reading, not this tick's. `test_an_executor_only_agent_reads_ok_because_its_table_answered`
  pins the carve-out, which exists so an executor-mode session does not read as
  dead the moment it starts — the false positive [[CORR-725]]'s fifth criterion
  is about.
- **Why `EFFECT_VERBS` and not "any call that did not raise".** That is design
  alternative 2's failure, and the third criterion is the one that makes this
  item worth building. The recordable set includes a saved controller config, a
  routine written, a token mapping edited, an agent paused — a dead market
  maker's operator can do all of those. The set is the four effects #64's race
  comment names plus their DEX equivalents (a signed swap, every liquidity
  move), and the liquidity halves are spelled off `danger`'s own
  `MUTATING_CLMM_ACTIONS` / `MUTATING_AMM_ACTIONS` so a new action added there
  cannot silently drop out of this one.
- **Where they render.** `livenessFacts` (pure, in `lib/agent-attribution.ts`)
  renders the two as ages on their own line in `AgentScopeHeader`, the band
  directly above the bot list on `/bots` and in the workspace fleet. Kept out of
  `loopFacts` on purpose: that band answers "when does it tick next", which is a
  schedule, and a dead strategy's ticks are due just as punctually as a healthy
  one's. 0 renders as **never**, never as 1970 — "never acted" is the normal
  state of a market maker between deploys and is itself an answer.
- Four files beyond `touched_files:`. `condor/agents/performance.py`,
  `condor/agents/providers/executors.py` and `condor/agents/fleet_map.py` carry
  the reading state and the stamps to the tick and to the fleet map, which the
  design's "persist both through `record_tick`" and "expose on the agent/bot
  payload" require. `tests/test_session_pnl_attribution.py` is ARCH-690's guard
  that `AgentPerformanceModel` covers every `AgentPerformance` field: adding
  `reading` to the dataclass made it fail until the wire model carried it too,
  which is the guard doing its job. Six frontend test files gained
  `lastReadingAt`/`lastSuccessfulActionAt` defaults in their `LiveLoop`
  factories, the same housekeeping `lastDid` needed when it was added.
- `condor/runtime/registry_file.py` is deliberately **unedited**:
  `write_status` already merges arbitrary `**fields`, so the two stamps needed
  no schema change — which is exactly why `record_tick` was the right seam.
- Verification: `uv run pytest` = **6589 passed, 2 skipped, 0 failed in 102.8s**
  on the change, against **6561 passed, 2 skipped, 0 failed in 126.3s** at the
  merge base `e7bcd57b` (throwaway detached sibling worktree, `frontend/dist`
  linked, removed afterwards). +28 is exactly the 28 tests this item adds;
  nothing newly fails ([[CORR-414]]). `black --check` / `isort --check-only`
  clean on all eight Python files; the 10 `ruff` findings over
  `engine.py` and `web/routes/agents.py` (one `F401` on `Heartbeat`, three
  `F401` on unused imports, six `F821` on `TYPE_CHECKING`-only names) reproduce
  identically at `e7bcd57b` and are all on lines this item did not touch.
  Frontend: `tsc -b --force` exit 0, `eslint` clean on all nine touched files,
  `vitest` 95 passed over the six touched test files.
- [[FEAT-141]] is the fleet view that reads both stamps across every loop at
  once; this item is the signal and its single-loop display.

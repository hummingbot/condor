---
id: CORR-725
title: A bot with 0 controllers or an empty performance object reads as running, and "no reading" is scored as zero
summary: An empty performance object yields 0 controllers while the bot still reads running, a controller with no status field defaults to running, _aggregate_by_bot coerces missing fields with float(... or 0), and get_drawdown_pct returns 0.0 with no snapshots — so a dead strategy reads as flat P&L inside its limits.
category: correctness
impact: high
effort: M
risk: medium
status: done
brain: developer
files:
  - condor/fetchers/bots.py:112
  - condor/fetchers/bots.py:129
  - condor/fetchers/bots.py:214
  - condor/fetchers/bot_performance.py:126
  - condor/agents/journal.py:959
  - condor/agents/risk.py:275
depends_on:
  - "[[CORR-723]]"
commits:
  - "4bc3dc30 fix(bots): tell \"no reading\" from zero in the bot row, the aggregate and the drawdown guard (CORR-725)"
created: 2026-10-14
closed: 2026-10-15
---

## Problem

GitHub issue [#272](https://github.com/hummingbot/condor/issues/272) cost one
race entry about **4h45m**: a strategy stopped on its own drawdown guard, its
container stayed up, the bot record still said `running`, and the controller
returned an empty performance object. Condor read that as a healthy agent.
[#234](https://github.com/hummingbot/condor/issues/234)'s race comment states
the same rule from the other side — "**'No reading' is unknown, not zero.**"

Every step confirmed at `45e60d60`:

- `condor/fetchers/bots.py:112-113` takes `status` from the orchestrator as it
  comes. With an empty `performance` dict the controller loop (`:123`) never
  iterates, so the bot is emitted with `num_controllers: 0` (`:219`) and
  `status: "running"`. Nothing flags the combination.
- a controller carrying no `status` field defaults to `"running"`
  (`bots.py:129`, `ctrl_status = ctrl_info.get("status", "running")`).
- `_aggregate_by_bot` (`bot_performance.py:126-147`) coerces a missing or
  non-dict `performance` to `{}` and then reads every field as
  `float(perf.get(..., 0) or 0)` — realized, unrealized, volume, fees. "No
  data" and "flat" become the same number.
- `get_drawdown_pct` (`journal.py:959-961`) returns `0.0` when there are no
  snapshots, and `RiskEngine` (`risk.py:275-320`) compares that 0 against the
  drawdown limits. The guard fails closed only when the tracker *raises*, not
  when it reports nothing — so a strategy that has stopped producing readings
  reads as a strategy sitting safely at zero drawdown.

The consequence is not only a wrong display - it is that the guard designed to
notice a dying strategy is handed the one value that always passes.

## Proposed solution

1. Make the reading tri-state at the point it is built. `_aggregate_by_bot` and
   the bot-universe fetch distinguish `ok` (a dict with fields),
   `empty` (a dict with none) and `missing` (absent or not a dict), and stop
   coercing the latter two to 0.0. Carry the state on the aggregate rather than
   changing the numeric fields' type, so existing readers keep working.
2. `RiskEngine.get_state` treats an owned bot with no fresh reading as unknown
   and alerts or blocks, the way it already does when the tracker raises
   (`risk.py:286-293`) — the same fail-closed arm, reached by the reporting
   path as well as the raising one.
3. Flag "running with 0 controllers" explicitly on the bot row
   (`bots.py:214-222`) so the dashboard and the agent's own context can show
   it, instead of each reader re-deriving it from two fields.
4. Stop defaulting a controller with no `status` to `"running"` (`:129`) —
   an absent status is unknown, and [[CORR-633]] already records that an
   archived instance's snapshot says `running` on its own.

#272's fourth proposal — persisting `last_reading_at` /
`last_successful_action_at` — is additive signal rather than a correction, and
is [[FEAT-137]].

## Acceptance criteria

- [x] A bot whose performance object is `{}` is emitted with `num_controllers: 0` and an explicit flag that it is running with no controllers, not a bare `status: "running"`
- [x] `_aggregate_by_bot` given a missing, non-dict and empty performance reports `missing`/`missing`/`empty` respectively, and none of the three reports realized/unrealized/volume as 0.0-meaning-flat
- [x] A controller entry with no `status` field no longer reads as `running`
- [x] `RiskEngine.get_state` for an owned bot with no fresh reading alerts or blocks instead of comparing 0.0 against the drawdown limits
- [x] An owned bot that genuinely is flat (snapshots present, P&L 0) still reads `ok` and does not alert, so the change does not make a quiet strategy look dead
- [x] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Notes

- Verified at `45e60d60` with `git show HEAD:<path>` for all six citations.
- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60` with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0 failed in 98.1s, EXIT=0**. Quoted as provenance, not as a threshold — unrelated items add tests.
- `depends_on: [[CORR-723]]` - that item decides how an unverified reading is
  carried, and this one has to agree with it rather than inventing a second
  unknown state. Both touch `condor/fetchers/bot_performance.py`.
- The fifth criterion is deliberate - the easy version of this fix makes every
  idle market maker alert, which is the failure mode that gets a guard switched
  off. Telling "quiet" from "dead" needs [[FEAT-137]]'s timestamps; this item
  only has to stop scoring absence as zero.
- #272 also asks for the dashboard half - that is [[FEAT-141]], which renders
  this flag among the other fleet signals.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.28
  `CORR-633 [done]`, below the 0.50 fallback bar; no open doc covers it.

- **Shipped in `4bc3dc30`.** Four seams, one rule — absence is never zero:
  `_reading_state` in `bot_performance.py` classifies each snapshot `ok` /
  `empty` / `missing` and stamps it on the per-controller row and, rolled up by
  `_best_reading`, on the bot aggregate; `build_bots_page` stamps
  `running_without_controllers` on the bot row; `ctrl_status` defaults to
  `"unknown"` instead of `"running"`; and `JournalManager.get_drawdown_pct_checked`
  returns `(pct, known)`, which `RiskEngine.get_state` routes into the same
  fail-closed arm the raising tracker already reaches.
- The numeric fields deliberately keep their type and their 0.0, as
  `## Proposed solution` 1 asks — the state travels alongside them
  (`reading` on the aggregate, `drawdown_known` on `RiskState`), the same shape
  [[CORR-723]] used for `liveness_known` and `fees_known`, so every existing
  reader keeps working. `test_aggregate_reports_the_reading_state_tri_state`
  asserts both halves: all four shapes report 0.0, and only `reading` tells
  them apart.
- `_merge_instance_aggregates` pools `reading` with `_best_reading` as well as
  the figures. Without that, a redeployed base whose name-bearing instance
  reported nothing would hide a live instance that did.
- **New-session carve-out, and why it is not a hole.** `get_drawdown_pct_checked`
  reports `known=False` only when the Snapshots section is empty *and*
  `tick_count > 0`. Before the first tick there is nothing to have fallen from,
  and `record_snapshot` runs at the *end* of a tick (`engine.py:984`) — so a
  session blocked on its own missing history could never take the tick that
  writes its first snapshot. `test_a_session_that_has_not_ticked_yet_is_not_blocked`
  pins it. On tick 1 nothing else can set `is_blocked` (`_apply_drift_verdict`
  sets `book_trusted`, not `is_blocked`, and the drawdown is 0.0 against the
  limits), so the blocked-tick path that records a tick without a snapshot
  cannot be reached before the first snapshot exists.
- `RiskEngine.get_state` branches on `get_drawdown_pct_checked` only when the
  tracker has it, falling back to the flat `get_drawdown_pct`. `_NullTracker`
  (experiments) and the `_DrawdownOnly` tracker in
  `tests/test_journal_read_cache.py` therefore behave exactly as before — a
  tracker that can only return a number has not reported an absence.
- `is_blocked` is the soft pause: the engine journals `tick_blocked`, notifies
  once per distinct reason, and skips the turn. It is **not** consulted by the
  per-call `check_*` gates, so `shutdown.py:343`'s gated client is unaffected
  and a winddown still gets its brakes.
- Two files beyond `files:`. `condor/web/models.py` gained
  `BotSummary.running_without_controllers` — without it the REST body would
  drop the flag the builder sets, i.e. keep every reader re-deriving it; the
  criterion's test asserts it survives `BotsPageResponse`. `frontend/src/lib/api.ts`
  gained the matching optional field so the wire contract is declared on both
  sides; nothing renders it yet, which is [[FEAT-141]].
- `condor/fetchers/bots.py:112` (`bot_status` taken from the orchestrator as it
  comes) is deliberately **unedited**. The orchestrator's `running` is not
  wrong — the container really is up — and rewriting it would lose the
  distinction between "stopped" and "up but idle". The fix is the flag beside
  it, which is what `## Proposed solution` 3 asks for.
- Verification: `uv run pytest` = **6450 passed, 2 skipped, 0 failed in 97.0s**
  on the change, against **6439 passed, 2 skipped, 0 failed in 119.8s** at the
  merge base `698aecbe` (throwaway detached sibling worktree, `frontend/dist`
  linked). +11 is exactly the 11 tests this item adds; nothing newly fails
  ([[CORR-414]]). `black --check` / `isort --check-only` clean on all eight
  Python files; the 10 `ruff` findings over them (`E741` on `l` in
  `journal.py`, two `F401` in `models.py`) reproduce identically at
  `698aecbe` and are all on lines this item did not touch.
  Frontend: `tsc -b --force` exit 0, `eslint src/lib/api.ts` clean.
- [[FEAT-137]]'s `last_reading_at` is still what would tell a *quiet* strategy
  from a *dead* one over time. This item only stops absence scoring as zero,
  which is why the fifth criterion exists and has its own test.

---
id: FEAT-141
title: Fleet diagnostics view — liveness, idle and drift flags, host capacity, executor hygiene and throttling
summary: The bot log viewer issue 64 asked for has shipped (LogsSection, rendered from error_logs/general_logs), so this is the fleet view the race showed is still missing — last successful action, zero-fill and failure-rate flags, host load and tick duration, executor hygiene, and throttle refusals per agent.
status: done
effort: L
risk: low
brain: developer
new_files:
  - condor/agents/diagnostics.py
  - tests/test_fleet_diagnostics.py
  - frontend/src/components/bots/fleet-diagnostics.ts
  - frontend/src/components/bots/FleetDiagnostics.tsx
  - frontend/src/components/bots/FleetDiagnostics.test.tsx
  - frontend/src/components/perf/PerfBrowser.diagnostics.test.tsx
touched_files:
  - condor/agents/engine.py
  - condor/agents/fleet_map.py
  - condor/web/models.py
  - condor/web/routes/agents.py
  - condor/web/routes/bots.py
  - frontend/src/lib/api.ts
  - frontend/src/lib/agent-attribution.ts
  - frontend/src/hooks/useFleetData.ts
  - frontend/src/pages/Bots.tsx
  - frontend/src/components/agent/workspace/AgentFleet.tsx
  - frontend/src/components/perf/PerfBrowser.tsx
  - tests/test_bots_page_transform.py
  - tests/test_config_drift.py
  - frontend/src/lib/agent-attribution.test.ts
  - frontend/src/hooks/useLiveLoops.test.tsx
  - frontend/src/components/chat/AccountDock.test.tsx
  - frontend/src/components/chat/LoopsPanel.test.tsx
  - frontend/src/components/perf/AgentScopeHeader.deed.test.tsx
  - frontend/src/components/perf/AgentScopeHeader.bucketName.test.tsx
depends_on:
  - "[[FEAT-137]]"
  - "[[FEAT-138]]"
  - "[[CORR-726]]"
commits:
  - "73a7bc51 feat(bots): one view says which of my bots is in trouble, and why (FEAT-141)"
created: 2026-10-14
closed: 2026-10-15
---

## Objective

One fleet view answers "which of my bots is in trouble, and why" from signals
rather than from logs — so the symptom that cost one race entry 4h45m is visible
in seconds.

## Context and constraints

GitHub issue [#64](https://github.com/hummingbot/condor/issues/64) originally
asked for a bot log viewer and error diagnostics. **That part has shipped.** At
`45e60d60`, `frontend/src/components/bots/LogsSection.tsx` exists and
`PerfBrowser.tsx:1751-1752` renders `error_logs` and `general_logs` from the bot
payload (`condor/web/models.py:120-121`), and commit `394552c0` ("fix(web): draw
Stop bot and Logs on an agent fleet running a single bot") is in `HEAD`. So the
PR #65 the issue thread points at is in. This item is therefore **not** "add a
log viewer" — it is the list #64's race comment adds, which logs do not answer:

- **Per-strategy last successful action**, so quiet and dead can be told apart
  (#272) — [[FEAT-137]] produces it.
- **Idle and drifting flags**: hours with zero fills on a volume strategy, and
  failures per hour. The race's numbers are the calibration: "Volume strategies
  failing 0.01–0.4% of orders were fine. One DEX path at 93% failures went
  unnoticed for hours."
- **Host capacity**: `load/vCPU`, tick duration and order-placement lag.
  "Oversubscription (a 2-vCPU host above 1.0× load with high-frequency makers)
  showed up as slow ticks and timeouts, not crashes, and was misread as strategy
  bugs."
- **Executor hygiene**: terminated executor counts per host (35 / 173 / 0 / 2 /
  16 at the end of the race) and running executors whose owner session is gone
  (#271) — [[CORR-726]] produces the orphaned-running state, [[PERF-733]] is the
  terminated-row fetch.
- **Throttling**: rate-limit refusals per agent and venue (#276) —
  [[FEAT-138]] produces them.

So this is a presentation feature over signals four other items produce, which
is what the dependencies encode. The constraint is that it must add no new
polling cost to a dashboard that already polls per server.

## Alternatives considered

1. **Add each signal to the existing bot list as it lands.** Rejected: the
   diagnostic value is in seeing them together — the 4h45m case is "status
   running" plus "0 controllers" plus "no recent action", and no single column
   says it.
2. **Build the view now with the signals it can get today, and backfill.**
   Rejected: three of the five signals do not exist yet, so the view would ship
   mostly empty and its layout would be designed around the wrong set.
3. **A separate diagnostics page.** Rejected: the operator arrives at the bot
   list because a bot looks wrong, and a second place to go is the reason the
   race's symptoms went unnoticed for hours.
4. **A diagnostics section in the existing bot/fleet view, fed by the signals
   the dependency items produce.** Chosen.

## Decision

Alternative 4. One `FleetDiagnostics` section in the existing fleet/bot view,
rendering the five signal groups, each with an explicit unknown state.

## Design

- Five groups, in the order an operator triages: liveness (last reading, last
  successful action, running-with-0-controllers from [[CORR-725]]), activity
  (fills in the last hour, failure rate), host (load/vCPU, tick duration,
  order-placement lag), executors (terminated count, orphaned-running from
  [[CORR-726]]), throttling (refusals per venue from [[FEAT-138]]).
- Every signal renders three states — ok, flagged, unknown. Unknown is explicit
  and never styled as ok, which is the whole lesson of [[CORR-725]]; a
  diagnostics view that shows a missing signal as green reproduces the defect
  it exists to surface.
- The failure-rate flag's threshold is set from the race's measured bands
  (0.01–0.4% normal, 93% the missed case) rather than invented, and the band is
  stated in the UI so an operator can judge a borderline reading.
- Fed from the existing bot and agent payloads, extended with the new fields, so
  the view adds no poll of its own.

## Implementation plan

1. [x] Extend the bot/agent route payloads with the five signal groups.
2. [x] Add `FleetDiagnostics.tsx` with the three-state rendering.
3. [x] Place it in the existing fleet/bot view beside `LogsSection`.
4. [x] Frontend types and tests per the criteria below.

## Acceptance criteria

- [x] A bot reading `status: running` with 0 controllers and no successful action for an hour is flagged, and the view names all three facts
- [x] A signal that is unavailable renders as unknown, visibly distinct from ok
- [x] A volume strategy failing 0.3% of orders is not flagged; one failing 93% is
- [x] Host load per vCPU, tick duration and order-placement lag are shown, and a 2-vCPU host above 1.0× load is flagged
- [x] Terminated executor count per host and any orphaned-running executor are shown
- [x] Throttle refusals per agent and venue are shown
- [x] The section adds no additional polling beyond the existing bot/agent payloads
- [x] The existing log viewer still renders, unchanged
- [x] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Risks and notes

- Verified at `45e60d60`: `LogsSection.tsx` exists, `PerfBrowser.tsx:1751-1752`
  renders both log lists, and `394552c0` is an ancestor of `HEAD` — checked with
  `git merge-base --is-ancestor`. This is why the item is scoped to the fleet
  signals and **not** to the log viewer #64's title asks for.
- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60`
  with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0
  failed in 98.1s, EXIT=0**. Provenance, not a threshold.
- Three dependencies are declared and two more are cited: [[FEAT-137]]
  (liveness), [[FEAT-138]] (throttling) and [[CORR-726]] (orphaned-running) are
  `depends_on` because the view cannot render a signal that does not exist;
  [[CORR-725]] and [[PERF-733]] are cited but not depended on, since the view
  degrades to unknown without them rather than being wrong.
- `effort: L` is mostly the four payload extensions and the three-state
  rendering, not the layout.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.24
  `READ-402 [done]` (useWorkspaceAlerts returning the whole reading rather than
  alerts), below the 0.50 fallback bar — adjacent on the alerts surface.

## Notes — what shipped

**Where the signals ride.** The constraint "no new poll" decided the shape. The
host, lag and throttle groups are an additive `diagnostics` block on
`GET /agents/fleet-map`, the one agent payload `/bots` already polls (5s) while
making no Hummingbot call at all; executor hygiene is an additive
`executor_hygiene` on the bots payload, folded from the executor rows the
poller already cached with a **cache-only** read (`sds.get`, never
`get_or_fetch`). Nothing was given a route of its own, and
`PerfBrowser.diagnostics.test.tsx` measures it: opening the drawer issues no
request. `GET /agents/venue-conditions` is untouched and keeps its own meaning.

**Two signals nothing produced.** The dependencies supply liveness
([[FEAT-137]]), throttling ([[FEAT-138]]), orphaned-running ([[CORR-726]]) and
running-without-controllers ([[CORR-725]]). Host capacity and the terminal-row
count did not exist, so they are produced here:

- `condor/agents/diagnostics.py` reads Condor's own 1-minute load average
  against `os.cpu_count()` — the host the loops run on and every order is
  issued from, and the only one readable for free. A platform with no load
  average, or no CPU count, reports `available: false`; the bare load number is
  withheld, because 3.0 is idle on 32 vCPUs and on fire on 2.
- order-placement lag is the per-server round-trip the data service's own poll
  counters already measured (`get_stats()["health"]`), reported as the *floor*
  it is: an order cannot be placed faster than the API answers.
- tick duration needed one new field. `TickEngine` computed it per tick and
  threw it away, so `_last_tick_duration` now keeps the newest one and it
  travels through `get_info`, `fleet_map.LiveLoop` and `LiveLoopModel`.

**Activity reads call failures, not fills.** The design named "fills in the last
hour, failure rate". The failure rate is folded from the throttle counters per
venue and per agent — `(refusals + errors) / attempts` — which is exactly the
race's measured quantity ("one DEX path at 93% failures"), and the criterion is
pinned on 0.3% against 93%. A fills-per-hour reading was **not** added: the only
source for it is the sampled performance history, and folding a one-hour window
out of it would have been a second interpretation of a series the chart already
draws, for a signal the liveness group answers better (`acted … ago` is an
outcome; a zero-fill hour on a quiet book is not). The group reads unknown
rather than ok when fewer than 20 calls have been accounted — a sample too small
to rate is not a clean bill.

**Three states, always all of them.** The Host group draws load, tick duration
and placement lag on every render, each `ok` / `flagged` / `unknown`; a group
that silently omitted the reading it could not take would leave the operator to
notice an absence, which is the one thing this view exists not to ask of them.

**Files beyond the declared set.** The file set grew for three reasons, all
recorded in `new_files`/`touched_files` above:

- `frontend/src/components/bots/fleet-diagnostics.ts` holds the thresholds, the
  folds and `buildSignalGroups`, apart from the `.tsx` that draws them. Forced
  by `react-refresh/only-export-components` (13 errors against the repo's
  two-sided lint gate) and right anyway — it is the same split
  `lib/agent-attribution` has from the tree it feeds, so every verdict is
  reachable from a test.
- `PerfBrowser.diagnostics.test.tsx` pins what only the host can get wrong: the
  drawer at fleet scope, the untouched log viewer beside it, and the no-request
  measurement.
- six frontend fixtures and two pytest files gained the new fields
  (`lastTickDuration`, `_last_tick_duration`, `executor_hygiene`), which is
  what a required field on a shared type costs.

**Verification, run in this worktree at `e7407618` + this change:**

- `uv run pytest` — **6823 passed, 2 skipped, 0 failed in 102.4s**. No failure
  to compare against the merge base. (The doc's 6427 provenance predates the
  twenty other items this plan landed; it was never a threshold.)
- `uv run black --check condor tests` and `uv run isort --check-only` — clean.
- `npx tsc --noEmit -p tsconfig.app.json` — clean.
- `npx vitest run src` — **2321 passed**, 223 files.
- `npm run lint:ci` — *No new lint errors*, EXIT=0.
- `npm run build` — built.

---
id: FEAT-141
title: Fleet diagnostics view — liveness, idle and drift flags, host capacity, executor hygiene and throttling
summary: The bot log viewer issue 64 asked for has shipped (LogsSection, rendered from error_logs/general_logs), so this is the fleet view the race showed is still missing — last successful action, zero-fill and failure-rate flags, host load and tick duration, executor hygiene, and throttle refusals per agent.
status: todo
effort: L
risk: low
brain: developer
new_files:
  - frontend/src/components/bots/FleetDiagnostics.tsx
  - frontend/src/components/bots/FleetDiagnostics.test.tsx
touched_files:
  - condor/web/routes/agents.py
  - condor/web/routes/bots.py
  - frontend/src/lib/api.ts
  - frontend/src/components/perf/PerfBrowser.tsx
depends_on:
  - "[[FEAT-137]]"
  - "[[FEAT-138]]"
  - "[[CORR-726]]"
commits: []
created: 2026-10-14
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

1. Extend the bot/agent route payloads with the five signal groups.
2. Add `FleetDiagnostics.tsx` with the three-state rendering.
3. Place it in the existing fleet/bot view beside `LogsSection`.
4. Frontend types and tests per the criteria below.

## Acceptance criteria

- [ ] A bot reading `status: running` with 0 controllers and no successful action for an hour is flagged, and the view names all three facts
- [ ] A signal that is unavailable renders as unknown, visibly distinct from ok
- [ ] A volume strategy failing 0.3% of orders is not flagged; one failing 93% is
- [ ] Host load per vCPU, tick duration and order-placement lag are shown, and a 2-vCPU host above 1.0× load is flagged
- [ ] Terminated executor count per host and any orphaned-running executor are shown
- [ ] Throttle refusals per agent and venue are shown
- [ ] The section adds no additional polling beyond the existing bot/agent payloads
- [ ] The existing log viewer still renders, unchanged
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

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

---
id: FEAT-134
title: Every stop emits a close-out report built from a venue read
summary: A graceful stop produces no inventory of what it closed and what it left, and the emergency shutdown's report covers only this session's agent_id. Make every stop emit one report — executors, orders and positions, each closed or left — read from the venue rather than from local rows.
status: todo
effort: M
risk: medium
brain: developer
new_files:
  - condor/agents/closeout.py
  - tests/test_closeout_report.py
touched_files:
  - condor/agents/engine.py
  - condor/agents/shutdown.py
  - condor/agents/journal.py
  - condor/web/routes/agents.py
depends_on:
  - "[[CORR-727]]"
commits: []
created: 2026-10-14
---

## Objective

After a stop, an operator can read one report that says what was closed and what
was left behind — checked against the venue, not against Condor's own rows — and
can trigger that close-out for an agent that is already stopped.

## Context and constraints

GitHub issue [#273](https://github.com/hummingbot/condor/issues/273): at race
close-out one archived agent listed two Gate spot `LIMIT_MAKER` sells as
non-terminal. Cancelling them returned *"not found in active orders"*, so the
platform could neither cancel them nor confirm their state. A manual check of
the venue subaccount found no open orders — **the local rows were stale** — and
proving that took an operator. Another entry's stop left a filled market order
and no residue, and that too had to be established by hand.

Confirmed at `45e60d60`:

- a graceful stop produces no inventory (`condor/agents/engine.py:352-389`,
  `_finish`).
- the emergency shutdown *does* report "left N position(s) OPEN"
  (`condor/agents/shutdown.py:197-262`, `:395-462`) — but only for this
  session's `agent_id`, so executors from previous sessions are skipped. That
  is the same per-session blindness [[CORR-726]] fixes for stopping.
- the "not found in active orders" error is upstream
  (hummingbot-api `services/accounts_service.py:1047-1051`), and it only checks
  the API's own connector `in_flight_orders` — so it cannot see orders placed by
  bot containers, or orders tracked before an API restart. **This is why the
  report must read the venue**: a local-row report would have reproduced exactly
  the wrong answer the operator had to disprove by hand.

Constraints: the report must not be able to fail a stop (a stop that cannot
complete because its report failed is worse than no report), and it must work
after the agent is stopped, when no session context exists.

## Alternatives considered

1. **Report from local DB rows.** Cheapest, and refuted by the incident: the
   local rows were stale and the venue was clean. A report that confirms the
   wrong answer costs more than no report.
2. **Report only on the emergency shutdown path**, leaving graceful stops
   silent. Rejected: the race's stale rows came from a *graceful* close-out, and
   #273 asks for graceful, shutdown and restart alike.
3. **Have the LLM produce the report in its winddown turn.** This is what the
   current design effectively does with `cancel_open_orders`, and [[CORR-727]]
   is the item saying why it does not hold — a report that depends on a model's
   turn is absent exactly when the session ended early.
4. **Venue-read report, emitted by every stop path, with a standalone
   re-run.** Chosen.

## Decision

Alternative 4. One close-out builder, called by the graceful stop, the emergency
shutdown and the restart path, reading the venue for orders and positions and
the API for executors; plus a "close everything for agent X" action that takes
an agent id rather than a live session, so it still works once the agent is
stopped.

## Design

- `condor/agents/closeout.py` holds the builder: given an agent id and the set
  of executor lineages that belong to it, produce a record of executors stopped
  or left, orders cancelled or left, and positions closed or left open, each
  with the source it was confirmed from (venue read, API record, or
  unconfirmable).
- Lineage, not `agent_id`, is the scope — the same `"{agent}.{strategy}_"`
  prefix rule [[CORR-726]] establishes — so a previous session's residue appears
  in the report instead of being skipped the way `shutdown.py` skips it today.
- Unconfirmable is a first-class outcome, carried as such. The race's whole cost
  was not knowing which of "no open orders" and "could not check" was true, so
  the report never collapses the two.
- Emission is best-effort and after the stop's own work - a failure is recorded
  in the report and never propagated into the stop.
- The record is written to the journal (reusing the journal's existing snapshot
  writer) and exposed on the agent route for the dashboard.
- The standalone action takes `agent_id` and runs the same builder with no
  engine, which is what makes it usable post-stop.

## Implementation plan

1. Add `closeout.py` with the record type and the builder, venue read included,
   plus the unconfirmable outcome.
2. Call it from `_finish` (`engine.py:352`), from the emergency shutdown
   (`shutdown.py:197`), and from the restart path, scoping by lineage.
3. Persist to the journal and expose on the agent route.
4. Add the "close everything for agent X" action on top of the same builder.
5. Tests per the criteria below, with a fake venue that disagrees with the local
   rows — the race's actual case.

## Acceptance criteria

- [ ] A graceful stop emits a close-out report naming executors, orders and positions, each as closed or left
- [ ] With local rows showing two non-terminal orders and the venue showing none, the report says the venue shows none and marks the local rows stale — it does not report two open orders
- [ ] An order that can be neither cancelled nor confirmed is reported as unconfirmable, distinct from "left open" and from "closed"
- [ ] A previous session's executor appears in the report, where `shutdown.py`'s `agent_id` scoping skips it today
- [ ] A report builder that raises does not fail or delay the stop, and the failure is recorded
- [ ] "Close everything for agent X" runs against a stopped agent with no live session
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Risks and notes

- Verified at `45e60d60` with `git show HEAD:<path>` for the engine and shutdown
  citations; the upstream `accounts_service.py` line is quoted from #273 and is
  **not** in this repo, so it is cited as the issue's evidence rather than as a
  verified local anchor.
- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60`
  with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0
  failed in 98.1s, EXIT=0**. Provenance, not a threshold.
- `depends_on: [[CORR-727]]` — the report says what was cancelled, which is only
  meaningful once something cancels.
- #273's upstream asks (venue fallback in hummingbot-api when an order is absent
  from `in_flight_orders`, and executor retention) are **not filed**, being
  outside this repo. The Condor-side retention half is [[PERF-733]].
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.23
  `SEC-631 [done]` (the emergency-shutdown cleanup running with no risk gate),
  below the 0.50 fallback bar — same file, different concern.

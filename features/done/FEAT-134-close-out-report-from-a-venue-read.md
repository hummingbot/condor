---
id: FEAT-134
title: Every stop emits a close-out report built from a venue read
summary: A graceful stop produces no inventory of what it closed and what it left, and the emergency shutdown's report covers only this session's agent_id. Make every stop emit one report — executors, orders and positions, each closed or left — read from the venue rather than from local rows.
status: done
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
commits:
  - "c925a37c feat(agents): report what every stop closed and what it left, read from the venue (FEAT-134)"
created: 2026-10-14
closed: 2026-10-15
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

1. [x] Add `closeout.py` with the record type and the builder, venue read included,
   plus the unconfirmable outcome.
2. [x] Call it from `_finish` (`engine.py:352`), scoping by lineage — one call
   site, because `_finish` is the one teardown the emergency shutdown and the
   restart's stop half both reach. See `## Notes`.
3. [x] Persist beside the journal and expose on the agent route.
4. [x] Add the "close everything for agent X" action on top of the same builder.
5. [x] Tests per the criteria below, with a fake venue that disagrees with the
   local rows — the race's actual case.

## Acceptance criteria

- [x] A graceful stop emits a close-out report naming executors, orders and positions, each as closed or left
- [x] With local rows showing two non-terminal orders and the venue showing none, the report says the venue shows none and marks the local rows stale — it does not report two open orders
- [x] An order that can be neither cancelled nor confirmed is reported as unconfirmable, distinct from "left open" and from "closed"
- [x] A previous session's executor appears in the report, where `shutdown.py`'s `agent_id` scoping skips it today
- [x] A report builder that raises does not fail or delay the stop, and the failure is recorded
- [x] "Close everything for agent X" runs against a stopped agent with no live session
- [x] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Notes

Shipped at `c925a37c`. Verified with `uv run pytest` = **6618 passed, 2 skipped,
0 failed in 98.9s, EXIT=0**, plus `uv run black --check` and `uv run isort
--check-only` clean on every touched path and `uv run ruff check` clean on both
new files. Criterion 7 is met by that zero: a run with no failures cannot
contain a test that passed at the base and fails now, so the baseline run can
only report tests that were *already* failing — nothing a comparison would add.
The counts are provenance, not a threshold ([[CORR-414]]); the doc's own
`45e60d60` baseline of 6427 has moved because the plan has landed items since.

Per-criterion evidence, all in `tests/test_closeout_report.py` (29 tests):

1. `test_a_graceful_stop_emits_a_report_of_executors_orders_and_positions` — the
   report names all three kinds and each as closed or left, and it lands in the
   session dir and the journal.
2. `test_two_stale_local_orders_are_reported_closed_not_open` — #273's exact
   shape: the records claim two non-terminal sells, the venue holds none, and
   the report says closed with both rows named STALE. It asserts
   `report.of_outcome(LEFT)` is empty, which is the "does not report two open
   orders" half.
3. `test_an_order_that_cannot_be_cancelled_or_confirmed_is_unconfirmable`,
   `test_a_refused_cancel_the_venue_answered_for_is_not_unconfirmable`,
   `test_a_failed_venue_position_read_is_unconfirmable_not_flat` — the outcome
   is distinct in both directions: a refused cancel with no read is
   unconfirmable, the same refusal with an answering read is closed or left.
4. `test_a_previous_sessions_executor_is_in_the_report` — session 1's executor
   appears while session 2 is the one stopping; a sibling strategy's and an
   experiment's are rejected.
5. `test_a_builder_that_raises_does_not_fail_the_stop_and_is_recorded`,
   `test_a_builder_that_hangs_is_capped_and_recorded`,
   `test_finish_still_completes_when_the_close_out_fails` — the raise is caught
   and written into the report, the hang is capped by `EMIT_TIMEOUT_SEC`, and
   `_finish`'s own `finally` still records STOPPED either way.
6. `test_close_everything_runs_against_a_stopped_agent`,
   `test_the_close_out_action_runs_on_a_stopped_strategy` — two slugs and a
   client are the whole input; no engine is registered, so `/stop` would 404.

### Declared files that needed no edit

- **`condor/agents/shutdown.py`** — the design named `shutdown.py:197` as a
  second call site. It is not one: `_run_shutdown` ends in `_finish`, which is
  "the ONE teardown every exit path goes through", so the single call in
  `_finish` already covers the graceful stop, the emergency winddown, an error
  and the stop half of a restart. The module's rules are reused *by import*
  (`_cancel_scope`, `_keep_position`, `_market_key`, `_is_resting`, `_order_id`,
  `CancelOutcome`, `cancel_resting_orders`) rather than restated, so a second
  copy of the scope rule cannot drift from the sweep's.
- **`condor/agents/journal.py`** — the design said "reusing the journal's
  existing snapshot writer". The record is written beside the journal as
  `closeout.md` + `closeout.json` through `condor.fsutil.atomic_write_text`,
  which is the one implementation of that dance in the codebase
  ([[ARCH-148]]), and announced in the journal through the existing
  `append_action`. Adding a writer to `JournalManager` would have been a third
  spelling of the same thing.

### Deviations from the design, and why

- **Positions are mapped off `condor.venue_drift` rather than compared again.**
  That module owns *tracked versus venue* and its own docstring warns against a
  second copy, so `unanswered` becomes `unconfirmable`, `ghost` becomes `closed`
  with the row named stale, and anything the venue still holds is `left`. The
  check is perps-only, so a spot hold is reported from the record with
  `source: record` and a detail saying the venue read does not cover spot — not
  collapsed into `unconfirmable`, since the record does make a statement.
- **A claim is a *stated* non-terminal status**, not a bare order id. #273's
  rows said "non-terminal", and inferring a claim from a record that says
  nothing would invent a stale row for every order an executor ever placed and
  then "find" them all closed. The keys whose own name says terminal, and the
  orders that opened a kept position, are out of scope for the same reason.
- **The executor read is narrowed server-side to the lineage's session tags**
  (`lineage_controller_ids`, read from the session dirs on disk) because
  `search_executors` filters `controller_ids` by exact match and a lineage is a
  prefix. That is what makes reading the TERMINATED rows affordable — and
  reading them is what lets the report say *closed* and not only *left*. The
  prefix filter is still applied locally, so a server that ignores the argument
  cannot widen the report silently.
- **A crashed process still emits nothing at stop time**, having never reached
  `_finish`. `close_out_agent` is the answer for it — that is the criterion-6
  case — but boot reconciliation does not call it: a close-out is a mutating
  action, and flattening a position is not a boot chore (the same rule
  [[CORR-726]]'s orphan scan follows, reporting and never stopping). Worth
  filing separately if the operator wants it offered at boot.

### Risks and notes carried from the design


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

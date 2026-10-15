---
id: FEAT-135
title: Session and lifetime scopes are reported side by side, each tagged with the instances it covers
summary: Nothing labels which scope a P&L or volume figure has, so a session summary folding in earlier generations and a leaderboard summing only live ones are both wrong in different directions. Report session and lifetime figures side by side, each naming the instances it covers.
status: done
effort: M
risk: medium
brain: developer
new_files:
  - tests/test_scope_labelling.py
  - frontend/src/components/agent/session/SessionKpis.scopes.test.tsx
touched_files:
  - condor/agents/performance.py
  - condor/agents/attribution.py
  - condor/agents/session_report.py
  - condor/fetchers/bot_performance.py
  - frontend/src/lib/api.ts
  - condor/web/routes/agents.py
  - condor/agents/providers/executors.py
  - condor/agents/engine.py
  - frontend/src/components/agent/session/SessionKpis.tsx
  - tests/test_session_pnl_attribution.py
depends_on:
  - "[[CORR-724]]"
commits:
  - "8294cdce feat(agents): report session and lifetime scopes side by side, each tagged with its instances (FEAT-135)"
created: 2026-10-14
closed: 2026-10-15
---

## Objective

Every P&L and volume figure Condor reports says which scope it has — this
session's ownership window, or the whole lifetime of the bot name — and names
the bot instances it was summed over.

## Context and constraints

GitHub issue [#262](https://github.com/hummingbot/condor/issues/262) reports a
session summary that folds in earlier stopped deployments' volume and realized
P&L under a stable bot name ("$120 from the current bot plus $80 from previous
runs appears as $200"). Its race comment then shows the inverse need: "The race
leaderboard needed the opposite — full-race cumulative P&L and volume,
*including* stopped and archived generations. The scoring monitor originally
summed only live generations, so totals understated after every stop/archive.
Fixing that mid-race moved the public board."

That is the constraint that shapes the design: **both scopes are wanted, and the
defect is that nothing says which one a number is.** #262 states it directly —
"it isn't a matter of picking one total; the problem is that nothing labels
which scope a number has."

[#234](https://github.com/hummingbot/condor/issues/234)'s race comment adds the
boundary: unrealized P&L, open positions and exposure may come only from
instances confirmed by the active-bot inventory and are "never summed across
generations"; realized P&L and volume "may be cumulative across generations, but
only when labelled that way".

Already in place at `45e60d60`: the two scopes are *computed* — ownership
windows (`condor/agents/attribution.py`, `ownership_windows`/`window_span`),
`slice_history` for the windowed figures, and `fetch_base_histories` covering
archived names for the lifetime ones. [[CORR-633]] also added
`_merge_stopped_instance` so a windowless session's stopped deploys still
contribute realized P&L. So this is a labelling and surfacing feature, not new
arithmetic.

## Alternatives considered

1. **Pick session scope everywhere.** Breaks the leaderboard, which needs
   lifetime, and #262's comment records that correcting it mid-race moved the
   public board.
2. **Pick lifetime scope everywhere.** The original #262 defect.
3. **Keep one set of field names and switch scope by context.** Rejected: this
   is what produces the bug — the caller cannot tell which it received, so a
   figure means different things on two surfaces with no way to notice.
4. **Report both, under distinct names, each tagged with its instances.**
   Chosen, and it is what #262 asks for by name.

## Decision

Alternative 4. `session_volume` / `session_realized_pnl` scoped by the ownership
window, beside `lifetime_volume` / `lifetime_realized_pnl` across every
generation of the bot name, each carrying the instance names it covers.
Unrealized P&L, open positions and exposure stay single-scope and live-only, per
#234 — there is deliberately no `lifetime_unrealized`.

## Design

- The performance payload carries both pairs plus, for each, the list of
  instances summed. The instance list is the audit trail that makes a
  disagreement between two surfaces diagnosable instead of mysterious — which
  is the whole lesson of #234's "tag each figure with the instances it came
  from".
- Unrealized, open positions and exposure keep exactly one scope (live,
  confirmed by the registry) and are **not** given a lifetime twin. A doc that
  offered one would invite the cross-generation unrealized sum #234 forbids.
- Where no ownership window exists, the session figures report that explicitly
  rather than silently falling back to lifetime — the fallback is the original
  #262 defect. [[CORR-724]] is what makes a window exist in the first place, so
  this item's job is to stop the fallback being silent when it does not.
- The session report and the dashboard render the scope label beside the figure,
  so the two can never be read as one number.

## Implementation plan

1. Extend the performance payload with both scoped pairs and their instance
   lists, deriving them from the windows and histories already computed.
2. Make the no-window case explicit instead of a lifetime fallback.
3. Update the session report and the frontend types/renderers to show the label.
4. Tests per the criteria below.

## Acceptance criteria

- [x] A stable bot name with one live generation and two stopped ones reports session figures covering only the owned window and lifetime figures covering all three, each listing its instances — `test_three_generations_report_both_scopes_with_their_instances`: session 50/500 over `[GEN2, GEN3]`, lifetime 60/600 over all three
- [x] The synthetic #262 case ($120 current, $80 prior) reports session 120 and lifetime 200, and no field reports 200 as the session's — `test_the_262_case_reports_120_as_the_session_and_200_as_the_lifetime`
- [x] No lifetime twin exists for unrealized P&L, open positions or exposure, and a test asserts the payload has no such field — `test_no_lifetime_twin_exists_for_unrealized_positions_or_exposure` asserts it on the dataclass, on `AgentPerformanceModel.model_fields`, and on a serialized payload whose only `lifetime_*` keys are realized PnL, volume and instances
- [x] A session with no ownership window reports its session figures as unavailable rather than as the lifetime totals — `test_a_session_with_no_window_reports_its_scope_unavailable`
- [x] The session report and the dashboard show the scope label next to each figure — `test_the_session_report_labels_each_figure_with_its_scope` / `..._says_unavailable_not_the_lifetime_total` and `SessionKpis.scopes.test.tsx` (3 tests)
- [x] No test that passed at the merge base fails — `uv run pytest` on the change: **6561 passed, 2 skipped, 0 failed in 100.4s, EXIT=0**, with `frontend/dist` built. Nothing failed, so no failure needed exonerating against the base

## Risks and notes

- Verified at `45e60d60` with `git show HEAD:<path>` for the window and history
  helpers this builds on.
- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60`
  with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0
  failed in 98.1s, EXIT=0**. Provenance, not a threshold.
- `depends_on: [[CORR-724]]` — labelling a session scope is only useful once the
  ownership window is reliably recorded; without it every session reports
  "unavailable" and the feature has nothing to show.
- The third criterion is the one to keep. The symmetric-looking design is to
  give every figure both scopes, and #234 is explicit that unrealized across
  generations is never valid.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.27
  `CORR-634 [done]` (session slicing from the earliest takeover), below the
  0.50 fallback bar and the closest ancestor on the windowing side.

## Notes (shipped 2026-10-15)

Implemented as designed — alternative 4, both scopes under distinct names, each
tagged with its instances. Four deviations and refinements, all recorded here:

1. **The lifetime figure is summed from each instance's history first, and from
   the live snapshot only where there is no history.** The design said "this is a
   labelling and surfacing feature, not new arithmetic", and summing the snapshot
   aggregates would have been the minimal reading. It is wrong in the most common
   real case: the snapshot forgets an archived instance entirely — the normal end
   state of a stopped bot, and where a session's whole realized PnL can live
   ([[CORR-633]]) — so a leaderboard built on it would have understated after
   every stop/archive, exactly the defect #262's race comment reports. Taking the
   history first also means both scopes are cut from the *same* source, which is
   what structurally keeps the session figure from exceeding the lifetime one
   (`test_the_session_scope_never_exceeds_the_lifetime_one`,
   `test_the_lifetime_scope_counts_a_generation_the_snapshot_has_forgotten`).

2. **`fetch_base_histories` was split.** The new
   `fetch_instance_histories` returns `{base: {instance: history}}` and
   `fetch_base_histories` is now the key-dropping wrapper over it, so its three
   existing callers are untouched. A scope has to name the instances it covers,
   and re-deriving the name positionally from `partition_instances` is wrong the
   moment `MAX_HISTORY_INSTANCES` (24) caps the fan-out.

3. **Five files beyond the declared `touched_files`,** all on the path a field has
   to travel to reach the two renderers criterion 5 names:
   `condor/web/routes/agents.py` (the wire model the frontend type receives —
   `from_perf` maps by field name, so this is also what keeps the two in step),
   `condor/agents/providers/executors.py` and `condor/agents/engine.py` (the
   tick's data dict and `get_info`, which is what the session report reads),
   `frontend/src/components/agent/session/SessionKpis.tsx` (the dashboard), and
   `tests/test_session_pnl_attribution.py`, whose `_every_field_perf` guard
   requires every new dataclass field to differ from its default. A second new
   test file, `SessionKpis.scopes.test.tsx`, covers the dashboard half of
   criterion 5, which a Python test cannot reach.

4. **The windowless base's lifetime fallback was kept, not removed.** Reporting
   zero for a live bot would be worse than over-crediting an adopted one. What
   changed is that it is no longer silent: the session-scoped fields report
   `None`, `session_instances` omits that base's instances, both renderers say
   "unavailable — no ownership window recorded", and a warning names the bases
   whose lifetime figures are folded into the mixed-scope aggregate. The warning
   is scoped to bases that actually resolved to an instance, since one that
   resolved to nothing is already reported through `unresolved_bases`.

The KPI strips were left showing the existing folded aggregate rather than being
replaced by the scoped pair: that aggregate also carries the agent's *own*
executor rows, which are inherently session-scoped and would have been dropped
from the headline figure for a mixed session. The scoped pair is rendered beside
it instead, and `Unrealized` is labelled live-only on both surfaces.

Gate on the change, measured in this checkout at `8294cdce`: `uv run pytest` =
**6561 passed, 2 skipped, 0 failed, EXIT=0**; `black --check` and
`isort --check-only` over `condor/` and `tests/` clean; `ruff` clean on every
file touched except `condor/agents/engine.py` and `condor/web/routes/agents.py`,
which carry the same 10 pre-existing findings at `fae40bdd` (verified by running
ruff on both files' base blobs); frontend `tsc --noEmit`, `npm run build`,
`npx vitest run` (219 files, 2266 tests) and `npm run lint:ci` (EXIT=0, 41
baselined) all green.

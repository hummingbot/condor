---
id: FEAT-135
title: Session and lifetime scopes are reported side by side, each tagged with the instances it covers
summary: Nothing labels which scope a P&L or volume figure has, so a session summary folding in earlier generations and a leaderboard summing only live ones are both wrong in different directions. Report session and lifetime figures side by side, each naming the instances it covers.
status: todo
effort: M
risk: medium
brain: developer
new_files:
  - tests/test_scope_labelling.py
touched_files:
  - condor/agents/performance.py
  - condor/agents/attribution.py
  - condor/agents/session_report.py
  - condor/fetchers/bot_performance.py
  - frontend/src/lib/api.ts
depends_on:
  - "[[CORR-724]]"
commits: []
created: 2026-10-14
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

- [ ] A stable bot name with one live generation and two stopped ones reports session figures covering only the owned window and lifetime figures covering all three, each listing its instances
- [ ] The synthetic #262 case ($120 current, $80 prior) reports session 120 and lifetime 200, and no field reports 200 as the session's
- [ ] No lifetime twin exists for unrealized P&L, open positions or exposure, and a test asserts the payload has no such field
- [ ] A session with no ownership window reports its session figures as unavailable rather than as the lifetime totals
- [ ] The session report and the dashboard show the scope label next to each figure
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

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

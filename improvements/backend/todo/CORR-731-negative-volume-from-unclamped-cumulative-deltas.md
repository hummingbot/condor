---
id: CORR-731
title: Negative volume reported from unclamped cumulative deltas and signed quantities
summary: slice_history and slice_history_series compute each instance's window as cum(end) minus cum(start) with no clamp, so a reset or drop in a bot's cumulative volume_traded yields a negative delta, and get_executor_volume applies no abs(). The journal then prints a negative traded volume.
category: correctness
impact: medium
effort: S
risk: low
status: todo
brain: developer
files:
  - condor/fetchers/bot_performance.py:1046
  - condor/fetchers/bot_performance.py:1069
  - condor/fetchers/executors.py:119
  - condor/agents/journal.py:936
  - condor/agents/session_report.py:112
depends_on: []
commits: []
created: 2026-10-14
---

## Problem

GitHub issue [#280](https://github.com/hummingbot/condor/issues/280): an agent's
journal printed a **negative volume** column during the race. Traded volume
cannot be negative, so the value was a signed quantity passed through as volume
— and it "undermined trust in every other volume figure the agent reported, at a
point where volume was a scored criterion".

Confirmed at `45e60d60`:

- `slice_history` (`condor/fetchers/bot_performance.py:1046-1066`) sums
  `volume += v_e - v_s` across instances with no clamp, and
  `slice_history_series` (`:1069-1108`) does the same per point. The docstring
  states the assumption the arithmetic rests on — "Because session windows tile
  the timeline, summing every session's slice reproduces each instance's full
  cumulative with no double counting" — which holds only while the cumulative is
  monotonic. A restart, or a new instance under the same name, drops it.
- `get_executor_volume` (`condor/fetchers/executors.py:119-125`) returns
  `float(val)` of the first non-zero of `filled_amount_quote`,
  `volume_traded`, `total_volume`, with no `abs()` and no check that the field
  is a traded-volume field rather than a signed position quantity.
- the values are then summed raw (`condor/agents/performance.py:358`,
  `condor/agents/attribution.py:531`, `:575`, `:598`) and rendered without a
  sign check — the journal snapshot `volume=${total_volume:,.0f}`
  (`condor/agents/journal.py:936`, fed from `condor/agents/engine.py:977-982`),
  `condor/agents/providers/executors.py:146`, `:168`, and
  `condor/agents/session_report.py:112`, `:180`, `:281`.

One upstream source of a signed value is named in the issue and is worth keeping
in the doc: Hummingbot's grid executor computes
`position_size_base = executed - close_executed`, which can go negative.

## Proposed solution

1. Treat a drop in a cumulative counter as a **reset**, not a negative delta.
   In `slice_history` and `slice_history_series`, when `v_end < v_start` for an
   instance, start a new segment from the reset point rather than subtracting —
   `max(0, v_end - v_start)` alone clamps the sign but silently loses the volume
   traded after the reset, so the segment treatment is what keeps the figure
   right as well as non-negative.
2. Apply `abs()` in `get_executor_volume`, and restrict the fields it reads to
   true traded-volume fields so a signed position quantity cannot be picked up
   as volume in the first place.
3. At `journal.py:936` and in the session report, flag a negative input as a
   data error rather than formatting it. A clamp that hides the input also hides
   the next instance of this bug.
4. Add the unit test #280 asks for - a reset counter and a signed position
   field.

## Acceptance criteria

- [ ] An instance history whose cumulative `volume_traded` drops mid-window yields a non-negative window volume, and that volume includes the trading done after the reset
- [ ] `slice_history_series` over the same history yields no negative point and no negative step
- [ ] `get_executor_volume` returns a non-negative value for an executor carrying a negative signed field, and ignores a field that is not traded volume
- [ ] A negative input reaching the journal snapshot or session report is reported as a data error, not formatted as a volume
- [ ] A monotonic history produces exactly the same figures as before the change
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Notes

- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60` with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0 failed in 98.1s, EXIT=0**. Quoted as provenance, not as a threshold — unrelated items add tests.
- Verified at `45e60d60` with `git show HEAD:<path>`; `slice_history` was read
  in full and its monotonicity assumption is quoted above.
- The reset-as-segment treatment in step 1 is the part worth insisting on. #280
  offers `max(0, v_end - v_start)` first and the segment treatment second; the
  clamp alone turns a visible wrong number into an invisible one, which for a
  scored figure is a worse outcome.
- The fifth criterion exists because this touches the arithmetic every volume
  and P&L surface shares; a change that moves a monotonic history's numbers at
  all is wrong.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.25
  `CORR-634 [done]` (session slicing from the earliest takeover), below the
  0.50 fallback bar — adjacent arithmetic, different defect.

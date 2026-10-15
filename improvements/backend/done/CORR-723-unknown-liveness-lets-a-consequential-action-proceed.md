---
id: CORR-723
title: Unknown bot liveness falls back to unfiltered performance, so adoption opens an ownership window on a bot nobody confirmed
summary: fetch_live_instance_names returns None on any failure and all four call sites keep their unfiltered behaviour. For a read that is a labelling problem, but adoption is not read-only — it opens an ownership window and licenses stop/resume on a bot nobody confirmed. Fail closed on the action, label the read.
category: correctness
impact: high
effort: M
risk: medium
status: done
brain: developer
files:
  - condor/fetchers/bot_performance.py:533
  - condor/fetchers/bot_performance.py:395
  - condor/agents/engine.py:1117
  - condor/agents/performance.py:485
  - condor/agents/attribution.py:367
depends_on: []
commits:
  - "1db75e66 fix(bots): fail closed on unknown liveness in adoption, label it on reads (CORR-723)"
created: 2026-10-14
closed: 2026-10-15
---

## Problem

[[CORR-633]] and [[CORR-699]] fixed the core of GitHub issues
[#207](https://github.com/hummingbot/condor/issues/207) and
[#234](https://github.com/hummingbot/condor/issues/234): `resolve_bots` and
`_adopt_running_bots` now filter the controller-performance snapshot through
`fetch_live_instance_names`, so a stopped bot's frozen final row no longer counts
as the live open book. Both issues are therefore **largely fixed in `45e60d60`**,
and this item is only the residual both of them also ask for.

`fetch_live_instance_names` (`bot_performance.py:533-558`) returns `None` —
never an empty set — when the call raises, the client lacks the listing, or the
response is not a listing (`_is_bots_listing`). Every one of the four call sites
then keeps its unfiltered behaviour:

- `engine.py:1117` → `if live is not None and instance_name not in live: continue`
- `bot_performance.py:395` → `live_names is None or i in live_names`
- `performance.py:485`, `attribution.py:367` → the same, through `resolve_bots`

This was deliberate. [[CORR-633]]'s design says the helper "must be best-effort
and return `None` (unknown) — never an empty set … so a backend/test double
without the listing keeps today's behaviour rather than silently zeroing every
bot's open book", and its acceptance criteria pin it: "a fake client whose
`get_active_bots_status` raises … leaves the unrealized=7 attribution exactly as
today (fallback is 'unknown', never 'nothing is live')". That rationale is sound
and this item does not reverse it.

What the race showed is that one policy is being applied to two different kinds
of consumer. For a *read*, treating unknown as unfiltered risks showing a figure
that is too high — bad, but a labelling problem. For `_adopt_running_bots` it is
not a read: `ledger.adopt()` opens an ownership window starting now, switches the
session to bot mode, and makes subsequent stop/resume/redeploy target a real bot
that places real orders. #207's own thread states the distinction — "adopt is not
read-only … any consequential lifecycle action should require a fresh
authoritative-state confirmation … and if that confirmation isn't available, fail
closed" — and #207's expected behaviour asks for exactly that: "Treat an
unavailable active-status request as unknown and retry adoption; do not interpret
request failure as an empty namespace."

A second, quieter path to the same fallback: `_is_bots_listing` returning false
on an upstream shape change reverts every surface to unfiltered with no error at
all, and [[CORR-633]]'s closure note records that an `{status: error}` payload is
already treated as unknown.

## Proposed solution

Split the policy by consumer, keeping [[CORR-633]]'s reasoning for reads:

1. Give `fetch_live_instance_names` a companion that distinguishes the three
   outcomes it already computes internally — confirmed set, unknown, empty — so a
   caller can branch on unknown rather than inferring it from `None`.
2. `_adopt_running_bots` (`engine.py:1117`): on unknown liveness, adopt nothing,
   leave `_adoption_done` false and retry on the next tick, logging why. This is
   the existing best-effort contract of that function ("a failure means 'own
   nothing yet' and is retried on the next tick, never fatal",
   `engine.py:1084-1086`) — the liveness branch is simply the one failure it does
   not currently route that way.
3. The read surfaces (`resolve_bots` and its two callers) keep today's unfiltered
   behaviour, but carry the unknown state through to the caller so the figure can
   be rendered as unverified rather than as confirmed-live. The flag this sets is
   the one [[FEAT-135]] labels and [[FEAT-141]] renders; this item only has to
   stop discarding it.

## Acceptance criteria

- [x] Fake client whose `get_active_bots_status` raises → `_adopt_running_bots` adopts nothing, `_adoption_done` stays false, and a second tick with a working listing adopts the live bot
- [x] Fake client whose listing returns `{"status": "error"}` → same deferral, not an adoption
- [x] A working listing still adopts only the instance it names ([[CORR-699]]'s criterion still holds)
- [x] `resolve_bots` with `live_names=None` still returns the unfiltered aggregate, so [[CORR-633]]'s "unrealized=7 unchanged" criterion still holds, and the result carries an explicit unknown-liveness marker
- [x] No test that passed at the merge base fails: `uv run pytest` run on the merge base and on the change in the same turn, compared

## Notes

- **Shipped in `1db75e66`.** `fetch_live_instance_names_checked` returns
  `(names, known)`; the flat helper delegates to it and keeps its `None`.
  `_adopt_running_bots` defers the whole pass on unknown and retries next tick;
  an empty *answer* still settles it (`_adoption_done` true), which is the
  distinction the split exists for.
- Deviation from `## Proposed solution` 3: the two read call sites
  (`performance.py:485`, `attribution.py:367`) keep calling the flat
  `fetch_live_instance_names`, so neither diff shows at those lines. The unknown
  state is carried on the resolved aggregate instead — `resolve_bots` stamps
  `liveness_unknown: True` on an unfiltered aggregate (a copy, so the cached
  snapshot dict a single-instance merge hands back is never marked), and one
  shared rule `attribution.apply_liveness` reads it onto
  `AgentPerformance.liveness_known` from both surfaces. Same shape as
  `fees_known`, and the rollup and the agent's own view cannot disagree about
  whether what they rendered was confirmed live. Both declared paths are edited
  (the dataclass field + `_merge_bot_perf`, and the helper + the operator fold).
- One file beyond `files:`: `condor/web/routes/agents.py` gained
  `AgentPerformanceModel.liveness_known`, without which `from_perf`'s
  field-name mapping would drop the flag on the wire — i.e. keep discarding it.
- Verification: `uv run pytest` on the change = **6436 passed, 2 skipped, 0
  failed in 95.4s** (frontend/dist present, so only the 2 usual skips). Above the
  doc's 6427 provenance because other items on this branch and this one add
  tests; nothing newly fails, so there is no count mismatch to report
  ([[CORR-414]]). `black --check` / `isort --check-only` clean on all eight files;
  the 10 `ruff` findings over them (`F821` on `TYPE_CHECKING` names in
  `engine.py`, unused imports in `agents.py`/`test_bot_ownership.py`) are all
  pre-existing and on lines this item did not touch.
- Four tests in `test_bot_ownership.py` that passed `object()` as the client were
  given a listing: they test the namespace, lineage and disowned rules, and
  without a live listing they would now assert a *deferral* rather than the rule
  they were written for. `test_adoption_keeps_the_whole_snapshot_when_liveness_is_unknown`
  pinned the policy this item reverses and was rewritten as
  `test_adoption_defers_when_liveness_is_unknown_and_retries`.
- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60` with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0 failed in 98.1s, EXIT=0**. Quoted as provenance, not as a threshold — unrelated items add tests.
- Verified at `45e60d60` with `git show HEAD:<path>` for all five citations.
- This is a **policy change to a deliberate decision**, not a missed case. The
  doc states [[CORR-633]]'s rationale on purpose so a reviewer can disagree with
  the split rather than re-derive why `None` meant unfiltered.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.34
  `CORR-633 [done]` and 0.19 `CORR-699 [done]` — both below the 0.50 fallback
  bar, both the right ancestors, and reading their bodies is what reframed this
  item from "they forgot to fail closed" to "two consumers, one policy".
- The retry half of #207 and #262 is [[CORR-724]]; they touch the same function
  and are ordered by `depends_on` there.

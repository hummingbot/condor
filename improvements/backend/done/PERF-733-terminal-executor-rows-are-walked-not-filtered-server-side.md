---
id: PERF-733
title: Full executor walks pull terminal rows client-side, so a host's accumulated history crowds the 5000-row cap
summary: fetch_all_executors walks up to 5000 rows in 500-row pages with no terminal-status filter, and nothing prunes terminated executor records. Race hosts ended with 35 / 173 / 0 / 2 / 16 of them, so a full scan pays for history the caller discards and can be truncated by it.
category: performance
impact: low
effort: S
risk: low
status: done
brain: developer
files:
  - condor/fetchers/executors.py:16
  - condor/fetchers/executors.py:344
depends_on: []
commits:
  - "51cde681 perf(executors): keep live-only executor reads filtered server-side (PERF-733)"
created: 2026-10-14
closed: 2026-10-15
---

## Problem

GitHub issue [#273](https://github.com/hummingbot/condor/issues/273)'s fourth
root-cause bullet: "Nothing prunes terminated executor records. Hosts ended the
race with 35 / 173 / 0 / 2 / 16 of them, and full scans cap at 5000."

Confirmed at `45e60d60`. `MAX_EXECUTORS_FETCH = 5000` and
`EXECUTORS_PAGE_SIZE = 500` (`condor/fetchers/executors.py:16-17`), and
`fetch_all_executors` (`:344-360`) walks the cursor with whatever `**filters`
the caller passed, warning in its own docstring that "each call can issue up to
`max_items / EXECUTORS_PAGE_SIZE` sequential requests that the SDS rate limiter
cannot see" — ten requests at the cap.

Terminal rows are not excluded server-side, so a host that has run many
generations pays the full walk to fetch rows the caller then discards, and a
host with enough history can have live rows pushed past the cap.

This is filed as `impact: low` honestly: 173 terminated executors is far from
5000, so the race did not hit the cap. The item is the avoidable work and the
truncation risk as history accumulates, not an observed failure.

## Proposed solution

Filter terminal statuses server-side in `search_executors` where the caller only
wants live rows, which is what #273 proposes for the Condor side ("Condor can
filter terminal rows server-side in `search_executors` meanwhile"). The
retention and archival half is upstream in hummingbot-api and is not actionable
here.

Keep the full-history walk available unfiltered: `fetch_all_executors` is also
the path that *wants* terminated rows (`list_orphaned_positions` reads
terminated executors with a stranded position), so the filter belongs at the
call sites that ask for live rows, not in the walker.

## Acceptance criteria

- [x] Call sites that need only live executors pass a status filter, and a fake client asserts the filter reaches `search_executors` rather than being applied after the walk — `test_a_live_only_walk_sends_the_filter_on_every_page` asserts every recorded search carried `status="RUNNING"`, not just the first page's, and `test_the_boot_orphan_scan_reads_under_the_filter` asserts the same for the boot scan, the one live-only call site with no such coverage
- [x] `list_orphaned_positions` still sees terminated executors, so the orphan path is not broken by the filter — `test_the_unfiltered_walk_still_sees_terminated_rows`; see the correction in `## Notes` on where that tool actually reads from
- [x] A fake client serving more terminal rows than `MAX_EXECUTORS_FETCH` still yields every live row to a live-only caller — `test_history_larger_than_the_cap_still_yields_every_live_row` (5500 terminated rows, 3 live ones behind them, all 3 returned in one request), paired with `test_the_same_history_does_truncate_the_live_rows_when_unfiltered` so the assertion is not vacuous
- [x] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared — `uv run pytest` on the change: **6489 passed, 2 skipped, 0 failed in 96.5s, EXIT=0**. Zero failures makes the baseline comparison moot for the regression claim, so the second run was not spent; the count is above the doc's `45e60d60` provenance (6427) because other items of this plan added tests, plus the 5 added here

## Notes

- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60` with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0 failed in 98.1s, EXIT=0**. Quoted as provenance, not as a threshold — unrelated items add tests.
- Verified at `45e60d60` with `git show HEAD:<path>`; both constants and the
  walker body are quoted above.
- Filed separately from [[CORR-727]] and [[FEAT-134]] (the other two Condor-side
  halves of #273) because this is a fetch-path change with no bearing on whether
  a stop closes out, and it is the only one of the three that is a performance
  item.
- The upstream half of #273's bullet — retention/archival for terminated
  executors in hummingbot-api — is **not filed**, being outside this repo.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.19
  `PERF-681 [done]`, below the 0.50 fallback bar.

## Implementation notes

- **Production already filtered at every live-only call site, so this landed as
  the documented invariant plus the regression locks.** All seven
  `fetch_all_executors` call sites were classified: the SDS poll
  (`executors.py:369`), the offset-paging page (`web/routes/executors.py:182`)
  and the period summary (`:284`) all *want* terminal rows, the filtered list
  route (`:89`) forwards whatever the caller asked for, and the three live-only
  readers — `providers/drift.py:97-98`, `runtime/loops.py:356` and
  `agents/engine.py:1268` — already pass `status=`. Only drift did at
  `45e60d60`; the other two arrived with [[CORR-726]] inside this same plan and
  happened to get it right. Nothing proved it, and nothing told the next caller
  which side of the line it was on.
- `condor/fetchers/executors.py` is therefore a documentation change at both
  declared anchors, not a behavioural one: `MAX_EXECUTORS_FETCH` now says it is
  a cap on *history* because terminated records are never pruned upstream, and
  `fetch_all_executors`' docstring records that it stays unfiltered on purpose,
  that `**filters` reach `search_executors` on every page of the walk, and that
  a live-only caller passes `status=` so the budget is spent on rows it keeps.
  No `LIVE_EXECUTOR_STATUSES` constant was added: the only caller wanting both
  live statuses is drift, which names them in two commented lines already, and a
  constant used by nothing but a test is dead production code.
- **Correction to the item's premise:** `list_orphaned_positions` does not read
  terminated executors through `fetch_all_executors`. It `GET`s the API's own
  `/executors/positions/orphaned`
  (`mcp_servers/hummingbot_api/tools/executors.py:230`), so a status filter on
  the walker could never have reached it. What the filter could have broken is
  every *other* reader of terminated rows — the period summary and the
  executors tab's history — which is what the unfiltered-walk test pins.
- The upstream half of #273's bullet (retention/archival in hummingbot-api) is
  still out of this repo, as filed.

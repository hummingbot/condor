---
id: PERF-733
title: Full executor walks pull terminal rows client-side, so a host's accumulated history crowds the 5000-row cap
summary: fetch_all_executors walks up to 5000 rows in 500-row pages with no terminal-status filter, and nothing prunes terminated executor records. Race hosts ended with 35 / 173 / 0 / 2 / 16 of them, so a full scan pays for history the caller discards and can be truncated by it.
category: performance
impact: low
effort: S
risk: low
status: todo
brain: developer
files:
  - condor/fetchers/executors.py:16
  - condor/fetchers/executors.py:344
depends_on: []
commits: []
created: 2026-10-14
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

- [ ] Call sites that need only live executors pass a status filter, and a fake client asserts the filter reaches `search_executors` rather than being applied after the walk
- [ ] `list_orphaned_positions` still sees terminated executors, so the orphan path is not broken by the filter
- [ ] A fake client serving more terminal rows than `MAX_EXECUTORS_FETCH` still yields every live row to a live-only caller
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

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

---
id: PERF-736
title: Executor adoption put a full executor walk in the hot tick path, so a tick now makes two identical RUNNING walks and three in total
summary: >-
  CORR-726's adoption pass calls fetch_all_executors(status="RUNNING") every
  tick, and the drift provider already walks the same RUNNING set in the same
  tick — two identical walks, three in total. The function's own docstring says
  it is not for the hot poll. Share one walk per tick instead.
category: performance
impact: low
effort: S
risk: low
status: todo
brain: developer
files:
  - condor/agents/engine.py:627
  - condor/agents/engine.py:1268
  - condor/agents/providers/drift.py:97
  - condor/fetchers/executors.py:372
  - condor/fetchers/bot_performance.py:271
depends_on: []
commits: []
created: 2026-10-15
---

## Problem

Confirmed at `a65ef76a`, the commit that closed [[CORR-726]], and re-read at
`63694a4f` after [[CORR-731]] landed mid-investigation (it touched
`condor/fetchers/executors.py` and shifted its line numbers by 26; every reading
below holds at both shas, with the numbers given at `63694a4f`).

One loop tick now makes **three** `fetch_all_executors` calls, two of them
byte-identical:

| call site | filter |
| --- | --- |
| `condor/agents/engine.py:1268` (`_adopt_running_executors`, new in [[CORR-726]]) | `status="RUNNING"` |
| `condor/agents/providers/drift.py:97` | `status="RUNNING"` |
| `condor/agents/providers/drift.py:98` | `status="SHUTTING_DOWN"` |

The adoption pass runs at `condor/agents/engine.py:627`, immediately before
`run_core_providers` at `:630`, so the two RUNNING walks are microseconds apart
inside one tick and ask the venue the same question twice.

The function itself says this is the wrong place for it
(`condor/fetchers/executors.py:375-379`):

> Walks the cursor until exhausted or ``max_items`` is reached. **Not for the
> hot poll** — each call can issue up to ``max_items / EXECUTORS_PAGE_SIZE``
> sequential requests that the SDS rate limiter cannot see.

`MAX_EXECUTORS_FETCH = 5000` and `EXECUTORS_PAGE_SIZE = 500`
(`condor/fetchers/executors.py:16-17`, unchanged by [[CORR-731]]), so the bound is ten sequential requests
per call. In practice a host with 35 or 173 RUNNING-or-terminated rows pays one
request ([[PERF-733]] measured those counts), so today's observed cost is **one
extra request per tick per loop session** — honest `impact: low`. The shipped
loops run at `frequency_sec` 60, 120, 300, 300, 3600 and 3600
(`agents/*/loops/*/loop.md`), so at the fastest shipped cadence that is ~60
avoidable requests an hour per session, against a limiter that cannot see them.

The every-tick cadence is correct and is not what this item questions:
[[CORR-726]] chose it deliberately because a sibling session can end at any
point in this one's life and a graceful stop leaves its executors behind by
design (`condor/agents/engine.py:1250-1254`). The redundancy is that the tick
asks twice, not that it asks often.

## Proposed solution

Add the TTL-plus-coalescing cache idiom this repo already uses for exactly this
shape, in `condor/fetchers/executors.py`, in front of `fetch_all_executors`
(`:372-386`):
`_SNAPSHOT_TTL = 5.0` / `_snapshot_cache` (`condor/fetchers/bot_performance.py:271-272`)
and `_HISTORY_TTL = 20.0` / `_history_cache` (`:878-885`), both introduced by
`PERF-184` to collapse back-to-back fetches of the same thing.

Follow that idiom exactly:

- Key on `str(getattr(client, "base_url", "") or "")` **plus the filter set and
  `max_items`** — never `id(client)`, whose reuse after GC would hand one
  server's book to another (`condor/fetchers/bot_performance.py:281-286` states
  the rule, `_server_key` at `:278-286`). The filters must be in the key: `fetch_executors`
  (`:359-369`) walks with *no* status filter at `EXECUTORS_POLL_MAX`, and the
  dashboard's 2 s poll must not be served the RUNNING-only list or vice versa.
- A client with no `base_url` (test doubles) **bypasses the cache entirely**,
  as both existing caches do (`:953`). That is what keeps
  `tests/test_executor_adoption.py`'s `_Client` and
  `tests/test_fetcher_executors.py`'s call-counting fakes behaving exactly as
  they do today.
- Coalesce in-flight calls, so the drift provider's `asyncio.gather`
  (`condor/agents/providers/drift.py:94-100`) awaits the walk adoption started
  rather than racing a second one.
- Export a `clear_executor_cache()` beside `clear_snapshot_cache`
  (`condor/fetchers/bot_performance.py:289`) and call it from the same test
  fixture that clears the others.
- Keep the TTL short — around 2 s, well under the minimum shipped
  `frequency_sec` of 60 — so no tick is ever served the previous tick's book.
  The two consumers are asking the same question at the same instant; sharing
  one answer is the correct semantics, not an approximation.

Safe to share the row objects as they stand: `orphaned_running_rows` copies
each row it keeps (`condor/agents/executor_adoption.py:100`,
`out.append(dict(row))`) and `build_executor_row` returns a fresh dict, while
`venue_drift.tracked_from_active` takes `Iterable[Mapping]` and only reads
(`condor/venue_drift.py:272-317`). No consumer mutates the walk's rows, so one
list can serve both — but say so in the cache's docstring, because
`_price_pending_rows` (`condor/agents/providers/executors.py:23-59`) *does*
mutate rows in place on a neighbouring path, and a future caller that mutates a
cached list would corrupt the other consumer's view.

### Alternatives rejected

- **Thread the rows from adoption into the provider registry** — the shape
  [[PERF-639]] used for the duplicated controller-performance walk (compute once
  where the data is in hand, hand it to the other consumer through the provider
  result). It does not fit here, in the wrong direction: adoption runs *before*
  the providers, so it would mean a new `running_rows` kwarg on
  `run_core_providers` and on all three `BaseProvider.execute` implementations,
  against the registry's own stated invariant that "the providers are
  independent (none reads another's result)"
  (`condor/agents/providers/__init__.py:93-95`). And adoption's fetch is
  conditional — it returns early for an experiment, for an unreadable
  supervisor and for a failed read (`condor/agents/engine.py:1256-1276`) — so
  drift would need to keep its own fetch as a fallback anyway, leaving two
  paths where the cache leaves one.
- **Make adoption less frequent** (first tick only, or every Nth). Rejected for
  the reason [[CORR-726]] wrote down: a sibling session can die at any point, and
  a latched pass is the defect [[CORR-724]] had just finished removing from bot
  adoption.
- **Drop drift's RUNNING walk and derive from adoption's rows.** Wrong set:
  adoption keeps only this lineage's unowned rows, while drift is unscoped on
  purpose — "the venue answers for the whole account, so the tracked side must
  too" (`condor/agents/providers/drift.py:77-80`).

### Relationship to [[PERF-733]]

Complementary and independent. [[PERF-733]] is about *what each walk pulls* —
terminal rows are not excluded server-side, so a full scan pays for history the
caller discards. This item is about *how many walks a tick makes*. All three
calls above already pass a live-status filter, so [[PERF-733]]'s change does not
reduce their number and this one does not change their filter. They touch the
same file and should not be shipped in the same commit without re-reading each
other's criteria.

## Acceptance criteria

- [ ] One loop tick, driven with a call-counting fake client that reports a
      `base_url`, issues exactly **one** `search_executors(status="RUNNING")`
      walk and one `status="SHUTTING_DOWN"` walk — not two and one. The
      `_Client.searches` list of `tests/test_executor_adoption.py:103-113` is the
      existing counter idiom; `tests/test_fetcher_executors.py` holds the
      page-walk fakes
- [ ] A second call with a *different* filter set (`status="SHUTTING_DOWN"`, or
      no status at all as `fetch_executors` makes it) is **not** served the
      cached RUNNING rows
- [ ] Two clients with different `base_url` values do not share an entry
- [ ] A client with no `base_url` bypasses the cache: a test double called twice
      performs two walks, so every existing fake-client test keeps its current
      call counts
- [ ] Concurrent callers coalesce: two `fetch_all_executors` awaits started
      together against one client perform one walk
- [ ] After the TTL elapses, the next call walks again — asserted with a
      monkeypatched clock, not by sleeping
- [ ] `clear_executor_cache()` exists, is called from the test fixture that
      clears the bot-performance caches, and an entry does not survive it
- [ ] `tests/test_executors_poll_bound.py`, `tests/test_fetcher_executors.py`,
      `tests/test_pagination_cursor_progress.py`,
      `tests/test_executors_period_summary.py` and
      `tests/test_executor_adoption.py` pass unchanged
- [ ] No test that passed at the merge base fails — `uv run pytest` run on the
      merge base and on the change in the same turn, compared. Provenance: at
      `a65ef76a`, in a detached worktree with `.env` copied in,
      `uv run pytest -q` = `6450 passed, 23 skipped, 7 warnings in 96.85s`,
      exit 0. [[CORR-731]] landed after that reading and added
      `tests/test_volume_never_negative.py`, so the count has already moved:
      re-measure, and never treat 6450 as the expected number.

## Notes

- Noticed while shipping [[CORR-726]] and left undone there, correctly: the
  adoption pass is what made the duplicate, and consolidating the reads is a
  separate change to a different module.
- The scope this was handed as described the drift provider as making "two such
  calls per tick over the same RUNNING set". Corrected here: one of drift's two
  is `status="SHUTTING_DOWN"`, so the duplicate is exactly two identical RUNNING
  walks and three `fetch_all_executors` calls in total.
- `condor/runtime/loops.py:356` makes the same RUNNING walk once per settled
  strategy at boot. A cache keyed on `base_url` would also collapse those for
  strategies sharing a server, which is harmless — the rows are the same and
  each strategy's `live` set is applied afterwards (`:366-370`) — but it is a
  behaviour change at boot and belongs in the criteria of whoever ships this.
- [[CORR-735]] is the other item handed over from the same ship run; it reads the
  adopted rows the engine already holds and makes no new fetch, so the two do
  not interact beyond both touching `condor/agents/engine.py`.

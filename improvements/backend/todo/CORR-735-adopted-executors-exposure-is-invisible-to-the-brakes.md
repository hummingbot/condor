---
id: CORR-735
title: An adopted orphan executor's exposure is invisible to the risk brakes, so a session grants a full fresh allowance on top of what it inherited
summary: >-
  The tick builds risk_state.executor_count and total_exposure from the executors
  provider alone, which fetches under this session's own controller tag. An
  executor adopted from a dead predecessor is counted as zero open and zero
  exposure, so both brakes approve a full fresh allowance on top of it.
category: correctness
impact: high
effort: S
risk: low
status: todo
brain: developer
files:
  - condor/agents/engine.py:663
  - condor/agents/engine.py:1238
  - condor/agents/providers/executors.py:23
  - condor/agents/providers/executors.py:188
  - condor/agents/risk.py:427
depends_on: []
commits: []
created: 2026-10-15
---

## Problem

Confirmed at `a65ef76a`, the commit that closed [[CORR-726]], and re-read at
`63694a4f` after [[CORR-731]] landed mid-investigation (it touched
`condor/fetchers/executors.py`; every reading below holds at both shas, with the
line numbers given at `63694a4f`).

The two risk brakes a create passes through read exactly two fields of
`RiskState`:

- `condor/agents/risk.py:427` — `if current_state.executor_count >=
  self.limits.max_open_executors`
- `condor/agents/risk.py:452` — `if current_state.total_exposure + amount >
  self.limits.max_position_size_quote` (and `:627-628` for the DEX notional
  path)

Both are set in one place, `TickEngine._gather_tick_context`
(`condor/agents/engine.py:665-670`):

```python
live_executors = self._last_skill_data.get("executors", [])
live_open_count = len(live_executors) if isinstance(live_executors, list) else 0
risk_state.executor_count = live_open_count
risk_state.total_exposure = float(
    self._last_skill_data.get("total_exposure", 0.0) or 0.0
)
```

`_last_skill_data` is the executors provider's result, and that provider's
`total_exposure` is `sum(r.get("amount", 0) for r in running)`
(`condor/agents/providers/executors.py:188`) over
`running = [e for e in perf.executors if e["status"] == "RUNNING"]` (`:113`).
`perf` comes from `fetch_agent_performance(client, agent_id, bot_names=bases,
...)`, which reaches `search_executors(controller_ids=[aid])`
(`condor/agents/performance.py:454`) — **this session's own tag only**.

An executor's `controller_id` is the session tag
`"{agent_slug}.{strategy_slug}_{N}"`, minted afresh on every start
(`condor/agents/executor_adoption.py:1-22`). So a row opened by a dead
predecessor session carries a tag this session's fetch never matches, and it
can appear in neither `executor_count` nor `total_exposure`.

Before [[CORR-726]] that under-count was arguably consistent: the orphan was
invisible full stop, and the session could not act on it either. `CORR-726`
changed that — `_adopt_running_executors` (`condor/agents/engine.py:1238`) now
runs **every tick**, puts the orphan's row in `self._adopted_executors`
(`:1281-1282`), merges it into `_executor_owners` so the stop gate allows a
stop (`:1483`), and renders it into the prompt as `ADOPTED from a previous
session` (`:1290-1313`). The session owns it, is told about it, and can close
it — and still does not count it. `CORR-726`'s own closing notes record this as
deliberately deferred ("An adopted executor's size is not added to
`risk_state.total_exposure` / `executor_count` … a separate decision from being
able to stop the orphan at all"); this item is that decision.

### The failure

A session inherits one live position executor worth $5,000.
`max_open_executors: 3`, `max_position_size_quote: 6000`. The tick reads
`executor_count == 0` and `total_exposure == 0.0`, so `risk.py:427` and `:452`
approve creates up to **3 more executors and $6,000 more**, and real exposure
reaches $11,000 against a $6,000 cap. The brakes did not fail; they were handed
a book with the inherited row missing from it.

### A second value of the same hole

The adopted rows are built by `build_executor_row`
(`condor/agents/engine.py:1281`), which emits `amount` — the canonical quote
size (`condor/fetchers/executors.py:268`, `:294`). But they never pass through
`_price_pending_rows` (`condor/agents/providers/executors.py:23-59`), which the
provider runs over its own rows at `:181`. `_quote_size`'s own docstring says
what a zero means: *"That 0 means 'not priced here', not 'no exposure': the
agents' executors provider, which has a client, prices such a row at the live
price"* (`condor/fetchers/executors.py:187-188`). So an adopted row that is a
still-pending market order would be folded in at `$0` — the exact case the
engine's existing unpriced branch (`:674-680`, `book_trusted = False`) was
written for.

### Why this is not a regression of [[READ-658]]

[[READ-658]] deleted the journal's dead `## Executors` ledger and made
`_last_skill_data` the **single source** for these two fields; its fix holds
(`RiskEngine.get_state` reads only the drawdown,
`condor/agents/risk.py:281-304`; `get_total_exposure` /
`get_open_executor_count` have no hits anywhere in `condor/`). This is a new
gap in that single source, not a revival of the one it closed.

## Proposed solution

Fold the adopted rows into the two risk fields at the one place they are built,
reusing the pattern `_executor_owners` already uses one consumer over
(`condor/agents/engine.py:1467-1483`: *"Those rows are ours by inheritance and
by nothing the provider can see — it fetches under this session's own tag — so
they are added here rather than by widening the gate itself"*).

1. Price the adopted rows in `_adopt_running_executors`, with the provider's own
   helper rather than a second copy of it: promote `_price_pending_rows`
   (`condor/agents/providers/executors.py:23`) to a public name — or move it to
   `condor/fetchers/executors.py` beside `_quote_size` (`:163`), whose docstring already
   points at it — and call it on `built` after `:1281`. It mutates rows in place
   and returns the ids it still could not price; keep those on the engine (e.g.
   `self._adopted_unpriced`). Best-effort, like the rest of the pass: a failed
   price read leaves the row at `amount == 0` and feeds step 3, never raises.
2. At `condor/agents/engine.py:665-670`, add the adopted rows to **both**
   fields:

   ```python
   adopted = list(self._adopted_executors or [])
   risk_state.executor_count = live_open_count + len(adopted)
   risk_state.total_exposure = provider_exposure + sum(
       float(r.get("amount", 0) or 0) for r in adopted
   )
   ```

   There is no double-count to guard against: the provider's `running` and the
   adopted set are disjoint by construction — `orphaned_running_rows` keeps only
   rows whose lineage session number is **not** in the live set
   (`condor/agents/executor_adoption.py:96`), and the caller's own number is
   always in that set.
3. Union the still-unpriced adopted ids into the existing unpriced branch at
   `:674-680`, so an inherited row of unknown size refuses new exposure rather
   than reading as zero — the behaviour that branch already specifies, and the
   reason step 1 keeps the ids rather than discarding them.

### Deliberately not changed

- `live_open_count` as passed to `_build_prompt` (`:705`) and on to the canvas
  nudge's `open_count` (`:779`) stays the provider's own count. Whether an
  inherited executor should make the nudge treat the session as busy is a
  heuristics question, not a brake.
- `journal.record_snapshot(open_count=..., position_size=...)` (`:997-1006`)
  and `get_info()` stay provider-only. Those are attribution figures that sit
  beside PnL, and an inherited executor's lifetime PnL must not be reported as
  this session's earnings — which is why the whole adopted set is kept out of
  the provider's ownership-window-sliced totals
  (`condor/agents/attribution.py`) and folded in only where exposure is read.

### Alternatives rejected

- **Append the adopted rows to the provider's `executors` list.** One line, and
  wrong: `total_pnl`, `total_volume`, `realized_pnl` and `pnl_series` are all
  derived from the same set and are ownership-window-sliced on purpose
  ([[CORR-634]], [[ARCH-662]]), so an inherited executor's lifetime PnL would be
  reported back to the agent, the journal snapshot and the session report as
  this session's earnings.
- **Widen the provider's fetch to the lineage prefix.** It would re-import the
  same PnL problem and duplicate the dead-owner conjunction
  `orphaned_running_rows` exists to hold in one place.

## Acceptance criteria

- [ ] With one adopted row of `amount: 5000.0` in `engine._adopted_executors`
      and an empty provider result, the risk state the engine builds reports
      `executor_count == 1` and `total_exposure == 5000.0` — asserted through
      `_gather_tick_context` with the `_capture_prompt` harness of
      `tests/test_journal_read_cache.py:134-175`
- [ ] With the same adopted row and a provider result carrying one running row
      of `amount: 1000.0`, the state reports `executor_count == 2` and
      `total_exposure == 6000.0`
- [ ] A `RiskEngine(RiskLimits(max_open_executors=3,
      max_position_size_quote=6000))` gate refuses a $2,000 create against that
      state, and the refusal names the position limit — red against today's
      tree, where the same call is approved
- [ ] An adopted row the price read could not value (`amount == 0`) leaves
      `book_trusted == False` and its id in `risk_state.drift_reason`, so creates
      are refused rather than sized against a zero
- [ ] The adopted set and the provider's `running` set never overlap: a test
      asserting `executor_count` is unchanged when an adopted row's id also
      appears in the provider result (it cannot, by lineage, but the sum must
      not be the thing that depends on it) — or, if deduping by id is chosen
      instead of asserting disjointness, that dedupe is what is pinned
- [ ] `tests/test_executor_adoption.py` passes unchanged: adoption, the stop
      gate's answer for an adopted orphan, and the drop-once-the-owner-is-back
      behaviour are untouched
- [ ] A failed price read during adoption adopts the rows anyway and does not
      raise (the existing best-effort contract of
      `condor/agents/engine.py:1267-1276`)
- [ ] No test that passed at the merge base fails — `uv run pytest` run on the
      merge base and on the change in the same turn, compared. Provenance: at
      `a65ef76a`, in a detached worktree with `.env` copied in,
      `uv run pytest -q` = `6450 passed, 23 skipped, 7 warnings in 96.85s`,
      exit 0. [[CORR-731]] landed after that reading and added
      `tests/test_volume_never_negative.py`, so the count has already moved:
      re-measure, and never treat 6450 as the expected number.

## Notes

- Handed over from [[CORR-726]]'s ship run, which recorded it as out of scope in
  its own closing notes. [[CORR-726]]'s fix is verified holding at `a65ef76a`:
  `_adopted_executors` is populated every tick and merged into
  `_executor_owners` (`condor/agents/engine.py:1483`); none of this item's own
  cited files changed between `a65ef76a` and `63694a4f` except
  `condor/fetchers/executors.py`, re-pinned above.
- Lineage: [[READ-658]] made `_last_skill_data` the single source for these two
  fields and shipped the harness this item's criteria reuse; [[CORR-724]] and
  [[CORR-725]] are the two adoption/liveness items shipped just before
  [[CORR-726]] in the same cluster.
- Measured cadences, for how long the window stays open: the shipped loops run
  at `frequency_sec` 60, 120, 300, 300, 3600 and 3600, and adoption re-derives
  every tick — so the mis-sized book is not a boot-only state, it lasts as long
  as the orphan does.
- `condor/agents/risk.py:683` (`_stop_refusal`) is not touched, for the same
  reason [[CORR-726]] did not touch it: widening the stop gate would also allow
  stopping a live sibling's position.

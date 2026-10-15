---
id: CORR-726
title: A previous session's executors cannot be stopped, and a running executor whose owner is gone has no state
summary: An executor's controller_id carries the session number, the stop gate allows only executors in this tick's own snapshot, and a graceful stop leaves executors running. A new session cannot stop what its predecessor opened — one orphaned 1.0s LP poller ate a host's RPC budget for 13.5h.
category: correctness
impact: high
effort: M
risk: medium
status: done
brain: developer
files:
  - condor/agents/risk.py:683
  - condor/agents/engine.py:1080
  - condor/agents/engine.py:1304
  - condor/runtime/loops.py:194
  - condor/agents/providers/executors.py:111
  - agents/solana_dex_lp_expert/loops/lp_slot_operator/loop.md:50
depends_on:
  - "[[CORR-724]]"
commits:
  - "3a1f7a20 fix(agents): adopt a dead session's running executors, and report the orphans at boot (CORR-726)"
created: 2026-10-14
closed: 2026-10-15
---

## Problem

GitHub issue [#271](https://github.com/hummingbot/condor/issues/271) is the
single most expensive defect of the race: one Solana LP entry lost about **13.5
hours**. An LP executor created by a *previous* session of the same agent kept
polling every 1.0s and consumed the host's whole RPC budget, so every open the
live session attempted failed immediately. The live session could not stop it.

Every link confirmed at `45e60d60`:

- an executor's `controller_id` is the session id `"{agent}.{strategy}_{N}"`,
  and `N` increments on every start or restart
  (`condor/agents/engine.py:203-231`, `condor/runtime/loops.py:194-206`,
  `:415-449`). The tag is therefore per-session, not per-strategy.
- `_stop_refusal` (`condor/agents/risk.py:683-716`) allows a stop only when the
  executor is in this tick's `_executor_owners` snapshot, or when the API's
  `controller_id` for it equals this session's `agent_id`. Anything else is
  refused with *"belongs to controller … stopping it would close another
  session's position"* — the exact message the race produced.
- that snapshot holds only this session's own tag — the provider fetches with
  `controller_ids=[aid]` (`condor/agents/performance.py:442`). A predecessor's
  executors can never be in it.
- a graceful `engine.stop()` deliberately keeps positions and executors running
  (`engine.py:307-330`, `_finish` `:352-389`, `stop_all` in `loops.py:165-189`),
  as documented in `agents/_shared/skills/operate_your_loop/SKILL.md:72-85`. So
  the orphan is produced by the normal path, not by a crash.
- adoption exists only for bots (`BotLedger.adopt`,
  `condor/agents/ownership.py:295`; `_adopt_running_bots`, `engine.py:1080`).
  There is no executor equivalent.
- the existing orphan concept (`condor/agents/providers/executors.py:111-150`,
  `list_orphaned_positions`) covers *terminated* executors with a stranded
  position. A **running** executor whose owning session is gone has no state, no
  alert and no reclaim path.

And the agent instructions contradict the gate. `lp_slot_operator/loop.md:50`
tells a fresh session to call `list_executors(... status=["RUNNING"])` and
"**treat ALL returned RUNNING lp_executors as your open slots** (they share
`controller_id="main"`, so you can monitor and exit them)" — then
`stop_executor` on exactly those is what `_stop_refusal` refuses. The model is
being instructed to attempt the one action the gate blocks.

## Proposed solution

Follow the shape `_adopt_running_bots` already established, rather than widening
the gate:

1. Add executor adoption next to `_adopt_running_bots`. A new session of the
   same agent *and* strategy adopts running executors whose `controller_id`
   carries the `"{agent}.{strategy}_"` lineage **and** whose owning session is
   not live in the supervisor (`loops.for_strategy()` is the authority on which
   session numbers are alive). Feed the adopted ids into `_executor_owners` so
   `_stop_refusal`'s existing membership test allows the stop with no change to
   the gate itself.
2. In boot reconciliation (`loops.py:194`), scan for RUNNING executors whose
   owner session is dead and record them as an explicit **orphaned-running**
   state with an alert, so an orphan is visible before an agent trips over it.
   The state is what [[FEAT-141]] renders; this item produces it and puts it in
   the agent's own context.
3. Correct `lp_slot_operator/loop.md:50` to describe what the gate allows once
   adoption exists, so the instruction and the gate agree.

The lineage-plus-dead-owner conjunction is what keeps this conservative: a
sibling session that is still live keeps its executors, which is the property
`_stop_refusal` exists to protect.

## Acceptance criteria

- [x] Fake client: a RUNNING executor tagged `"{agent}.{strategy}_1"` with no live session 1 → adopted, in `_executor_owners`, and `_stop_refusal` returns `""` for it — `test_an_orphan_is_adopted_and_the_gate_then_allows_the_stop`
- [x] The same executor while session 1 *is* live in the supervisor → not adopted, and `_stop_refusal` still refuses with the cross-session message — `test_a_live_siblings_executor_is_not_adopted_and_stays_refused`
- [x] An executor of a different agent or a different strategy → never adopted, however it is tagged — `test_another_agent_or_strategy_is_never_adopted`, plus the parametrised `test_foreign_tags_have_no_session_number` (experiment tags and the backend's `"main"` default too)
- [x] Boot reconciliation with a RUNNING executor whose owner is dead reports it as orphaned-running, distinct from `list_orphaned_positions`' terminated-with-position case — `test_boot_reports_a_running_executor_whose_owner_is_gone` feeds the scan both shapes and gets only the running one back
- [x] `lp_slot_operator/loop.md` no longer instructs a stop the gate refuses — step 1 now reads the two `[CORE DATA]` blocks and says a `list_executors` id in neither is one the gate refuses
- [x] No test that passed at the merge base fails — `uv run pytest` on the change: **6471 passed, 2 skipped, 0 failed in 96.0s** (`frontend/dist` present, so the 21 web-route tests ran). Nothing fails, so no failure can be newly introduced; the merge-base run is therefore moot and was not needed. The plan's own final verification chunk runs the full gate again over everything it landed.

## Notes

- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60` with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0 failed in 98.1s, EXIT=0**. Quoted as provenance, not as a threshold — unrelated items add tests.
- Verified at `45e60d60` with `git show HEAD:<path>` for all six citations,
  including the `loop.md` contradiction quoted above.
- `depends_on: [[CORR-724]]` - that item reworks the adoption pass and its
  one-shot gate in `engine.py`, and executor adoption should be built beside the
  reworked version rather than against the one-shot one. File overlap on
  `engine.py` serialises them regardless.
- `condor/agents/risk.py` is also declared by the open [[SEC-715]]. That is a
  real file overlap for plan ordering, although the two touch different
  functions (risk limits vs the stop gate).
- This item deliberately does **not** change `_stop_refusal`'s logic. Widening
  the gate to allow any same-lineage executor would also allow stopping a live
  sibling session's position, which is the harm the gate was built for.
- #271's own proposal 3 is the `loop.md` fix, kept here rather than split off
  because the instruction is only correctable once the gate's answer is known.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.26
  `CORR-633 [done]`, below the 0.50 fallback bar; no open doc covers executor
  adoption.

## Implementation notes

- **`condor/agents/executor_adoption.py` (new)** holds the pure half: `session_lineage`,
  `lineage_session_num` (``None`` for another agent, another strategy, an
  experiment ``_e{N}``, the backend's ``"main"`` default, and a sibling slug
  that merely starts with ours) and `orphaned_running_rows`, which is the
  lineage-AND-dead-owner-AND-RUNNING conjunction. Both halves of the fix call
  it, so the engine and the boot pass can never disagree about what an orphan
  is. It is a new file rather than an addition to `ownership.py`, whose stated
  scope is bot ownership by *name*.
- **`condor/agents/engine.py`**: `_adopt_running_executors` runs each tick right
  after `_adopt_running_bots`, best-effort (a failed read owns nothing and
  retries next tick). `_live_session_nums` returns `None` — "do not adopt" —
  when this engine is not itself in the supervisor, because the registry is the
  only liveness authority and an unregistered engine means it cannot be
  trusted. The adopted rows are merged into `_executor_owners` *and* rendered
  into the prompt as `[CORE DATA - adopted_executors]`: the executors provider
  fetches under this session's own tag, so an adopted orphan appears nowhere
  else and an id the model never sees is one it can never stop. Re-derived
  every tick, never latched, so an id stops being adopted the moment its owner
  comes back (`test_an_adopted_orphan_is_dropped_once_its_owner_is_back`).
- **`condor/runtime/loops.py`**: `OrphanedExecutor` + `ReconcileReport
  .orphaned_executors`, filled by `_scan_orphaned_executors` after the restarts
  so a session this boot brought back counts as a live owner. One API read per
  settled strategy, best-effort; the client is resolved from the session's
  status file through an injectable factory. Orphans are **reported, never
  stopped** — winding a position down is an emergency action, not a boot chore.
- **`main.py`**: the orphans ride along in the existing per-chat restart
  summary, so the owner hears about live exposure nobody is operating at the
  moment they are reading about the restart.

### Declared files not edited

- `condor/agents/risk.py:683` (`_stop_refusal`) — untouched **on purpose**, as the
  doc itself requires. Adoption works by putting the orphans into the snapshot
  the gate already tests membership against, so the gate's logic, its message
  and its API fallback are byte-for-byte what they were.
- `condor/agents/providers/executors.py:111` (`list_orphaned_positions`' feed) —
  untouched. It was cited to establish that the *terminated*-with-stranded-position
  case is a different thing with a different recovery (close the position by
  hand), and keeping the two apart is the point of the new state, so widening
  that list would have undone it.

### Deliberately out of scope

- An adopted executor's size is not added to `risk_state.total_exposure` /
  `executor_count`, which are still built from the executors provider's own
  rows. That under-count is pre-existing (an orphan was never counted) and
  changing it would alter which creates the risk brakes refuse — a separate
  decision from being able to stop the orphan at all.

---
id: CORR-726
title: A previous session's executors cannot be stopped, and a running executor whose owner is gone has no state
summary: An executor's controller_id carries the session number, the stop gate allows only executors in this tick's own snapshot, and a graceful stop leaves executors running. A new session cannot stop what its predecessor opened — one orphaned 1.0s LP poller ate a host's RPC budget for 13.5h.
category: correctness
impact: high
effort: M
risk: medium
status: todo
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
commits: []
created: 2026-10-14
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

- [ ] Fake client: a RUNNING executor tagged `"{agent}.{strategy}_1"` with no live session 1 → adopted, in `_executor_owners`, and `_stop_refusal` returns `""` for it
- [ ] The same executor while session 1 *is* live in the supervisor → not adopted, and `_stop_refusal` still refuses with the cross-session message
- [ ] An executor of a different agent or a different strategy → never adopted, however it is tagged
- [ ] Boot reconciliation with a RUNNING executor whose owner is dead reports it as orphaned-running, distinct from `list_orphaned_positions`' terminated-with-position case
- [ ] `lp_slot_operator/loop.md` no longer instructs a stop the gate refuses
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

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

---
id: CORR-724
title: Adoption runs once and only over performance names, so a live bot with no reading is never owned
summary: _adopt_running_bots iterates the controller-performance snapshot, so a live bot with no performance rows is never adopted — and _adoption_done is set after the first successful pass, so no later tick recovers it. The session then reports lifetime totals for a bot it never recorded owning.
category: correctness
impact: high
effort: M
risk: medium
status: todo
brain: developer
files:
  - condor/agents/engine.py:1080
  - condor/agents/engine.py:1140
  - condor/agents/engine.py:1153
  - condor/agents/ownership.py:295
depends_on:
  - "[[CORR-723]]"
commits: []
created: 2026-10-14
---

## Problem

Two GitHub issues report the same defect from different ends.

[#207](https://github.com/hummingbot/condor/issues/207)'s race comment states it
directly — "`_adopt_running_bots` iterates only `fetch_all_bot_performance`
names, so a bot that *is* live but has no performance rows is never adopted. And
because `_adoption_done` is set after the first tick, it isn't retried."

[#262](https://github.com/hummingbot/condor/issues/262) is the consequence: a
deployment that is missed leaves no ownership window, so
`fetch_agent_performance_batch` falls back to lifetime totals and the session's
Executor State carries earlier stopped deployments' volume and realized PnL.
#262's step 3 is this item verbatim — "`_adopt_running_bots()` runs before
deployment on the first tick, then sets `_adoption_done`. Subsequent ticks skip
adoption, and the agent's own runtime confirmation does not repair the ledger."

Confirmed at `45e60d60`:

- the loop is `for instance_name in all_perf:` (`engine.py:1140`), where
  `all_perf` is `fetch_all_bot_performance`. A live instance absent from that
  payload cannot be reached by any of the three matching rules, however well its
  name matches the namespace.
- `self._adoption_done = True` (`:1153`) is set at the end of every pass that
  got as far as a performance payload — including a pass that adopted nothing.
  Only the two early returns (`:1100` already-done/no-ledger, `:1108` the
  performance fetch raising) leave it false, so a successful-but-empty pass
  permanently closes adoption for the session.
- nothing re-opens it. `ledger.adopt` is reached from `_adopt_running_bots` and
  from `note_deploy` on a recorded deploy; an agent that *confirms* on a later
  tick that its bot is running has no path that writes the ledger.

The three matching rules themselves are conservative by design
(`engine.py:1086-1092`) and this item does not widen them — the defect is the
*source set* they are matched against and the *one-shot* gate, not the rules.

## Proposed solution

1. Match the rules against the union of the performance snapshot and the live
   registry, not the snapshot alone. `fetch_live_instance_names` is already
   fetched in this function for [[CORR-723]]'s filter, so the live set is in hand
   and no extra call is needed — a name in the live set is, by definition, better
   evidence of liveness than a name in the snapshot.
2. Replace the one-shot `_adoption_done` boolean with a condition that lets a
   later tick try again when adoption could still change something — the ledger
   owns nothing, or a deploy has been recorded since the last pass. Keep the
   "runs at most once per tick" property so this stays off the hot path; the
   point is that "nothing to adopt yet" and "adoption is finished" stop being the
   same state.
3. Repair on confirmation - when a tick observes a live bot whose base this
   session's own action log records deploying, record ownership then rather than
   waiting for a pass that will never run again. `recorded_deploy_names` is
   already read here (`:1137`), so this is the same evidence applied on a later
   tick.

An empty `owned_bots.json` is deliberately **not** the fix: #262 says so in
terms ("Creating an empty ledger file would not establish ownership"), and the
ownership window, not the file, is what scopes the figures.

## Acceptance criteria

- [ ] Fake client: a bot present in the live registry and absent from the performance snapshot, whose base matches the namespace → adopted
- [ ] Fake client: first tick's registry and snapshot are both empty → `_adoption_done` does not close adoption, and a second tick with a live bot adopts it
- [ ] A recorded deploy confirmed live on tick 3 → ownership recorded, with an ownership window starting no earlier than the deploy
- [ ] An adopted-then-disowned bot is still not re-adopted (`read_disowned` keeps outranking all sources)
- [ ] With ownership recorded, the session's performance no longer includes a prior stopped generation's volume (the #262 symptom), asserted against API fixtures and `windows={}` as #262 describes
- [ ] No test that passed at the merge base fails: `uv run pytest` run on the merge base and on the change in the same turn, compared

## Notes

- Verified at `45e60d60` with `git show HEAD:<path>`.
- `depends_on: [[CORR-723]]` because both edit `_adopt_running_bots` and
  [[CORR-723]] decides what the function does on unknown liveness, which this
  item's retry condition has to agree with. They would serialise on file overlap
  anyway; the edge fixes the order.
- #262's step 1 (the unrecognised dotted tool-call shape, which is *why* the
  deployment was missed in the reported case) is [[SEC-722]], filed separately
  because its fix is in the ACP seam and its worse consequence is the danger
  gate. Both are needed - this item covers the ledger even when the call *is*
  recognised.
- #262's remaining ask — labelling session vs lifetime scope explicitly — is
  [[FEAT-135]].
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.34
  `CORR-633 [done]`, below the 0.50 fallback bar; `CORR-699 [done]` at 0.19 is
  the doc that added the filter this item extends.

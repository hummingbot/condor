---
id: CORR-724
title: Adoption runs once and only over performance names, so a live bot with no reading is never owned
summary: _adopt_running_bots iterates the controller-performance snapshot, so a live bot with no performance rows is never adopted — and _adoption_done is set after the first successful pass, so no later tick recovers it. The session then reports lifetime totals for a bot it never recorded owning.
category: correctness
impact: high
effort: M
risk: medium
status: done
brain: developer
files:
  - condor/agents/engine.py:1080
  - condor/agents/engine.py:1140
  - condor/agents/engine.py:1153
  - condor/agents/ownership.py:295
depends_on:
  - "[[CORR-723]]"
commits:
  - "d3f1d83e fix(bots): adopt from the live registry, and stop adoption closing on an empty pass (CORR-724)"
created: 2026-10-14
closed: 2026-10-15
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

- [x] Fake client: a bot present in the live registry and absent from the performance snapshot, whose base matches the namespace → adopted
- [x] Fake client: first tick's registry and snapshot are both empty → `_adoption_done` does not close adoption, and a second tick with a live bot adopts it
- [x] A recorded deploy confirmed live on tick 3 → ownership recorded, with an ownership window starting no earlier than the deploy
- [x] An adopted-then-disowned bot is still not re-adopted (`read_disowned` keeps outranking all sources)
- [x] With ownership recorded, the session's performance no longer includes a prior stopped generation's volume (the #262 symptom), asserted against API fixtures and `windows={}` as #262 describes
- [x] No test that passed at the merge base fails: `uv run pytest` run on the merge base and on the change in the same turn, compared

## Notes

### How it was shipped

- `_adoption_done` is now a **property** on `TickEngine`
  (`condor/agents/engine.py:1084`) reading `bool(self.ledger.bases())`, not a
  latched field. One flag meaning both "a pass ran" and "adoption is finished"
  was the whole of the one-shot defect; owning nothing is not a finished state.
  The retry condition the proposed solution asks for falls out of it: the gate is
  `self._adoption_done and not recorded - self._adoption_deploys`, so a pass runs
  while the ledger is empty *and* again whenever this session's action log records
  a deploy the last pass had not seen. `_adoption_deploys` is set only after the
  liveness gate, so a deferred pass does not consume the trigger.
- **Deviation, point 1.** The proposed union of snapshot and live registry
  reduces to the live registry: the loop filtered every candidate through
  `if instance_name not in live: continue` (CORR-699), so
  `set(all_perf) | live` filtered by `live` is exactly `live`, and
  `set(all_perf)` would have been a provably dead term. So the loop iterates the
  orchestrator's listing directly and the `fetch_all_bot_performance` call is
  gone from this function — with it the deferral on a snapshot failure, which
  under the new source set could only withhold a correct adoption. It halves the
  per-tick cost of a retrying pass, and the one remaining read is server-cached.
- **Point 3 needed no code of its own.** Repair on confirmation is what the retry
  condition does: `recorded` is already folded into `inherited`, so the tick on
  which a recorded deploy first appears live reaches `ledger.adopt` through the
  existing path. `ownership.py:295` (`note_deploy` / `adopt`) is therefore
  unedited — `adopt` already opens the window at the instant it is called, which
  is after the deploy by construction, and the defect was never in how ownership
  is recorded but in when a pass runs. The edit to `condor/agents/ownership.py`
  is a one-line correction to `disown`'s docstring, which said adoption
  re-derives ownership "on the first tick".
- **Two existing assertions were inverted on purpose**, both asserting the
  behaviour this item removes:
  `test_engine_adopts_nothing_from_a_log_with_no_deploys` and
  `test_adoption_adopts_nothing_when_the_server_is_genuinely_empty` (whose
  comment read "answered — no point retrying every tick"). The second now
  carries criterion 2's second tick.
  `test_engine_adoption_failure_is_non_fatal_and_retried` was replaced by
  `test_adoption_owns_a_live_bot_the_snapshot_never_reported` (criterion 1): the
  snapshot fetch it exercised is no longer part of adoption, and unknown liveness
  is already covered by `test_adoption_defers_when_liveness_is_unknown_and_retries`.
- `test_adoption_skips_a_disowned_bot_it_recorded_deploying` passed at the merge
  base for the wrong reason — it called `_adopt_running_bots(object())`, which
  deferred on unknown liveness before ever reaching the disowned check. It now
  gets a live listing, so the disowned set is what stops the bot.
- Verification: full `uv run pytest` on the change — **6439 passed, 2 skipped** —
  against the merge base `8e14f46f` in a detached sibling worktree —
  **6415 passed, 23 skipped** (that worktree has no `frontend/dist`, which skips
  21 web-route tests). `pytest --collect-only` diffed between the two: exactly
  one test id removed and four added, as above. `black --check` and `isort
  --check-only` clean on all four files; the repo configures no other linter.

### From the audit

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

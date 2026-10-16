---
id: TASK-002
title: Land or retire origin/feat/acp-fable-option, which makes an unappliable model pin fail the session
summary: Issue 221's protocol fix is already in main, but its "fail loudly" half sits unmerged on origin/feat/acp-fable-option since 2026-09-28. Until it lands, a pin the bridge cannot apply is still only a log.warning and the session runs on the adapter default, billing Opus rates for a haiku or sonnet pin.
status: todo
effort: S
risk: low
actor: human
blocked_reason: A maintainer must decide whether to merge or close origin/feat/acp-fable-option; no unattended session merges a branch.
files:
  - condor/acp/client.py
  - condor/doctor.py
depends_on: []
commits: []
created: 2026-10-14
---

## Goal

Decide the fate of the branch `origin/feat/acp-fable-option` (`95729442`), so
that the remaining half of GitHub issue
[#221](https://github.com/hummingbot/condor/issues/221) is either shipped or
deliberately dropped — rather than sitting in a state where the issue looks open
but the work is written.

## Context

Measured at `45e60d60`:

- **#221's primary claim is already fixed in main.** `advertised_models`
  (`condor/acp/client.py:383-403`) reads the `configOptions` entry with
  `category: "model"`, and `_select_model` (`:728-731`) switches with
  `session/set_config_option` using `configId`, falling back to the old
  `models` block and `session/set_model` only when a legacy adapter advertises
  them. `active_model_id` is taken from `currentValue`. So the protocol drift
  the issue is titled for no longer applies.
- **The loud-failure half is written but unmerged.** `95729442` ("A pinned model
  the bridge doesn't offer, or a switch it refuses, fails the session instead of
  running it on the default…", 2026-09-28) touches `condor/acp/client.py`,
  `condor/doctor.py`, `tests/test_claude_bridge_upgrade.py` and
  `tests/test_doctor.py` (+86/−26). `git merge-base --is-ancestor 95729442 HEAD`
  reports **not in HEAD**; `git branch -a --contains` names only
  `remotes/origin/feat/acp-fable-option`.
- **So the live gap is real.** At `45e60d60`, `_select_model` on an unmatched
  model logs `"ACP model %r not found in advertised models %s; keeping default
  %s"` and returns. The session runs on the adapter default, which #221 records
  as `opus[1m]` — so a `claude-acp:haiku` or `claude-acp:sonnet` agent quietly
  bills at Opus rates, and in a race every entry meant to share one model
  becomes incomparable.

This is `actor: human` because merging a branch is a maintainer's call, not an
unattended session's — the branch is two and a half weeks old and may have
diverged or been superseded.

## Steps

1. Review `95729442` against current `main`, and rebase it if it no longer
   applies cleanly.
2. Confirm it still does what its message claims — an unappliable pin fails the
   session with an error naming what the bridge offers, and the bridge is
   stopped rather than orphaned.
3. Merge it, or close the branch and say here why the loud failure is not
   wanted.
4. Update GitHub issue #221 either way, recording that the protocol half shipped
   at or before `45e60d60` and what happened to the loud-failure half.

## Acceptance criteria

- [ ] `95729442`'s change is an ancestor of `main`, or the branch is closed with the reason recorded in this doc
- [ ] If merged - a pin the bridge does not advertise fails the session with an error naming the advertised options, instead of a `log.warning`
- [ ] GitHub issue #221 reflects the state of both halves
- [ ] The third ask in #221's race comment — showing the effective model for a pin audit — is **not** covered by that branch (checked - it touches no route or frontend file) and remains with [[FEAT-136]]

## Notes

- Numbered TASK-002 deliberately. `reserve_backlog_id` offered TASK-001, but
  that id is already used by `c6f85545` ("chore(backlog): file TASK-001 —
  decide the fate of the gitignored backlogs") on the unmerged branch
  `feat/risk_engine_improvements`. Taking it would mint a duplicate id the
  moment that branch merges; ids are never reused and a gap is harmless.
- That other task is directly relevant here - `/improvements/` and `/features/`
  are gitignored in this repo (`.gitignore:186-187`, 0 tracked docs), which is
  why the backlog filed alongside this doc cannot be turned into a plan
  (`create_plan` refuses a `source_doc` git will not track). `tasks/` is not
  ignored, which is why this doc can be committed.

## Decision

Settled by the repo owner (federico) on 2026-10-16: **close `origin/feat/acp-fable-option`, do not merge it.**

That removes the blocker but does not do the work. The "fail loudly" behaviour still has to exist, so write it fresh rather than cherry-picking from the closed branch:

- `condor/acp/client.py` — a model pin the bridge cannot apply must raise, not `log.warning` and fall through to the adapter default.
- `condor/doctor.py` — report an unappliable pin as a failing check, so it is visible before a session starts billing Opus rates for a haiku or sonnet pin.

Closing the branch on GitHub is a separate manual step for the owner, and is not what closes this doc.

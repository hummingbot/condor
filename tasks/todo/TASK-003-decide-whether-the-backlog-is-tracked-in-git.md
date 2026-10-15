---
id: TASK-003
title: Decide whether improvements/ and features/ are tracked in git — while they are not, no plan can ever close a backlog item
summary: >-
  create_plan refuses every source_doc under improvements/ or features/,
  naming .gitignore:186 and :187 - a plan closes a doc by committing its move
  to done/, and git will not track these. So the supervised plan route is
  unusable for 1016 of Condor's 1017 docs, and ship-by-hand is the only way to
  close one. Measured on the 21-doc Agent Builder Cup cluster.
status: blocked
effort: S
risk: medium
actor: human
blocked_reason: Tracking the backlog in git reverses an explicit owner decision — e4eeeb9d "(feat) remove improvements from git", cardosofede, 2026-06-17. Only the repo owner decides whether to keep the by-hand convention or adopt the plan machinery; no session reverses that on its own.
files:
  - .gitignore
depends_on: []
commits: []
created: 2026-10-15
---

## Problem

`create_plan` pins each chunk's `source_doc` git blob sha and closes the doc by
committing its move into `done/`. It therefore refuses, structurally, any doc
git will not track:

```
chunk 1: source_doc "improvements/backend/todo/CORR-720-...md" is ignored by
the project's git (.gitignore:186:/improvements/) — a plan closes a doc by
committing its move to done/, and a doc git will not track can never be
committed. Track the backlog in git, or ship this doc by hand
```

Measured 2026-10-15 in the `ws/issues-of-agent-builder-cup` worktree over the
21-doc Agent Builder Cup cluster: `git check-ignore -v` reports **all 21**
ignored — 12 improvements by `.gitignore:186:/improvements/` and 9 features by
`.gitignore:187:/features/`. `git ls-files` returns **0** files under either
directory. The only trackable backlog doc in the repo is
`tasks/todo/TASK-002-...md`, and `tasks/` is not ignored at all. So of the 1017
docs the index reports for this project, 1016 are unplannable, and the one that
is plannable declares `actor: human` with a `blocked_reason`.

The ignore is deliberate and dated: `e4eeeb9d`, 2026-06-17, cardosofede,
*"(feat) remove improvements from git"*. This item is the decision, not a fix —
un-ignoring the backlog is a reversal of that commit's intent.

Consequences while it stands, each already observed in this repo:

- No `create_plan` call over an improvement or a feature can succeed, so
  `execute_plan`, chunk supervision, the final verification chunk and
  `source_doc` sha pinning are all unreachable here. The `/burndown`,
  `/ship-improvement` and `/ship-feature` skills exist because of this.
- A closure leaves no git trace, so `todo/` can disagree with what is already
  shipped and nothing reconciles them.
- `git mv` fails on these paths; closing an item needs a plain `mv`.
- A subagent working in a fresh worktree cut from the branch cannot read the
  backlog at all — the docs are not on the branch, so they do not arrive with
  the checkout. They have to be copied in or staged in a scratchpad.
- The doc index's workstream view is git-derived, so
  `list_backlog(workstream=...)` returns nothing for these docs even with the
  files present on disk.

## The decision

Either:

**(a) Keep the convention.** `improvements/` and `features/` stay untracked and
disk-only; the plan machinery is accepted as unavailable for this repo and
items ship through `/ship-improvement`, `/ship-feature` and `/burndown` only.
If this is the answer, say so where a session will read it — `CLAUDE.md` — so
the next composer does not rediscover the refusal.

**(b) Track the backlog.** Delete the two `.gitignore` lines and commit the
docs. Then plans work, closures are auditable, worktree subagents can read
their own item, and the workstream view populates. The cost is that every
backlog doc and every closure becomes a reviewable repo change, which is what
`e4eeeb9d` chose against.

## Steps

1. Decide (a) or (b). Only the repo owner can.
2. If (a): write the convention and its consequence into `CLAUDE.md` — that the
   backlog is deliberately untracked, that plans cannot close its items, and
   that `/ship-*` is the route.
3. If (b): remove `.gitignore:186-187`, `git add improvements/ features/`,
   commit, and re-run the composition this item blocked.

## Acceptance criteria

- [ ] `git check-ignore -v improvements/ features/` reflects the decision taken
- [ ] If (a): `CLAUDE.md` states that the backlog is untracked by design, that
      `create_plan` refuses its docs, and that `/ship-improvement`,
      `/ship-feature` and `/burndown` are the only close-out route
- [ ] If (b): `git ls-files improvements/ features/ | wc -l` is non-zero, and a
      `create_plan` call naming one improvement and one feature as
      `source_doc` is accepted rather than refused
- [ ] Either way, the 21 Agent Builder Cup docs (CORR-720, CORR-721,
      CORR-723 to CORR-729, CORR-731, CORR-732, PERF-733, FEAT-133 to FEAT-141)
      have a stated route to being closed

## Notes

Found 2026-10-15 while composing one plan over the Agent Builder Cup cluster on
the `issues-of-agent-builder-cup` workstream. The composition itself is
complete and validated — two file-overlap components (a 19-doc one fused by
`condor/agents/engine.py` and `condor/web/routes/agents.py`, both named by 11
docs, plus a 2-doc `condor/layering.py` pair), a 21-chunk order with every
`depends_on` edge satisfied, and a final `scout` verification chunk. Only
`create_plan` refused. The order is reproducible from the docs' own frontmatter
and does not need re-deriving.

`tasks/` being trackable is why this item could be filed at all; a doc about the
untrackable backlog could not itself have been committed into it.

## Update 2026-10-15 — route (c), per cluster

A third route exists that reverses nothing repo-wide. `create_plan`'s ignore
check is `git check-ignore` without `--no-index`, which skips tracked files, and
a tracked file's `git mv` into an ignored `done/` commits normally (verified in a
scratch repo). So the 21 Agent Builder Cup docs were force-added
(`git add -f`) on `ws/issues-of-agent-builder-cup` only; `.gitignore:186-187`
are unchanged. That satisfies the last acceptance criterion for this cluster.
On `merge_workstream` the 21 docs land on main as tracked files under `done/`;
`git rm --cached` them afterwards if main should stay doc-free.

The repo-wide decision (a) or (b) is still open, so this item stays blocked.

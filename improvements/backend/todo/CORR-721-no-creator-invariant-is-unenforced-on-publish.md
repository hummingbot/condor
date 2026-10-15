---
id: CORR-721
title: The shipped library's "no creator" invariant is enforced nowhere — publish_to_stock keeps created_by, and the guard test's strategy.md glob has matched nothing since the loop rename
summary: >-
  CORR-705's guard globs agents/**/strategy.md, which FEAT-128 renamed to
  loop.md, so it matches zero files and six tracked loop playbooks go
  unchecked. And publish_to_stock — the one path that writes into the shipped
  library — clears the fork stamp but never zeroes created_by.
category: correctness
impact: medium
effort: S
risk: low
status: todo
brain: developer
files:
  - condor/layering.py:302
  - condor/layering.py:228
  - tests/test_shipped_library_has_no_creator.py:32
depends_on: []
commits: []
created: 2026-10-07
---

## Problem

[[CORR-705]] established that a tracked `agents/**` front matter must carry
`created_by: 0` or no key at all: `agents/` is the stock layer every install
reads, a real Telegram id there points to nobody on another box, and the
admin-principal rule and the legacy-restart owner fallback both still act on it.
It closed by cleaning ten files and adding
`tests/test_shipped_library_has_no_creator.py` so that "a later save-from-the-
editor and commit cannot reintroduce the problem".

The data is still clean. The *invariant* is enforced by nothing.

**1. The guard scans no loop playbook.** The test's pathspec
(`tests/test_shipped_library_has_no_creator.py:29`–`:33`) is
`agents/**/AGENT.md` and `agents/**/strategy.md`. FEAT-128 renamed the playbook
file to `loop.md` and moved it under `loops/` (`condor/agents/strategy.py`:
`LOOP_MD = "loop.md"`), so the second pattern matches zero files. Measured at
`c6f85545`:

```
$ git ls-files 'agents/**/strategy.md'        # (no output)
$ git ls-files 'agents/**/AGENT.md' | wc -l
6
$ git ls-files 'agents/*/loops/*/loop.md' | wc -l
6
```

Six tracked `loop.md` files — `adaptive_grid_trader` ×2, `directional_trader`,
`market_making_expert` ×2, `solana_dex_lp_expert` — are outside the guard. All
six happen to read `created_by: 0` today, so this is a blind guard rather than a
live leak; `test_no_tracked_agent_or_strategy_names_a_creator` passes by
scanning half the surface it was written for. CORR-705's own acceptance
criterion ("a new test fails if one is added") no longer holds for the file kind
that carries the most of this front matter.

**2. The one path that writes into the library does not scrub the field.**
`condor/layering.py:302` `publish_to_stock` is, by its own docstring, "the only
write into the tracked tree in the product". It copies each file and calls
`clear_fork_stamp` (`:228`), which removes `forked_from`/`forked_at` and nothing
else. A local agent's `AGENT.md` and `loops/*/loop.md` legitimately carry the
operator's real `created_by` — `AgentStore._save` and `StrategyStore._save`
write it, and CORR-705 explicitly chose to keep it in the local layer — so
publishing one copies that id straight into `agents/`. On a machine where the
loop playbook is the thing being published, the guard does not even notice;
where `AGENT.md` is, the guard turns a privacy leak into a red test at commit
time, which is better but is not the fix.

## Proposed solution

One invariant, enforced on the write and on the tree.

1. In `condor/layering.py`, scrub `created_by` on the way into stock, beside the
   fork stamp and for the same stated reason: a shipped file must not claim a
   fact that is only true on the install that published it. Either extend
   `clear_fork_stamp` into the publish-time scrubber it already is (and rename
   it for what it does), or add a sibling the publish loop calls — but keep it
   in `publish_to_stock`'s loop, not in the stores, which must keep recording
   the real creator locally.
2. Widen the guard's pathspec in
   `tests/test_shipped_library_has_no_creator.py` from `strategy.md` to
   `loop.md`, and assert the scan is not vacuous: each pattern must match at
   least one tracked file, so the next rename fails the test instead of
   silently emptying it. Take the two filenames from
   `condor.agents.agent.AGENT_MD` and `condor.agents.strategy.LOOP_MD` rather
   than restating the literals, so a third rename cannot drift them apart
   again.

Do not add load-time rewriting in the stores — CORR-705 ruled that out and the
reason stands: a user's own local-layer agents must keep their real creator.

## Acceptance criteria

- [ ] `publish_to_stock` on a local agent whose `AGENT.md` and
      `loops/l/loop.md` both carry `created_by: 481175164` writes both files
      into `stock_agent_home(slug)` with `created_by: 0`, and the local copies
      still read `481175164`
- [ ] The published files still have no `forked_from`/`forked_at` — the
      existing `test_publishing_clears_the_fork_stamp` stays green
- [ ] The guard's pathspec matches `loop.md`: with the widened patterns,
      `_tracked_library_files()` returns at least the 6 `AGENT.md` and 6
      `loop.md` paths that `git ls-files` reports at the tested commit
- [ ] The guard fails when any one pattern matches nothing, so emptying a
      pattern by a rename is a failure rather than a silent pass — exercised by
      a test that passes a pattern matching no file
- [ ] A `loop.md` hand-edited to `created_by: 481175164` makes
      `test_no_tracked_agent_or_strategy_names_a_creator` red; it is green on
      the unmodified tree. Both runs in the same turn.
- [ ] `uv run pytest` reports no failure that is not already failing on the
      merge-base: run it on the merge-base and on the change in the same turn
      and compare the two lists, rather than against a recalled count

## Notes

- Verified against the committed tree at `c6f85545`: the pathspec at
  `tests/test_shipped_library_has_no_creator.py:29`–`:33`, `publish_to_stock` at
  `condor/layering.py:302`, `clear_fork_stamp` at `:228`, and the three
  `git ls-files` counts quoted above.
- [[CORR-705]] is `done` and its data fix holds — all 6 tracked `AGENT.md` and
  all 6 tracked `loop.md` read `created_by: 0` at `c6f85545`. This is a
  regression of its *second* criterion only, caused by FEAT-128's rename, plus
  the publish path it never covered. CORR-705's own notes anticipate the
  remainder: "copied or imported folders and deleted users still produce
  orphans."
- The import half of that sentence is [[FEAT-133]]'s to answer, and it does:
  a bundle is exported with `created_by: 0` and stamped with the importer's id
  on the way in. FEAT-133 does not reuse `publish_to_stock`'s scrub, so it does
  not depend on this item.
- Same family as [[CORR-720]]: this install's local facts crossing into the
  library the repo ships.

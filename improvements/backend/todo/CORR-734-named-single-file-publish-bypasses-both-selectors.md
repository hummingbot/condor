---
id: CORR-734
title: publishable_files applies neither selector to an explicitly-named single file, so naming a path publishes this install's runtime state into the shipped library
summary: >-
  publishable_files returns an explicitly-named file unfiltered, skipping both
  _RUNTIME_NAMES and CORR-720's root allowlist, so publish_to_stock(slug,
  'state/op.json') copies runtime state into the tracked agents/ tree. Measured
  here: 333 files leakable by name against the 45 that are library content.
category: correctness
impact: medium
effort: S
risk: low
status: todo
brain: developer
files:
  - condor/layering.py:338
  - condor/layering.py:326
  - condor/layering.py:329
  - tests/test_agents_layering.py:685
depends_on: []
commits: []
created: 2026-10-15
---

## Problem

`condor/layering.py:322` `publishable_files(home, path)` decides what an agent
*is*, as opposed to what this install accumulated by running it. It has two
branches and only one of them filters.

The whole-agent branch (`:345`-`:353`) applies three rules: skip dotfiles
(`:346`), skip any path with a `_RUNTIME_NAMES` component at any depth
(`:349`), and skip a nested path whose root directory is not one the layout
defines (`:351`, the allowlist [[CORR-720]] added). The single-file branch is
two lines above it and applies none of them:

```python
rel_parts = tuple(part for part in path.split("/") if part and part != "..")
base = home.joinpath(*rel_parts)
if base.is_file():
    return [Path(*rel_parts)]      # :338-339 — no filter of any kind
```

So `publish_to_stock(slug, "state/op.json")` or
`publish_to_stock(slug, "store/user_7/audit.log")` copies this install's
runtime state straight into the tracked `agents/` tree, even though the bare
whole-agent publish now correctly refuses both.

**The function's own docstring asserts the opposite, twice.** `:326`-`:327`
reads *"The runtime filter applies either way, so naming a folder cannot
smuggle a store out."* The first clause is false and the second is true — and
the second is what makes the sentence dangerous, because it defends the one
direction that is already safe. Naming a **folder** is genuinely refused
(`publishable_files(home, "store")` returns `[]`, because the branch below
filters every candidate it walks). Naming a **file** is not. The word in that
sentence is `folder`, which is exactly the gap.

CORR-720 then added a second, more confident statement of the same contract at
`:329`-`:331`: *"Two rules, because the layout is only closed at one level. A
root *directory* must be one the layout defines; a root *file*, and any name
at any depth, must not be runtime output (`_RUNTIME_NAMES`)."* Stated
unconditionally, as the function's rules. `config.yml` is a root file and is in
`_RUNTIME_NAMES`, and `publishable_files(home, "config.yml")` returns it.

### Reproduction

Measured at `e8736550` on the workstream branch, against this box's real agent
homes at `/Users/dman/Documents/work/condor/.condor/agents` (the canonical
checkout's local layer; `main` does not carry CORR-720's fix):

| agent | library files | leakable if named |
| --- | --- | --- |
| adaptive_grid_trader | 0 | 0 |
| adaptive_orca_lp | 6 | 1 |
| backpack_mm | 8 | 16 |
| brigado | 17 | 193 |
| condor | 0 | 23 |
| directional_trader | 7 | 66 |
| fundamental_analyst | 0 | 1 |
| market_making_expert | 0 | 22 |
| regime_switch_lp | 7 | 5 |
| solana_dex_lp_expert | 0 | 6 |
| **total** | **45** | **333** |

333 files are reachable by naming them, against 45 the selector calls library
content. What is in there is this install's, not the library's:
`store/user_481175164/MEMORY.md`, `store/user_481175164/audit.log` and 18 more
memory files under a real Telegram user id; every
`loops/*/sessions/session_N/actions.jsonl` and `canvas.md`;
`controllers/*/.server_backups/local-*.py`;
`delegations/*/transcript.md`; `loops/*/dry_runs/experiment_*.md`;
`routines/__pycache__/*.pyc`; and `loops/*/config.yml`.

**The sharpest case is CORR-720's own file.** On `regime_switch_lp`:

```
bare publish includes state/regime_decision_state.json : False
publishable_files(home, "state/regime_decision_state.json") : ['state/regime_decision_state.json']
```

4923 bytes of tuning audit trail and peak-equity state — the exact file
CORR-720 measured and closed. The fix closed one path to it and left the other
open, on the same file.

### Why the docstring matters more than the reachability

Today the blast radius is bounded, the same way CORR-720's was:
`publish_to_stock` is admin-only (`tests/test_agents_layering.py:738`) and
leaves the copies dirty in the repo for the maintainer to read before
committing. The caller has to name the path, so nothing reaches this by
accident.

That is an argument about *today's two call sites*, not about the function. The
selector is the repo's one answer to "what is this agent, as opposed to what
this install accumulated", it is documented as total, and the next caller will
read the docstring rather than the branch. [[FEAT-133]] is the next caller: it
reuses `publishable_files` as its bundle manifest, and a bundle leaves the
machine with no maintainer reading a diff first. As designed, FEAT-133 is
**not** exposed: it names the selector four times
(`features/todo/FEAT-133-an-agent-travels-as-one-file.md:65`, `:76`, `:205`,
`:390`) and never with a `path` argument — the manifest spec at `:205` and the
round-trip criterion at `:390` both read `publishable_files(agent_home(slug))`
— so it takes the filtered branch. The exposure is that the contract it is building on is false, and a
later per-item export — one `path` argument — inherits the hole with the
docstring saying it is safe.

## Proposed solution

Factor the whole-agent branch's three conditions into one predicate and apply
it on both branches. The rules are not changing; they are being applied in the
one place that skipped them.

1. In `condor/layering.py`, add a module-level helper beside
   `_root_library_dirs` — `_is_library_path(rel: Path, root_dirs:
   frozenset[str]) -> bool` — holding exactly the three conditions the loop
   applies today, in the same order: the leaf name does not start with `.`,
   no component of `rel.parts` is in `_RUNTIME_NAMES`, and a nested path's
   `rel.parts[0]` is in `root_dirs`. It takes `root_dirs` as an argument
   rather than calling `_root_library_dirs()` itself, so the call stays once
   per `publishable_files` call and not once per file — the property
   CORR-720's closing notes recorded for the same reason.
2. Resolve `root_dirs = _root_library_dirs()` once, above the `base.is_file()`
   check, and make the single-file branch
   `return [rel] if _is_library_path(rel, root_dirs) else []` with
   `rel = Path(*rel_parts)`.
3. Rewrite the loop body to call the same predicate, so the two branches
   cannot drift again. `candidate.is_file()` stays in the loop — it is a
   filesystem question, not a layout one.
4. Correct both docstring claims. Replace the `:326`-`:327` sentence: the
   filter applies to a named file and a named folder alike, so naming either
   cannot smuggle runtime output out. Keep the `:329`-`:331` two-rule
   paragraph, which becomes true once the predicate is shared, and say that
   `path` narrows *which* files are considered and never *whether* they are
   filtered.

A root file the layout defines still publishes, because it is a root file not
named in `_RUNTIME_NAMES`: `AGENT.md`, `core_rules.md`, `reflect.md` and
`shutdown.md` all pass, as do nested library items like `skills/basis/SKILL.md`
and `loops/carry/loop.md`.

### Alternatives considered and rejected

- **Refuse a `path` that is not under a root library directory, in
  `publish_to_stock` rather than in the selector.** Puts the layout rule in the
  caller, leaves `publishable_files` still returning unfiltered paths to its
  next consumer, and would not help [[FEAT-133]], which calls the selector
  directly and never goes through `publish_to_stock`.
- **Drop the single-file branch and let `rglob` handle it.** `rglob("*")` on a
  file yields nothing, so this would make every named-file publish return `[]`
  and break `publish_to_stock(slug, "AGENT.md")` — the one named-file call site
  the product has.
- **Leave it, and only fix the docstring.** This is what CORR-720 chose, and
  its closing notes say so explicitly ("filed separately rather than widened
  into this item"). Rejected now because the reachability is 333 files against
  45, because it is the mechanism by which that item's own measured leak is
  still reachable, and because [[FEAT-133]] is building on the contract.

## Acceptance criteria

- [ ] `_is_library_path` exists in `condor/layering.py` and is the only place
      the three conditions are written; both `publishable_files` branches call
      it, and `grep -c "_RUNTIME_NAMES" condor/layering.py` shows the set is
      read in the predicate and nowhere else in that function
- [ ] `publishable_files(home, path)` returns `[]` for each of
      `state/op.json`, `store/user_7/audit.log`, `config.yml`, `mutes.yml` and
      `routines/__pycache__/x.cpython-312.pyc` on a home that contains them
- [ ] `publishable_files(home, path)` still returns the single path for each of
      `AGENT.md`, `core_rules.md`, `reflect.md`, `shutdown.md`,
      `skills/basis/SKILL.md`, `loops/carry/loop.md` and `routines/r.py`
- [ ] **Positive control, as a difference and not a set equality:** for every
      agent home under a fixture root, `publishable_files(home)` with no `path`
      returns a list identical to the one it returns before the change. The
      change must move the named-file branch and nothing else; assert the two
      lists are equal rather than asserting either equals a literal set, so the
      control stays meaningful as fixtures change
- [ ] A new test in `tests/test_agents_layering.py` named for the behaviour
      (`test_naming_a_file_cannot_smuggle_runtime_output_out`, beside
      `test_the_root_rule_excludes_invented_folders_not_the_name_state` at
      `:653`) is **red at the merge base** and green after. Run it in a
      detached worktree at the merge base with the test file copied in, and
      quote both readings in the closing notes
- [ ] `publish_to_stock(slug, "state/op.json")` returns the
      `Nothing publishable at '<slug>/state/op.json'` error branch
      (`condor/layering.py:376`-`:377`) rather than publishing, and creates no
      `state/` under the shipped home
- [ ] `tests/test_agents_layering.py:685` and `:733`, the two existing
      `publish_to_stock(..., "AGENT.md")` call sites, pass unchanged — no edit
      to either test
- [ ] Both docstring claims read true: the `:326`-`:327` sentence no longer
      says "folder", and the `:329`-`:331` two-rule paragraph is accurate for
      both branches
- [ ] No test that passed at the merge base fails. Measure by running
      `uv run pytest` on the merge base and on the change in the same turn and
      comparing the two. For provenance, that command read
      **6432 passed, 2 skipped, 7 warnings in 95.87s, exit 0** at `e8736550` —
      the suite is green there, so the invariant is that it stays green.
      Re-measure rather than expecting that count: the cluster's plan is
      landing items while this doc sits in `todo/`, and the touched-file
      suites `tests/test_agents_layering.py` and
      `tests/test_agent_controllers.py` read **94 passed in 1.04s** at the same
      sha
- [ ] `uv run black --check condor/layering.py tests/test_agents_layering.py`
      and `uv run isort --check-only` on the same two files are clean

## Notes

- Verified against the committed tree at `e8736550`: the unfiltered early
  return at `condor/layering.py:338`-`:339`, the three loop conditions at
  `:346`, `:349` and `:351`, the two docstring claims at `:326`-`:327` and
  `:329`-`:331`, the `publish_to_stock` call at `:374`, and the only
  named-file test call at `tests/test_agents_layering.py:685`.
- **Not a regression from [[CORR-720]].** The early return predates it —
  `git log -S 'if base.is_file():' -- condor/layering.py` names one commit,
  `fbd03bdc (feat) publishing is how the shipped library stays authorable` —
  and it bypassed `_RUNTIME_NAMES` just as completely before CORR-720 existed.
  `git show bd203838 -- condor/layering.py` leaves lines `320`-`325` of that
  tree untouched. CORR-720's fix is verified still holding: the root allowlist
  is live on the whole-agent branch, and its own acceptance criteria still
  pass.
- **CORR-720 recorded this deferral and said it was filed; it was not.** Its
  closing notes (`improvements/backend/done/CORR-720-…md:204`-`:209`) read
  "**Left alone, deliberately.** … `publish_to_stock(slug, "state/op.json")`
  would copy it. That is pre-existing, admin-only and deliberate on the
  caller's part, and the solution asked to keep the function's shape; it is
  filed separately rather than widened into this item." No such doc was
  written — `list_backlog(file="condor/layering.py", status="all")` returns
  exactly two docs, CORR-720 itself and [[CORR-721]] (a different concern).
  This doc is that filing. Its "deliberate on the caller's part" reading is the
  one thing worth disputing: the caller reads a docstring that says the filter
  applies either way.
- **The fix was simulated in-process before these criteria were written**, at
  `e8736550`, by monkeypatching the predicate over the real module and
  replaying it across all 10 local agent homes: whole-agent output identical
  for every one of them, `leakable-if-named` **333 → 0**, and all seven
  legitimate named-file paths still passing. That is where the criteria above
  come from.
- **The dotfile condition is latent, not live.** No agent home on this box has
  a dotfile at any level whose own leaf name starts with `.` —
  `.server_backups/local-*.py` is caught by `_RUNTIME_NAMES` on the directory
  component, which is what the comment at `:287`-`:290` says. It is included in
  the shared predicate for symmetry, not to close a measured leak.
- **[[CORR-721]] landed mid-investigation and does not overlap.** It renamed
  `clear_fork_stamp` to `scrub_for_stock` and added a `created_by` zeroing in
  `publish_to_stock`'s loop; `git diff 5af2454c 3c68b8c6 -- condor/layering.py`
  touches nothing in `publishable_files`. It does sharpen this item: the scrub
  returns early for any file whose suffix is not `.md` (`:244`-`:245`), so a
  named `state/op.json` or `audit.log` crosses into the shipped tree with no
  scrub applied at all. That is correct for CORR-721's scope — the answer is
  not copying the file — and is not filed separately.
- **File overlap with the running plan.** Plan `89937873-9f5` is executing this
  21-doc cluster and already holds [[CORR-721]] and [[FEAT-133]], both
  declaring `condor/layering.py`. This doc is held by no plan, so it needs a
  later `compose_plan` pass or an `edit_plan_chunks` amendment once that plan
  is no longer `RUNNING`. FEAT-133 is scheduled near the end of that plan and
  will therefore be implemented while this hole is open — harmless as it is
  designed, per `## Problem`, but worth sequencing deliberately if FEAT-133
  grows a per-item export.
- Same family as [[CORR-705]], [[CORR-720]] and [[CORR-721]]: this install's
  local facts crossing into the library the repo ships. CORR-705 cleaned the
  data once; the other three close the paths that put it back. This is the last
  of the three publish paths that is still open.
- `improvements/` and `features/` are gitignored (`.gitignore:186`, `:187`) and
  tracked only on this workstream branch by force-add — 12 files here against 0
  on `main`. That is [[TASK-003]]'s subject, and it is why this doc was
  committed with `git add -f`. It is also why `[[CORR-705]]` above resolves to
  no file on this branch: that doc sits untracked in the canonical checkout's
  `improvements/backend/done/`. Both CORR-720 and CORR-721 cite it the same
  way, and this repo has no backlog-link gate test, so the form is the
  established one here rather than a dangling link to repair.

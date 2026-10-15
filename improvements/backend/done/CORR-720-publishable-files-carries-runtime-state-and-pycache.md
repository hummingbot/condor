---
id: CORR-720
title: publishable_files is a denylist pretending to be an allowlist, so a publish copies this install's routine state and .pyc files into the shipped library
summary: >-
  publishable_files skips only _RUNTIME_NAMES and dotfiles, so an
  agent-invented state/ folder and every routines/__pycache__/*.pyc publish
  into the tracked agents/ tree. Measured here: 3 .pyc files plus
  state/regime_decision_state.json (peak equity, last profile). Make the root
  level an allowlist.
category: correctness
impact: medium
effort: S
risk: low
status: done
brain: developer
files:
  - condor/layering.py:256
  - condor/layering.py:296
  - tests/test_agents_layering.py:603
  - condor/memory/paths.py
  - condor/routine_store.py
depends_on: []
commits:
  - "bd203838 fix(layering): make publishable_files an allowlist at the agent root (CORR-720)"
created: 2026-10-07
closed: 2026-10-15
---

## Problem

`condor/layering.py:277` `publishable_files` decides what an agent *is*, as
opposed to what this install accumulated by running it. Its own comment at
`:252` states the contract as an allowlist — "Anything not named here is this
install's, not the library's" — but the code at `:293`–`:296` is a denylist: a
file is library content unless its name starts with `.` or one of its path
parts is in `_RUNTIME_NAMES` (`:256`).

A denylist cannot enumerate what agents invent. Two things already escape it on
this install:

1. **`__pycache__`.** Nothing in `_RUNTIME_NAMES` names it and
   `regime_decision.cpython-312.pyc` does not start with a dot, so compiled
   bytecode is library content. It is `.gitignore`d at the destination
   (`.gitignore:2`), so it does not reach a commit — but it is copied, and it
   would be carried by anything else that reuses this selector.
2. **A root `state/` directory.** `_RUNTIME_NAMES` has the *file* name
   `state.json` (written into a loop directory by
   `condor/runtime/state.py:_state_dir`) but not the directory `state`. An
   agent-authored routine that keeps its own state under
   `agent_home(slug)/state/` is therefore published, and that path is **not**
   gitignored, so it lands dirty in the tracked tree and a maintainer running
   `git add agents/<slug>` commits it.

Measured against the committed selector, on this install's
`.condor/agents/regime_switch_lp`:

```
$ uv run python -c "from pathlib import Path; from condor.layering import publishable_files
for p in publishable_files(Path('.condor/agents/regime_switch_lp')): print(p)"
AGENT.md
loops/regime_operator/loop.md
routines/__pycache__/diagnose.cpython-312.pyc
routines/__pycache__/propose_tuning.cpython-312.pyc
routines/__pycache__/regime_decision.cpython-312.pyc
routines/diagnose.py
routines/propose_tuning.py
routines/regime_decision.py
shutdown.md
skills/lp_operator_playbook/SKILL.md
state/regime_decision_state.json
```

`state/regime_decision_state.json` is this operator's accumulated machine state
— peak equity, last regime profile, bars held, cooldown — written by
`.condor/agents/regime_switch_lp/routines/regime_decision.py:407` (`_state_path`,
local-only and untracked, which is why the leak is in the *selector* and not in
that routine). `loops/regime_operator/dry_runs/experiment_1.md` was correctly
excluded, which is the behaviour the rest of the list should have.

`tests/test_agents_layering.py:603` asserts the published set *exactly*, and is
green: its fixture writes only `store/`, `mutes.yml` and `learnings.md` — one
instance of each name already in `_RUNTIME_NAMES`. The test enumerates the
denylist rather than probing past it, which is why the gap has been invisible.

Today the blast radius is bounded: `publish_to_stock` is admin-only and leaves a
dirty tree the maintainer reviews before committing. It stops being bounded the
moment a second consumer reuses the selector without a human reading the diff —
which is exactly what [[FEAT-133]]'s bundle builder does.

## Proposed solution

Make the selector say what the comment already claims, at the one level where
the layout is actually defined — the agent home's root. The tracked library's
root entries are a closed set today (`git ls-files agents/ | awk -F/ 'NF>2
{print $3}' | sort -u`): `AGENT.md`, `core_rules.md`, `reflect.md`,
`shutdown.md`, `controllers/`, `loops/`, `routines/`, `skills/`.

1. In `condor/layering.py`, keep `publishable_files`'s shape and add a
   root-level allowlist beside `_RUNTIME_NAMES`: a path whose first part is a
   directory the layout does not define is this install's. Name the four
   library directories from the modules that own them — `routines` and `skills`
   from `condor/memory/paths.py`, `controllers` from
   `condor.agent_controllers.CONTROLLERS_DIRNAME`, `loops` from
   `condor.agents.strategy.LOOPS_DIRNAME` — rather than restating four string
   literals. Root *files* stay on the existing name denylist, so `learnings.md`
   and `mutes.yml` keep being excluded by the rule that already excludes them.
2. Add `__pycache__` to `_RUNTIME_NAMES`. It needs the any-depth rule rather
   than the root rule, because it appears *under* an allowed directory
   (`routines/__pycache__/`), and the name is reserved by Python so no library
   item can legitimately claim it.

Do **not** add a bare `"state"` to `_RUNTIME_NAMES`: `rel.parts` is matched at
any depth, so that would also drop a skill or controller legitimately named
`state` (`skills/state/SKILL.md`). The root rule is what distinguishes the two.

## Acceptance criteria

- [x] `publishable_files` on an agent home containing `state/op.json` and
      `routines/__pycache__/r.cpython-312.pyc` returns neither, and still
      returns `AGENT.md`, `routines/r.py`, `skills/s/SKILL.md`,
      `loops/l/loop.md`, `controllers/c/c.py` and
      `controllers/c/sample_configs/a.yml`
- [x] A root directory the layout does not define (use a fresh name, e.g.
      `scratch/notes.md`) is excluded, so the next agent-invented folder needs
      no further change here
- [x] `skills/state/SKILL.md` and `controllers/state/state.py` are still
      published — the fix is scoped to the root, not to the name `state`
- [x] `publish_to_stock` on that agent creates no `state/` and no
      `__pycache__/` anywhere under `stock_agent_home(slug)`, and the returned
      `paths` list names neither
- [x] The exact-set assertion in `tests/test_agents_layering.py:603` is
      extended with a `state/` file and a `__pycache__/*.pyc`, and that
      extended assertion is **red** on the merge-base and green after the
      change — demonstrated by running it on both in the same turn
- [ ] `uv run pytest` reports no failure that is not already failing on the
      merge-base: run it on the merge-base and on the change in the same turn
      and compare the two lists, rather than against a recalled count
      — **not run here**: this item shipped as chunk 1 of plan
      `89937873-9f5`, which ends in a verification chunk that runs the whole
      gate once over everything the plan lands. Running it per chunk would
      run the suite 22 times. See `## Notes` for what was run instead.

## Notes

- Verified against the committed tree at `c6f85545`: `_RUNTIME_NAMES` at
  `condor/layering.py:256`, the two skip conditions at `:293` and `:296`, and
  the exact-set test at `tests/test_agents_layering.py:603`.
- Found while designing [[FEAT-133]] (export an agent as one portable bundle),
  which reuses this selector to decide what a bundle carries and therefore
  depends on this item. A bundle leaves the machine with no maintainer reading a
  diff first, so "copied but gitignored at the destination" stops being a
  mitigation.
- Same family as [[CORR-705]] and [[CORR-721]]: this install's local facts
  crossing into the library the repo ships. CORR-705 cleaned the *data* once;
  these two close the *paths* that put it back.

## Notes — closing

**What changed.** `condor/layering.py` now applies two rules in
`publishable_files` instead of one. `_RUNTIME_NAMES` keeps its any-depth name
match and gains `__pycache__` (it sits *under* an allowed directory, so the
root rule could not catch it). A new `_root_library_dirs()` states the root
positively from the four owning modules — `CONTROLLERS_DIRNAME`
(`condor/agent_controllers.py`), `LOOPS_DIRNAME` (`condor/agents/strategy.py`),
and `ROUTINES_DIRNAME`/`SKILLS_DIRNAME` (`condor/memory/paths.py`) — and a path
whose first part is not one of them is skipped when it is nested. Root *files*
stay governed by the name denylist, so `learnings.md` and `mutes.yml` keep
being excluded by the rule that already excluded them.

**Two files beyond the declared `files:`.** The solution asked for the four
directories to be named from their owning modules rather than restated as
literals, and `routines`/`skills` had no constant to name: `condor/memory/paths.py`
now defines `SKILLS_DIRNAME` and `ROUTINES_DIRNAME` and uses them in the
resolvers that build those two directories, and `condor/routine_store.py`
uses `ROUTINES_DIRNAME` for the agent-home routines dir it resolves. Both are
mechanical substitutions of the same string.

**Deviation: `LOOPS_DIRNAME` is imported inside the function.**
`condor/agents/strategy.py` imports `condor.layering` at module scope, so
naming it at `condor.layering` module scope would be an import cycle.
`_root_library_dirs()` therefore imports all four names lazily — the pattern
`condor/agent_controllers.py:489` already uses in the other direction — and is
called once per `publishable_files` call, not once per file.

**Verification.** `tests/test_agents_layering.py` gained the extended exact-set
assertion (a `state/` file, a `routines/__pycache__/*.pyc`, a fresh
`scratch/notes.md`, plus `routines/basis_check.py` as the positive case) and a
new `test_the_root_rule_excludes_invented_folders_not_the_name_state` pinning
`skills/state/` and `controllers/state/`. Both are **red at the merge-base**
`95552e36` — run in a detached worktree at that commit with this file copied in:
`2 failed, 40 passed`, the failures being exactly those two with
`Extra items in the left set: 'state/op.json'` — and green after the change.
Touched-file suites green on the change: `tests/test_agents_layering.py`,
`test_agent_controllers*.py`, `test_agent_id_paths.py`, `test_agents.py`,
`test_skill_store.py`, `test_skill_proposals.py`, `test_shared_routines.py`,
`test_routine_discovery_cache.py`, `test_routine_source_confinement.py`,
`test_routines_access.py`, `test_memory_store.py`,
`test_agents_split_migration.py`, `test_condor_is_an_agent.py`,
`test_agent_mutes.py` — 358 passed. `black --check` and `isort --check-only`
clean on the four files; `ruff` reports one `F821 Undefined name 'RoutineInfo'`
at `condor/routine_store.py:324`, which is **pre-existing** — the same error
reports at line 323 of that file as it stands at `95552e36`.

**Left alone, deliberately.** `publishable_files` still returns a single
explicitly-named file without applying either filter (`base.is_file()` early
return), so `publish_to_stock(slug, "state/op.json")` would copy it. That is
pre-existing, admin-only and deliberate on the caller's part, and the solution
asked to keep the function's shape; it is filed separately rather than widened
into this item.

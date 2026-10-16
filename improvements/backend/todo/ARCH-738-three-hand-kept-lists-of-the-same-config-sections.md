---
id: ARCH-738
title: Three hand-kept lists of the same config.yml sections, already disagreeing — a new section is silently dropped on the next save
summary: >-
  Three functions in config_manager.py each enumerate config.yml's sections by
  hand and already disagree: sharing is absent from _default_data,
  web_jwt_secret and admin_id from the setdefault block. A section omitted from
  _save_config's allowlist is silently discarded — fixed twice already.
category: architecture
impact: medium
effort: S
risk: low
status: todo
brain: developer
files:
  - config_manager.py:191
  - config_manager.py:230
  - config_manager.py:265
  - tests/test_config_manager_load_recovery.py
depends_on: []
commits: []
created: 2026-10-15
---

## Problem

`config.yml`'s top-level section set is written down three times in
`config_manager.py`, by hand, and nothing keeps the three in step:

| site | sections |
|---|---|
| `_load_config`'s `setdefault` block, `:191-198` | `servers`, `default_server`, `users`, `server_access`, `chat_defaults`, `user_preferences`, `telemetry`, `sharing` — **8** |
| `_default_data`, `:230-242` | the same, plus `admin_id` and `version`, **minus `sharing`** |
| `_save_config`'s `data` dict, `:265-294` | the same as the first, plus `admin_id`, `web_jwt_secret` and `version` — **11** |

They already disagree, measured at `87043ad6`: `sharing` is in two of the three
lists, and `web_jwt_secret` and `admin_id` are in `_save_config` but not in the
`setdefault` block.

`_save_config` is the dangerous one, because it is an **allowlist that
truncates**: it builds a fresh `data` dict from named keys and writes that over
the file, so a section nobody added to the list is discarded on the next save
rather than failing. That has already happened twice, and both fixes left their
own comments in the code:

- `:282-284` — *"Omitting this dropped every preference on the next save —
  including the `code_run` grant, which `_load_config` reads back and
  `routes/code.py` gates on (ARCH-177)."*
- `:289-292` — *"Same omission as ARCH-177 above, one section newer: without
  this the admin's sharing veto, each user's standing answer and the
  `share_secret` behind the stable pseudonyms were re-minted on every
  restart ([[CORR-244]])."*

Two instances, one comment apiece, and the third is already queued: [[FEAT-146]]
adds a `hub` section holding the token that authenticates a publish. A dropped
credential reads to the operator as "Condor forgot my login", with nothing in
any log to say why — the same shape as [[CORR-244]], where the identity behind
the pseudonyms was re-minted silently.

The live drift is latent rather than biting today: `sharing`'s absence from
`_default_data` is covered because `consent._update` creates the section on
first use and `_save_config` writes `{}` for it either way. The finding is that
the three lists are demonstrably not maintained together, and the failure mode
of the one that truncates is silent data loss.

## Proposed solution

Make the section set a single named constant and derive all three sites from it.
That is the move [[ARCH-670]] already made one file over — it collapsed
`condor/agents/config.py`'s two parallel load/save pairs for an *agent's*
`config.yml` into one, with the narrow pair's silent dropping of
strategy-specific keys as the stated reason. The same argument applies to the
install's `config.yml` with one more list in play.

- One module-level mapping of section name → default factory, in
  `config_manager.py`.
- `_load_config` `setdefault`s from it; `_default_data` builds from it;
  `_save_config` writes `{name: self._data.get(name, default)}` over it, keeping
  the scalar keys (`default_server`, `admin_id`, `web_jwt_secret`, `version`)
  as entries of the same mapping so there is no second list beside it.
- Keep `_save_config` a *projection* rather than `dict(self._data)`: writing
  everything would persist whatever a caller stuffed into `_data` at runtime,
  and the existing truncation is deliberate about that. The fix is that the
  projection is generated, not retyped.
- Add the guard that would have caught both historical instances: a test that
  round-trips a value in **every** declared section through a save and a fresh
  `ConfigManager` over the same file, so a section added to the mapping without
  a save path fails immediately, and one added to `_data` without being declared
  is named by the test.

## Acceptance criteria

- [ ] The three enumerations are gone: `grep` shows one declaration of the
      section set in `config_manager.py`, and `_load_config`, `_default_data`
      and `_save_config` all read from it
- [ ] `_default_data()` now includes `sharing`, and the `setdefault` path and
      `_default_data` produce the same key set for an empty config — asserted by
      comparing the two directly
- [ ] A test writes a distinctive value into every declared section, saves,
      builds a fresh `ConfigManager` over the same path, and reads every one of
      them back unchanged
- [ ] Adding a section to the declaration with no other change makes it survive
      the round trip above, demonstrated in the test by a fixture section
      declared only for that test
- [ ] A key present in `_data` but **not** declared is still not written — the
      projection property is preserved, asserted by setting
      `cm._data["junk"] = 1`, saving, reloading and finding no `junk`
- [ ] `web_jwt_secret` and `admin_id` still survive a save/reload cycle, and the
      backup copy at `self._backup_path` is still written before the truncating
      open
- [ ] An unreadable config at startup still refuses to save, the
      `_load_failed` guard unchanged
- [ ] The five existing tests in
      `tests/test_config_manager_load_recovery.py` pass unchanged: they assert
      CORR-106's "a config.yml that cannot be read must never be overwritten"
      over the very function this item rewrites
- [ ] `uv run pytest` reports no failure that is not already failing on the
      merge base: run it on the merge base and on the change in the same turn
      and compare the two lists

## Notes

- **Measured gate at `87043ad6`** (this workstream's worktree,
  `.venv/bin/python -m pytest -q`, the `make test` command): **6683 passed, 2
  skipped, 0 failed, exit 0 in 105.57 s**. Provenance only — the count moves
  with every item in this workstream, so the criterion above is the invariant.
- **Found while designing [[FEAT-146]]**, which needs a `hub` section for the
  publish token and would otherwise be the third instance. That item adds its
  own line to the list and names this doc; the two are independent and either
  can land first.
- **ARCH-177 and [[CORR-244]] are precedent, not coverage.** Each fixed one
  section and left the mechanism that produced it in place; both wrote a comment
  warning the next reader instead of making the next instance impossible. This
  item is the mechanism.
- `/improvements/` is excluded by `.gitignore:186`, and the docs tracked on this
  branch are force-added, so committing this doc needs `git add -f`.

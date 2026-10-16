---
id: FEAT-145
title: Update an installed agent from a newer bundle — diff it, replace the library, keep everything this install earned
summary: >-
  FEAT-133 refuses a slug collision and never replaces, so a newer version of an
  agent you already have means importing a second copy and migrating by hand. Add
  an update verb: show the file diff, replace library content, keep
  store/config/mutes, report locally edited files, mute what is new.
status: todo
effort: M
risk: medium
brain: developer
new_files:
  - tests/test_agent_bundle_update.py
touched_files:
  - condor/agents/bundle.py
  - condor/web/routes/agents.py
  - mcp_servers/condor/tools/trading_agent.py
  - frontend/src/components/settings/ImportAgentDialog.tsx
  - frontend/src/lib/api.ts
  - condor/agents/README.md
  - agents/condor/skills/agent_builder/SKILL.md
depends_on:
  - "[[FEAT-143]]"
commits: []
created: 2026-10-15
---

## Objective

Receiving a newer bundle of an agent this install already has is an **update**,
not a name collision. One verb shows what would change file by file, replaces
the library content, keeps everything this install accumulated by running the
agent, keeps any file the operator edited locally, mutes whatever arrives new,
and starts nothing.

## Context and constraints

**This is [[FEAT-133]]'s own declared deferral, quoted.** Its "Deliberately out
of scope" section says: *"Overwriting an existing agent on import. Import
creates; it never replaces. Updating an agent from a bundle is a different verb
with a different diff-and-confirm problem, and conflating the two is how a tuned
local agent gets silently reset."* Its collision path answers **409** through
`_create_refusal` (`condor/web/routes/agents.py:2378` at `87043ad6`; [[FEAT-133]]
cites `:2231`, which the file has since outgrown) and offers `<slug>_2`,
`<slug>_3`. So today the newer version lands beside the old one, the memory
forks, and the dashboard lists two agents one of which is stale.

**The split that makes an update safe is already the bundle's own selector.**
`publishable_files` (`condor/layering.py:322`) with `_RUNTIME_NAMES` (`:274`)
and `_root_library_dirs` (`:300`) divides a home into library content and what
this install accumulated. An update replaces exactly the first and must keep
exactly the second: `store/` (one person's learned memory, keyed by their user
id), `config.yml` (the tuned live config — including the risk limits actually in
force), `mutes.yml` (the curation the operator performed after the last import),
`sessions/`, `dry_runs/`, `learnings.md`, `state.json`, `owned_bots.json`,
`disowned_bots.json`, `proposals/`, `delegations/` and `.server_backups/`. No
new classification is needed; the one that exists is the answer.

**Dotfiles never travel, so an update can keep its own bookkeeping in the
home.** `publishable_files` skips any entry whose name starts with `.`
(`condor/layering.py:347`), at any depth, and the root-directory allowlist
catches anything below it. A `.bundle.json` and a `.bundle_backup/` in the home
are invisible to export by construction, not by being added to a denylist.

**The repo already has a preflight-then-apply vocabulary for updating
something.** `condor/updates` is headless on purpose — "nothing in here imports
Telegram or FastAPI" — and exposes `check()`, `preflight(keys)` returning
"blockers, warnings and the plan", `resolve(key, action)` and `start(keys, ...)`.
[[FEAT-133]]'s `plan(bundle, as_slug)` / `apply(plan, user_id)` is the same
shape one level down. An agent update should speak that vocabulary rather than
invent a third.

**Keeping a replaced file is an existing idiom.** FEAT-126's overwriting
controller sync keeps the server's copies under `.server_backups`, which
`_RUNTIME_NAMES` then excludes from publishing — a precedent for "an overwrite
leaves the previous bytes behind, out of the library".

**Divergence tracking exists for the stock layer and is the right shape.**
`stamp_fork` (`condor/layering.py:121`) and `carry_fork_stamp` (`:209`) record
"this local file diverged from the one it came from, and here is that one's
digest", with `content_digest` (`:53`) as the digest. An update needs the same
fact for a *bundle*-sourced file, which is what `.bundle.json` records.

**Identity has to come first.** Deciding "same agent, newer" needs
[[FEAT-143]]'s `agent_uid` and `version`: without them a slug match is the only
signal and it is wrong in both directions — two unrelated agents can pick one
name, and one agent can arrive under a different slug. [[FEAT-143]] also
establishes that a version is only meaningful within one lineage, which is why
the diff is authoritative here and the number only orders.

**An update must not run under a live trading loop.** `get_engine(slug)`
(`condor/agents/engine.py:125`, whose parameter is named `agent_id` and is the
slug) answers whether a `TickEngine` exists for the agent. Swapping a skill's
instructions under a session that is mid-tick changes what the model is told
between one tool call and the next.

## Alternatives considered

**1. Delete the home and re-import.** One code path, no diff logic. Rejected
outright: it destroys `store/`, `config.yml` and `mutes.yml` — the exact silent
reset [[FEAT-133]] refused to build, and the reason this is a separate verb at
all.

**2. Keep the status quo: import as `<slug>_2` and let the operator migrate by
hand.** Zero new code. Rejected: there is nothing to migrate *with* — the
memory is under `store/user_{id}/` in the old home and the new agent has none,
the operator must re-tune `config.yml` and re-apply every mute, and until they
finish the dashboard lists two agents with the same name and different content.

**3. Three-way merge per file (bundle vs local vs the copy at last import).**
Rejected as over-engineered and actively unsafe for this content. These are
markdown instructions a model reads and YAML a trader tuned; a conflict-marked
`SKILL.md` is not a conflict to resolve later, it is a *new instruction* the
model will follow. Conflicts are reported per file with a choice, never merged.

**4. Make the home a git repo and pull.** Rejected for the reasons
[[FEAT-133]] already rejected git as the transport — a remote and credentials
per agent — plus `.condor/` being gitignored wholesale (`.gitignore:195`), which
is the wound the bundle exists to route around.

## Decision

**A second verb beside [[FEAT-133]]'s, in `condor/agents/bundle.py`**:
`plan_update(bundle, slug)` → an `UpdatePlan` that writes nothing, and
`apply_update(plan, user_id)` → the writes it described. It speaks
`condor/updates`' vocabulary (blockers, warnings, a plan) and reuses
[[FEAT-133]]'s one-builder rule: the dialog and the write call the same
function, so pressing Update does exactly what the diff showed.

Library files are replaced. Runtime output is untouched. A file the operator
edited is a **conflict** and is kept. New routines and skills are muted;
existing mutes are never changed. Nothing is started.

## Design

### The import stamp

`.bundle.json` at the root of the agent home, written by import and by every
update:

```json
{
  "agent_uid": "…",
  "version": 3,
  "exported_at": "2026-10-15T12:00:00Z",
  "digests": {"AGENT.md": "sha256:…", "skills/s/SKILL.md": "sha256:…"}
}
```

It is the record of *what the bundle said last time*, which is the only way to
tell an operator's local edit from an upstream change. A dotfile, so it never
travels ([[FEAT-133]]'s export cannot see it).

### `plan_update`

Blockers, each refusing the update and naming the reason:

- the bundle's `agent_uid` differs from the installed agent's — two different
  agents, not an update; the answer stays [[FEAT-133]]'s 409 and a rename;
- the installed agent has no `agent_uid` and no `.bundle.json` (it was authored
  here, or imported before this landed) — the operator is offered import-as-new
  instead, because there is no basis for claiming the bundle is its upstream;
- the bundle's `version` is **lower** than the stamp's;
- `get_engine(slug)` returns an engine — a live trading loop.

Warnings: the bundle's `version` equals the stamp's but its digest differs ("a
different build of the same version" — legitimate, per [[FEAT-143]]'s
per-lineage rule, and shown with the full diff); any `unmet` requirement
([[FEAT-143]]); any finding from the review ([[FEAT-144]], if landed).

The plan, one entry per path, computed from three digests — the bundle's, the
file on disk, and the stamp's:

| disk vs stamp | bundle vs stamp | verdict |
|---|---|---|
| absent from stamp, in bundle | — | `added` |
| same | differs | `changed` — replace |
| same | same | `unchanged` |
| differs | same | `local_only` — the operator edited it, upstream did not; **keep** |
| differs | differs | `conflict` — both changed; **keep local**, offer the bundle's copy |
| in stamp, absent from bundle, unchanged on disk | — | `removed` — delete |
| in stamp, absent from bundle, changed on disk | — | `conflict_removed` — **keep** |
| absent from stamp and from bundle | — | the operator's own file — **keep**, never listed as removed |

Defaulting a conflict to *keep local* is the deliberate half: an operator's edit
is the only content in the home nobody else can reproduce.

### `apply_update`

1. Every validation [[FEAT-133]] runs on an import runs first — schema, kind,
   per-file digest, path containment through `safe_slug` — and all of it before
   the first write, which is that feature's stated rule.
2. The files about to be replaced or deleted are copied to
   `.bundle_backup/<timestamp>/` (a dotdir, so it never travels), the
   `.server_backups` idiom.
3. Writes go through `condor.fsutil.atomic_write_text`, as every store write
   does.
4. `.bundle.json` is rewritten last, so an interrupted update leaves a stamp
   describing the *previous* state and the next `plan_update` is still correct.
5. Mutes: every routine and skill whose path is `added` is muted via
   `set_muted(slug, "routines"|"skills", name, True)`
   (`condor/memory/mutes.py:111`). A name already in `mutes.yml` is left exactly
   as it is — an item the operator unmuted after reading it stays unmuted, and a
   `changed` item is **not** re-muted. That asymmetry is a decision: re-muting
   on every update would make updating an agent cost the whole curation pass
   again, and the update diff already told the operator which instructions
   changed.
6. Nothing is started. No `control_agent(start)`, no delegation, no tick. The
   result names the next step.

### Surfaces

`POST /api/v1/agents/{slug}/update/preview` and
`POST /api/v1/agents/{slug}/update`, beside [[FEAT-133]]'s import pair;
`manage_agents(action="update_agent", agent_slug=…, path=…)`; and in the
dialog, the collision step gains "this is a newer version of the agent you have
— review the changes" when the `agent_uid` matches, instead of only offering a
rename.

## Acceptance criteria

- [ ] Importing a bundle writes `.bundle.json` with the bundle's `agent_uid`,
      `version` and a digest per carried file, and `publishable_files` on that
      home does **not** list `.bundle.json`
- [ ] `plan_update` on a bundle whose `agent_uid` differs from the installed
      agent's returns a blocker naming both, and `apply_update` on that plan
      writes nothing
- [ ] `plan_update` against an agent with no `agent_uid` and no `.bundle.json`
      blocks with the import-as-new suggestion, and the agent's AGENT.md is
      byte-identical afterwards
- [ ] `plan_update` with a bundle `version` lower than the stamp's blocks; with
      an equal version and a different digest it warns and still produces a full
      diff
- [ ] `plan_update` while `get_engine(slug)` returns an engine blocks naming the
      live loop; with no engine it does not
- [ ] A file present in the bundle and absent from the stamp is `added`; one
      whose disk digest matches the stamp and whose bundle digest differs is
      `changed`; one where all three agree is `unchanged` — asserted on one
      fixture carrying all three
- [ ] A file the operator edited after import, unchanged upstream, is
      `local_only` and `apply_update` leaves its bytes untouched
- [ ] A file the operator edited after import **and** changed upstream is
      `conflict`, `apply_update` keeps the local bytes, and the plan carries the
      bundle's copy so the surface can offer it
- [ ] A file in the stamp, absent from the new bundle and unchanged on disk is
      `removed` and is deleted; the same file edited locally is
      `conflict_removed` and survives
- [ ] A file the operator created that the bundle never carried is never listed
      as `removed` and survives the update
- [ ] After `apply_update`, every path in `_RUNTIME_NAMES` present before the
      update is byte-identical: asserted on a home carrying `store/user_7/x`,
      `config.yml` with a non-default `risk_limits`, `mutes.yml`, `sessions/`,
      `learnings.md` and `state.json`
- [ ] After `apply_update`, `mutes.yml` lists every newly `added` routine and
      skill as muted; a routine the operator had unmuted before the update is
      still unmuted; a `changed` routine's mute state is unchanged
- [ ] `apply_update` leaves the replaced and deleted files under
      `.bundle_backup/<timestamp>/`, and `publishable_files` does not list
      anything under it
- [ ] An `apply_update` interrupted after some writes (simulated by making one
      write raise) leaves `.bundle.json` describing the pre-update state, so a
      second `plan_update` still classifies every file correctly
- [ ] `apply_update` starts nothing: no `TickEngine` exists for the slug
      afterwards and no delegation is recorded, on a bundle whose loop
      `default_config` sets a frequency
- [ ] The preview endpoint and the apply endpoint report the same plan — same
      per-path verdicts, same blockers, same warnings — compared field by field
- [ ] `uv run pytest` reports no failure that is not already failing on the
      merge base: run it on the merge base and on the change in the same turn
      and compare the two lists
- [ ] `cd frontend && npm test` and `cd frontend && npm run build` report no
      failure absent on the merge base, compared the same way

## Risks and notes

- **Measured gate at `87043ad6`** (this workstream's worktree): `make test` =
  `uv run pytest` → **6683 passed, 2 skipped, 0 failed, exit 0 in 105.57 s**;
  `cd frontend && npm test` (`vitest run`) → **220 files, 2286 tests, 0 failed,
  exit 0 in 12.18 s**. Provenance only; both counts move with every item in this
  workstream, so the criteria above are the invariant. Re-measure:
  [[FEAT-139]] landed immediately afterwards and added `tests/test_preflight.py`,
  so the pytest count has already changed.
- **`agent_id` in this codebase means the slug** — 446 occurrences across
  `condor/`, `mcp_servers/` and `handlers/`, including `get_engine(agent_id)`
  (`condor/agents/engine.py:125`). [[FEAT-143]]'s new identifier is therefore
  `agent_uid`, and this item must not reintroduce the collision by abbreviating
  it back.
- **Not re-muting a `changed` item is the one judgement here that could go the
  other way.** An upstream author who edits a skill the operator already read
  and unmuted gets their new instructions followed without a second review. The
  compensating control is that the diff names exactly those files and the update
  is an explicit act; the alternative — re-mute everything that changed — costs
  the whole curation pass on every update and would make operators stop
  updating. If it proves wrong the fix is additive: re-mute a `changed` item
  only when [[FEAT-144]] reports a finding on it.
- **Updating does not re-run a preflight.** Whether the agent can still trade
  here after the update — capital, account mode, venue rules — is [[FEAT-139]],
  now closed (`22952d57`), so the thing to point at is
  `condor/preflight.py`'s `PreflightReport`. An update is a good moment to
  invite it; do not reimplement any of it.
- **An update of a `stock` agent is out of scope.** `publish_to_stock`
  (`condor/layering.py:357`) is the one sanctioned write into the tracked
  library and is admin-only; an imported agent lives in the local layer, and an
  update writes there too. A bundle naming a stock-only slug is a blocker for
  the same reason import refuses one.
- `/features/` is excluded by `.gitignore:187` and the docs tracked on this
  branch are force-added, so committing this doc needs `git add -f`.

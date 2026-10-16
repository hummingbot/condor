---
id: FEAT-143
title: A bundle says which agent it is, who made it, which version, and what it needs
summary: >-
  FEAT-133's bundle identifies an agent only by a slug the importer may rename,
  and carries no author, version or requirements. Schema 2 adds a stable
  agent_uid, an integer version with notes, an author claim, and a requires block
  derived from the files, so the preview says what will not work here.
status: todo
effort: L
risk: medium
brain: developer
new_files: []
touched_files:
  - condor/agents/bundle.py
  - condor/agents/agent.py
  - condor/web/routes/agents.py
  - mcp_servers/condor/tools/trading_agent.py
  - frontend/src/components/settings/ImportAgentDialog.tsx
  - frontend/src/lib/api.ts
  - tests/test_agent_bundle.py
  - condor/agents/README.md
  - agents/condor/skills/agent_builder/SKILL.md
depends_on:
  - "[[FEAT-133]]"
commits: []
created: 2026-10-15
---

## Objective

A bundle that travels between strangers carries enough manifest to be
identified, compared against a copy the receiver already has, attributed to
somebody, and checked against the install receiving it — before any hub exists,
and whether or not it ever came from one.

Done means: two homes that came from one bundle can be recognised as the same
agent even after a rename; a second bundle of that agent can be ordered against
the first; the preview names the author as an unverified claim rather than
implying provenance; and the preview lists every connector, tool, model and
skill the agent needs that this install cannot supply, without refusing the
import.

## Context and constraints

**Identity today is the slug, and the importer is allowed to change it.** The
`Agent` record (`condor/agents/agent.py:83`) holds `slug`, `name`,
`description`, `instructions`, `agent_key`, `tools` (`:92`), `when_to_consult`,
`server_required`, `server_name` (`:100`), `created_by` (`:101`) and
`created_at` (`:102`) — and no id, no version and no author. [[FEAT-133]]'s
manifest carries `schema`, `kind`, `exported_at`, the agent's slug/name/
description/when_to_consult, and `files[]`; its collision path offers `as_slug`,
so one rename on import leaves nothing that says two homes are the same agent.

**There is no Condor version to require, and inventing one would be a lie.**
`condor.telemetry.context.version()` is `git rev-parse --short HEAD`
(`condor/telemetry/context.py:82`), and `pyproject.toml:3` has said
`version = "0.1.0"` since the repo began. A `requires: {condor_min_version}`
field has nothing to compare against. Requirements therefore have to be *named
capabilities* a receiving install can resolve, not a version range.

**The capabilities an agent needs are already written in its files.** Every
loop's `default_config.connector_name`
(`agents/adaptive_grid_trader/loops/btc_usdt_adaptive_grid/loop.md:8` →
`bitget_perpetual`), every controller `sample_configs/*.yml`'s
`connector_name`, `AGENT.md`'s `tools:` allowlist (empty meaning unrestricted,
`condor/agents/agent.py:91-92`), its `agent_key`, and each loop's `skills:`
list. So the manifest can *derive* the requirement set instead of asking an
author to maintain one — the same move `publishable_files`
(`condor/layering.py:322`) already makes for "what is this agent, as opposed to
what this install accumulated".

**Part of the requirement set is now *declared*, with one reader.**
[[FEAT-139]] landed at `22952d57` while this was being written and brought
`AgentConfig.trading_requirements` (`TradingRequirementsConfig`,
`condor/agents/config.py:153`, the field at `:272`) and
`condor/preflight.py`'s `Requirements` (`:165`) and
`requirements_of(config)` (`:204`): a loop now states its connector, trading
pairs, account, capital asset and amount, position mode and leverage, and
`Requirements.declared` answers whether it stated anything at all. So the
connector a loop needs has a single authoritative reader, and this item must
use it rather than parse `trading_requirements` a second time.

**The receiving install can answer each one.**
`fetch_available_cex_connectors` (`condor/fetchers/connectors.py:64`) returns
the connectors with credentials configured intersected with what the server
offers; `fetch_connectors` (`:30`) returns the server's whole list. Capital in
the exact asset, account position mode and venue rules are **not** this item's
job — that is [[FEAT-139]], and an imported agent that cannot trade here yet is
exactly the case that doc exists for.

**"Stable id plus monotonic revision" is already solved in this repo.**
`condor/sharing/wire.py` keeps `share_id` stable per conversation and bumps
`revision` (`:178`) on re-send, with the reason stated in its docstring: the
events pipeline's `ON CONFLICT DO NOTHING` "will not do here, because a
transcript legitimately changes". An agent is the same shape.

**Stamping where content came from is an existing idiom.** A skill's front
matter already carries `source: agent:adaptive_grid_trader`
(`agents/adaptive_grid_trader/skills/liquidation_guard/SKILL.md`).

**An author field cannot be the Telegram id.** [[FEAT-133]] scrubs
`created_by` to `0` on export precisely because a real Telegram id "points to
nobody" on a foreign install ([[CORR-705]]), and drops `forked_from`/
`forked_at` because they describe this install's stock tree
(`condor/layering.py:48-49`). So an author is a claim the exporter writes, held
in a field whose unverified status is stated at the surface.

## Alternatives considered

**1. Semantic version plus a CHANGELOG.md in the home.** Familiar, and
human-readable. Rejected: semver is a promise about a compatibility contract an
agent does not have — a one-word change to a skill's instructions can change
behaviour more than a renamed controller — so the three numbers would be
decoration nobody maintains. `wire.py`'s integer `revision` is the repo's own
cheaper answer to the same question, and free-text `notes` carries whatever a
changelog would have said without pretending to a contract.

**2. The content digest is the identity.** Addressing for free, integrity for
free. Rejected: it makes every edit a different agent, which is exactly what an
upgrade path must not do, and [[FEAT-133]] already carries a per-file `sha256`
for integrity. A digest answers "is this the bytes I expected", not "is this the
same agent".

**3. Public-key signatures binding an author to a bundle.** Rejected as the
identity mechanism. Condor has no key infrastructure: its only secrets,
`install_secret` and `share_secret` (`condor/sharing/consent.py:266-277`),
exist to *prove* something locally and never leave the box. A signature would
need issuance, distribution and revocation, and it answers "who built this"
while the dangerous payload here is free-text instructions a verified author can
ship as easily as anyone. Integrity between a hub and a client is
[[FEAT-146]]'s digest check; what a static review can actually say about the
contents is [[FEAT-144]].

**4. An author-declared `requires:` block in AGENT.md.** One place to read.
Rejected **as the whole answer**, not as the idea: a hand-written block covering
everything drifts from the files the moment a loop's connector changes and
nothing notices. But declaration is exactly what [[FEAT-139]] chose for trading
requirements, deliberately — its module docstring says the checks run "against
what the loop *states* it needs … rather than against one order" — so the right
split is to **read what is declared and derive what is not**, with no field
parsed twice.

## Decision

**Schema 2 of the [[FEAT-133]] bundle**, adding four manifest fields in one
pass — `agent_uid`, `version` (+`notes`), `author`, `requires` — because they
are one manifest and one schema integer. Splitting them would mean two schema
bumps for one shape.

`requires` is **derived at export from the content**, never authored.
`author` is a **claim**, labelled as such at every surface. `version` is
**advisory**: it orders two bundles of one `agent_uid`, and the authoritative
answer for an upgrade is the file diff ([[FEAT-145]]).

## Design

### `agent_uid`, and why not `agent_id`

**The name matters and `agent_id` is taken.** In this codebase `agent_id`
already means *the slug*: `get_engine(agent_id)`
(`condor/agents/engine.py:125`) looks a running engine up by slug, and
`agent_id` appears **446 times** across `condor/`, `mcp_servers/` and
`handlers/` with that meaning. Adding a second, stable, slug-independent
identifier under the same name would make every one of those sites ambiguous.
The new field is therefore `agent_uid`, and the contrast is the point: `slug` is
the name, `agent_id` is the slug by another name, `agent_uid` is the identity.

`uuid4().hex`, written into AGENT.md front matter. Minted by `AgentStore.create`
(`condor/agents/agent.py:328`) for a new agent, and minted **at export** for an
agent that has none — which is every agent on disk today. Never minted on a read
path: a read that writes AGENT.md would put a disk write behind
`AgentStore.get`, which the module's own contract ("the filesystem is read on
every call… no cache and therefore no invalidation bug") does not expect.

It **travels** and is **not scrubbed**, unlike the three install-local fields.
It does not change when the importer supplies `as_slug`. Two installs that
imported one bundle therefore hold the same `agent_uid`, which is the point: it is
what makes "you already have this agent" answerable.

### `version` and `notes`

`version` is an integer starting at 1. Export bumps it only when the agent's
content has changed since the last export, so re-exporting an unchanged agent
returns the same number instead of inflating it. The comparison is a digest over
`publishable_files(agent_home(slug))` in sorted order, using
`content_digest` (`condor/layering.py:53`); the last exported digest and version
are stamped in AGENT.md as `bundle_version` and `bundle_digest`.

`notes` is free text the exporting caller supplies, carried verbatim. It is the
changelog, and it is optional.

A version is only meaningful *within one lineage*: two people who both import
one bundle, edit locally and re-export each produce version 2 with different
content. That is stated in the manifest's own docstring rather than papered
over, and it is why [[FEAT-145]] diffs files rather than trusting the number.

### `author`

`{"name": "", "handle": ""}`. `name` is free text the exporting caller supplies.
`handle` is reserved for the account a hub attaches at publish time
([[FEAT-146]]) and is empty on a file exported locally. Neither is verified by
anything in Condor, and the preview labels the block "author (unverified
claim)". The `share_install_id` is deliberately **not** reused here: it is the
sharing feature's correlator and carrying it into a second, differently-scoped
artifact would link two unrelated things about one install.

### `requires`, derived

```json
"requires": {
  "connectors": ["bitget_perpetual"],
  "tools": ["create_grid_executor", "manage_controllers"],
  "agent_key": "claude-code",
  "skills": ["liquidation_guard"]
}
```

- `connectors`: for each loop in the bundle, `requirements_of(default_config)`
  — [[FEAT-139]]'s one reader of a stated requirement — and where
  `Requirements.declared` is false, the loop's own `default_config.connector_name`;
  union the `connector_name` of every `controllers/*/sample_configs/*.yml`.
  `trading_requirements` is never parsed here: a second parse of a field that
  already has a reader is the two-selectors-disagreeing hazard [[FEAT-133]]
  names when it refuses to re-implement `publishable_files`.
- `tools`: `AGENT.md`'s `tools:` list. An empty list means unrestricted and is
  recorded as `[]`, not as "every tool".
- `agent_key`: the model the agent asks for — the field [[FEAT-133]] already
  lets travel with an `agent_key_inherited` fallback.
- `skills`: the skill slugs the agent's loops name, split into those carried in
  the bundle and those it expects to inherit from `agents/_shared/skills`
  (which [[FEAT-133]] establishes are structurally absent from a bundle).

### Resolution: `unmet`, and why nothing is refused

`plan(bundle, as_slug)` grows an `unmet` list. Each entry names the
requirement, what this install has instead, and a `blocking` flag. The
resolution function takes the install's connector list and tool names **as
arguments** so `bundle.py` stays headless — the fetchers are async and need a
client, and the module's rule is no FastAPI and no Telegram import.

An unmet requirement never refuses the import. The honest case — "I will add
that exchange's API key after I have read the agent" — must stay possible, and
everything imported is muted and unstarted anyway. The preview says what will
not work; the operator decides.

### Reading a schema-1 bundle

[[FEAT-133]]'s rule holds upward: an unknown schema is refused whole. Downward
it does not: a schema-1 bundle is accepted by a schema-2 reader, with
`agent_uid` minted on import, `version` 1, `author` empty and `requires` derived
from the imported files. That asymmetry is deliberate and narrow — schema-1
bundles exist from the day [[FEAT-133]] lands, and refusing them would strand
them for no safety gain.

## Implementation plan

1. `condor/agents/agent.py` — `agent_uid`, `bundle_version`, `bundle_digest` on
   the record and its front-matter round trip; mint `agent_uid` in `create`.
2. `condor/agents/bundle.py` — `SCHEMA = 2`; the four manifest fields; the
   `requires` derivation; the version-bump rule; `unmet` on `ImportPlan`; mint
   `agent_uid` at export when absent.
3. `condor/web/routes/agents.py` — the bundle, preview and apply responses
   carry the new fields and `unmet`; the route supplies the install's connector
   and tool sets to the headless resolver.
4. `mcp_servers/condor/tools/trading_agent.py` — export accepts `notes` and
   `author_name`; import reports `unmet`.
5. Frontend — `ImportAgentDialog.tsx` renders identity, version + notes, the
   author block labelled unverified, and the unmet list; `api.ts` types.
6. `tests/test_agent_bundle.py` — the criteria below.
7. Docs — the new fields in `condor/agents/README.md` and the export/import rows
   in `agents/condor/skills/agent_builder/SKILL.md`.

## Acceptance criteria

- [ ] An agent created through `AgentStore.create` has a 32-hex `agent_uid` in
      its AGENT.md front matter, and two agents created in the same test have
      different ones
- [ ] Exporting an agent whose AGENT.md has no `agent_uid` writes one into the
      bundle **and** into the agent's AGENT.md on disk; exporting it a second
      time returns the same `agent_uid`
- [ ] `AgentStore().get(slug)` on an agent with no `agent_uid` writes nothing:
      the AGENT.md mtime and bytes are identical before and after, asserted on a
      home that is read ten times
- [ ] Importing a bundle under `as_slug` produces a home whose AGENT.md
      `agent_uid` equals the bundle's, so the two homes agree on identity while
      disagreeing on slug
- [ ] A first export of an agent carries `version: 1`; a second export with no
      file changed carries `version: 1` again; a third export after one byte
      changes in `skills/s/SKILL.md` carries `version: 2`
- [ ] The version bump is driven by `publishable_files` content only: adding
      `store/user_7/x`, `config.yml` and `routines/__pycache__/r.pyc` to the
      home between two exports leaves the version unchanged
- [ ] `notes` passed at export appears verbatim in the manifest and in the
      preview response, and an export with no `notes` carries an empty string
      rather than a missing key
- [ ] `author.name` passed at export appears in the manifest; `author.handle`
      is empty for a locally exported bundle; neither field is read by any code
      path that decides whether to write (asserted by a grep-style test or by
      importing a bundle whose author is a forged handle and observing the same
      plan)
- [ ] `requires.connectors` for a bundle with two loops naming
      `bitget_perpetual` and `binance_perpetual` and one controller sample
      config naming `hyperliquid_perpetual` is exactly those three, sorted and
      deduplicated
- [ ] A loop declaring `trading_requirements.connector` contributes that value,
      read through `requirements_of`, and a loop declaring nothing
      (`Requirements.declared` false) contributes its `default_config.connector_name`
      — asserted on one bundle carrying both kinds of loop
- [ ] No code path in `bundle.py` reads the `trading_requirements` key directly:
      asserted by a grep-style test over the module, so the field keeps its one
      reader
- [ ] `requires.tools` equals the AGENT.md `tools:` list, and is `[]` (not the
      full discovered set) for an agent with an empty allowlist
- [ ] `requires.skills` splits the loops' skill names into the ones carried in
      `files[]` and the ones expected from `agents/_shared/skills`, asserted on
      a fixture agent that names one of each
- [ ] Previewing a bundle requiring `bitget_perpetual` against an install whose
      resolved connector set is `{"binance"}` returns one `unmet` entry naming
      the connector and what the install has, and `apply` on the same bundle
      still writes the agent
- [ ] Previewing the same bundle against an install that has
      `bitget_perpetual` returns an empty `unmet`
- [ ] A bundle with `schema: 1` imports successfully under a schema-2 reader,
      gets a freshly minted `agent_uid`, `version: 1`, empty `author`, and a
      `requires` block derived from its own files
- [ ] A bundle with `schema: 3` is refused whole, the message names both `3` and
      `2`, and `agent_home(slug)` does not exist afterwards
- [ ] The preview and the apply responses report the same `agent_uid`,
      `version`, `author` and `unmet` for one bundle, compared field by field —
      [[FEAT-133]]'s one-builder rule extended to the new fields
- [ ] `uv run pytest` reports no failure that is not already failing on the
      merge base: run it on the merge base and on the change in the same turn
      and compare the two lists
- [ ] `cd frontend && npm test` and `cd frontend && npm run build` report no
      failure absent on the merge base, compared the same way

## Risks and notes

- **Measured gate at `87043ad6`** (this workstream's worktree,
  `.venv/bin/python -m pytest -q`, the `make test` command): **6683 passed, 2
  skipped, 0 failed, exit 0 in 105.57 s**. Quoted as provenance only — the
  count moves with every item in this workstream, so the criterion above is the
  invariant, not the number. Re-measure; never treat 6683 as expected —
  [[FEAT-139]] landed immediately afterwards and added `tests/test_preflight.py`,
  so the count has already moved. The
  frontend gate at the same sha, `cd frontend && npm test` (`vitest run`):
  **220 files, 2286 tests, 0 failed, exit 0 in 12.18 s**.
- **If this ships in the same batch as [[FEAT-133]], collapse it into schema 1**
  rather than shipping a version number nothing ever emitted. Schema 2 exists
  because schema-1 bundles may already be in the wild by then; if they are not,
  the honest manifest is one schema with all the fields. Decide by asking
  whether `condor/agents/bundle.py` has landed on `main`.
- **A version is per-lineage, not global.** Two installs that import one bundle
  and both edit locally both produce `version: 2`. `agent_uid` says "same agent";
  it does not establish who is upstream. [[FEAT-145]] therefore shows the file
  diff and treats the number as a hint.
- **`author` is unverified and must read as unverified.** The one place this
  becomes dangerous is a UI that renders a handle next to a download without
  saying so; the hub's badge ([[FEAT-146]]) is the only attested provenance, and
  it attests a review, not an identity.
- **An unmet requirement is not a trading preflight.** This item answers "does
  this install have that connector at all"; whether capital sits in the right
  asset, in the right account mode, under venue rules that make the strategy
  viable, is [[FEAT-139]] — now **closed** (`22952d57`,
  `features/done/`), so the hook is concrete code: `condor/preflight.py`'s
  `PreflightReport` and its `doctor --agent` section. Do not re-implement any of
  its checks here; the natural wiring is for the import result to point at it.
- `/features/` is excluded by `.gitignore:187`, and the 28 docs tracked on this
  branch are force-added, so committing this doc needs `git add -f`.

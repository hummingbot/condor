---
id: FEAT-133
title: An agent travels as one file — export a bundle, import it, review before anything runs
summary: >-
  Moving an agent today means hand-copying agents/<slug>/ and repairing what
  was install-specific. Give it one portable JSON bundle: export carries
  library content only and scrubs this install's facts; import previews it,
  refuses a slug collision, and lands routines and skills muted.
status: done
effort: L
risk: medium
new_files:
  - condor/agents/bundle.py
  - tests/test_agent_bundle.py
  - frontend/src/components/settings/AgentsSettings.tsx
  - frontend/src/components/settings/ImportAgentDialog.tsx
  - frontend/src/components/settings/ImportAgentDialog.test.tsx
touched_files:
  - condor/web/routes/agents.py
  - mcp_servers/condor/server.py
  - tests/test_agents_routes_order.py
  - mcp_servers/condor/tools/trading_agent.py
  - frontend/src/lib/api.ts
  - frontend/src/pages/Settings.tsx
  - frontend/src/components/agent/workspace/WorkspaceHeader.tsx
  - agents/condor/skills/agent_builder/SKILL.md
  - condor/agents/README.md
depends_on:
  - CORR-720
commits:
  - "62f3e2ea feat(agents): an agent travels as one file — export a bundle, import it, review before anything runs (FEAT-133)"
created: 2026-10-07
closed: 2026-10-15
---

## Objective

An operator can hand one of their agents to another machine or another person as
**one file**, and the receiving install can take it without copying a directory
or repairing anything install-specific. The user's words: *"if i want to import
an agent i need like to copy the files and everything, should be simpler."*

Done means: a single artifact per agent; an export action in the dashboard and
on `manage_agents`; an import that validates, previews, resolves a slug
collision and registers the agent so it is listed and loadable; a manifest
schema version that makes a newer bundle fail loudly; and nothing imported
running until the operator switches it on.

## Context and constraints

**An agent is a folder in two layers.** FEAT-115 split `agents/` into
`stock_agents_root()` — `<repo>/agents`, tracked, never written at runtime — and
`local_agents_root()` — `<repo>/.condor/agents`, which git has never heard of
(`condor/paths.py:201`, `:219`). Reads are layered per item, local shadowing
stock (`condor/memory/paths.py:124` `resolve_agent_file`); writes are always
local. An import is therefore a write into the local layer, and **never** into
stock: the one sanctioned write there is `publish_to_stock`, which is admin-only
and exists so the maintainer can author the library.

**The agent that most needs to travel cannot travel today.** `.condor/` is
gitignored wholesale (`.gitignore:195`), so every agent this install authored —
on this box, `adaptive_orca_lp` and `regime_switch_lp` — is disk-only. The older
`.git/info/exclude` entries for `agents/brigado/` and `agents/backpack_mm/`
([[agents-dir-locally-excluded]]) are the same wound at the other root. Git is
not the answer for either.

**The content selector already exists.** `condor/layering.py:277`
`publishable_files` answers "what is this agent, as opposed to what this install
accumulated by running it": it walks the home and skips `store/`, `sessions/`,
`dry_runs/`, `learnings.md`, `config.yml`, `state.json`, `mutes.yml`,
`proposals/`, `delegations/`, `owned_bots.json`, `disowned_bots.json` and
`.server_backups/`. It is a bundle manifest in everything but name — and it has
two holes ([[CORR-720]]: a root `state/` folder and `__pycache__`), which is why
this feature depends on that item rather than re-implementing the selection.

**Inherited skills exclude themselves.** The shared library is a *directory*,
`agents/_shared/skills`, not a frontmatter flag ([[skills-scoping]];
`condor/memory/paths.py:258`). It is a **sibling** of the agent home, not a
child, so `publishable_files(agent_home(slug))` cannot reach it. "Do not bundle
the inherited shared skills" needs no filter — it is structural.

**Nothing in the repo reads or writes an archive.** `grep -rl
'zipfile\|tarfile\|make_archive'` over the tree returns nothing. Whatever format
is chosen here is a new idiom, so it should be the smallest one that works.

**Three fields are install-local and must not travel.** `server_name` names a
server in *this* install's `config.yml`, and three separate guards refuse an
unreachable pin (`_gate_pin_change` at `condor/web/routes/agents.py:949`,
`_refuse_unreachable_pin` at `mcp_servers/condor/tools/trading_agent.py:223`,
SEC-693) — a bundle carrying it would arrive pinned to an account the importer
cannot reach and then refuse its own first edit. `created_by` is a real Telegram
id; [[CORR-705]] established that a non-zero one on a foreign install points to
nobody while the admin-principal rule still acts on it, and its notes name
"copied or imported folders" as the remaining source. `forked_from`/`forked_at`
describe a divergence from *this* install's stock tree and mean nothing on
another (`clear_fork_stamp`, `condor/layering.py:228`).

**Imported code runs in-process, unsandboxed.** A routine is imported by
`routines/base.py:554` `discover_routines_from_path` and executed inside the
Condor process; `run_code` has no sandbox either. There is no sandbox to put an
imported routine in, and this feature does not build one.

**Discovery is uncached, so import is hot.** `AgentStore.get` resolves from disk
on every call, `condor/agent_controllers.py` states "the filesystem is read on
every call… no cache and therefore no invalidation bug", `SkillStore` resolves
per read, and `discover_routines_from_path` keys its mtime cache per directory —
so a directory that did not exist is scanned on its next call. A freshly
imported agent is therefore listed and loadable with no restart. The one restart
is **deploying** this feature: `main.py`'s `modules_to_reload` covers only
`handlers.*`, so new endpoints in `condor/web/routes/agents.py` need the process
restarted once ([[web-backend-no-hot-reload]]).

## Alternatives considered

**1. A zip or tar archive.** Standard, compact, handles binary. Rejected on two
counts. It is opaque — you cannot read it to decide whether to trust the
executable code inside, and review-before-run is the central safety requirement
here. And it imports a new attack surface the repo has never had to defend:
zip-slip, symlink entries, compression bombs, and member-count limits, all of
which have to be got right on the first try for a file that arrived from
somebody else.

**2. Git: export pushes a branch or a repo, import clones a ref.** Versioning
and addressing for free. Rejected: it needs the sender to have a remote and the
receiver credentials for it, it needs a repo per agent because `.condor/` is
gitignored inside the Condor checkout, and it is precisely what
`_publish_agent`'s docstring calls "a distribution system nobody asked for"
(`mcp_servers/condor/tools/trading_agent.py:191`). The user asked to move a
folder, not to run infrastructure.

**3. Extend `publish_to_stock` and let the other side `git pull`.** Zero new
code. Rejected: it only reaches people on the same fork with commit access, and
it is the status quo that already fails for every locally-authored agent. It
also inverts the trust direction — publishing says "this belongs in the library
everyone gets", which is not what handing one agent to one person means.

**4. One self-describing text file: a JSON manifest with every library file
inlined.** No new dependency, no archive parser, reviewable, pasteable, and
sendable as a Telegram document (`_HttpBot` already supports `send_document`).
Its weakness is that arbitrary file content has to be framed without a delimiter
clash — which JSON string encoding solves outright, and which a markdown
envelope with fences would not (a controller `.py` containing a fence or a `---`
line breaks it). Its other weakness is that raw JSON is not pleasant to read by
eye; that is answered by rendering the review in the UI rather than by choosing
a prettier wire format.

A variant of 4 — markdown envelope with frontmatter, to match the repo's
`AGENT.md`/`SKILL.md` idiom — was dropped for exactly the delimiter reason. The
idiom is for *authored* files a human edits; a bundle is a wire format, and the
repo already has one of those in `condor/sharing/wire.py`.

## Decision

**Alternative 4.** One JSON file, `<slug>.condor-agent.json`, built by a single
builder that both the preview and the write go through — the rule
`condor/sharing/share.py:83` already sets for shares ("the acceptance criterion
is that pressing Share sends exactly the bytes the dialog displayed, and the
only way to promise that is for the dialog and the sender to call one
function").

**Deliberately out of scope**, and recorded here so it is a decision rather than
an omission:

- **Import from a URL or a git ref.** The Condor process can reach `localhost`
  and cloud metadata endpoints, so a server-side fetch of a user-supplied URL is
  an SSRF surface that needs its own design. The browser can already fetch a
  link and hand the file to the upload endpoint, which covers the sharing-a-link
  case without putting the fetch in the server.
- **Seed memory in the bundle.** `store/user_{id}/` is one person's learned
  memory, keyed by their user id, and it is the first name in
  `_RUNTIME_NAMES`. Carrying it would move one person's notes onto another
  person's install under a third user id. An agent's durable knowledge belongs
  in `AGENT.md` and `skills/`, both of which travel.
- **Overwriting an existing agent on import.** Import creates; it never
  replaces. Updating an agent from a bundle is a different verb with a different
  diff-and-confirm problem, and conflating the two is how a tuned local agent
  gets silently reset.

## Design

### The bundle

```json
{
  "schema": 1,
  "kind": "condor-agent-bundle",
  "exported_at": "2026-10-07T09:12:03Z",
  "agent": {
    "slug": "regime_switch_lp",
    "name": "Regime Switch LP",
    "description": "…",
    "when_to_consult": "…"
  },
  "files": [
    { "path": "AGENT.md", "sha256": "…", "text": "---\nname: …" },
    { "path": "controllers/orca/sample_configs/sol_usdc.yml", "sha256": "…", "text": "…" }
  ]
}
```

- `schema` is an **integer**, and an unknown one is refused **whole** — the
  precedent and the wording are `condor/sharing/wire.py:39`: "a client ahead of
  its server fails loudly rather than quietly dropping shares." A bundle from a
  newer Condor answers "this bundle was made by a newer Condor (schema N); this
  install reads up to 1" and writes nothing.
- `kind` must match exactly, so an unrelated JSON file is refused with a
  sentence instead of a `KeyError`.
- `files[].path` comes from `publishable_files(agent_home(slug))` — relative to
  the home, already filtered of runtime output.
- `files[].sha256` is per file; the importer recomputes and refuses on mismatch,
  so a hand-edited bundle is caught. Reuse `condor.layering.content_digest`'s
  `sha256:<12>` convention or the full digest — the module picks one and says
  which.
- **Text only.** A file whose bytes are not valid UTF-8 is refused **at export**,
  naming the file. Nothing in a library agent is legitimately binary (`.md`,
  `.py`, `.yml`), and refusing loudly keeps the bundle reviewable and the
  importer free of a base64 branch.
- `MAX_BUNDLE_BYTES`, mirroring `wire.py:48`'s named cap, enforced on export and
  again on import so the HTTP boundary is not the only limit.

### Export scrubs three fields

Before serialization, the bundle's copy of each `.md` front matter is rewritten
(the file on disk is untouched):

| field | becomes | why |
|---|---|---|
| `server_name` | `''` | names a server in this install's `config.yml`; a carried pin is refused by the three pin guards on the first edit |
| `created_by` | `0` | a Telegram id that points to nobody on the receiving install ([[CORR-705]]) |
| `forked_from`, `forked_at` | dropped | a divergence from *this* install's stock tree |

`agent_key` **travels**. The importer keeps it if the target has that model
configured and otherwise falls back to `_creator_agent_key()` — exactly what
`create_agent` already does — reporting `agent_key_inherited: true`, the field
that action already returns.

### Import: preview, then apply, from one builder

`plan(bundle, as_slug)` returns everything a human needs to decide, and writes
nothing:

- the manifest (`schema`, `kind`, `exported_at`, the agent's identity);
- every file with its byte count, and **the `.py` files called out as
  executable** — routines and controllers, named individually, because a routine
  runs in the Condor process and the review *is* the only gate;
- the collision verdict for `as_slug` (or the bundle's slug);
- the fields that will be stamped (`created_by` → the importer, `server_name` →
  empty);
- the names that will arrive muted;
- any refusal, with its reason.

`apply(plan, user_id)` writes exactly what the plan described. All validation
runs before the first write; on any write error the directory this import
created is removed, so a failure never leaves half an agent. Writes use
`condor.fsutil.atomic_write_text`, as every other store write does.

### Collision

`AgentStore.create` (`condor/agents/agent.py:328`) already raises
`AlreadyExistsError` for a taken slug and resolves through **both** layers, so a
shipped agent is refused too. Import reuses it and never overwrites. The route
answers **409** through the existing `_create_refusal`
(`condor/web/routes/agents.py:2231`), naming the taken slug and the first free
`<slug>_2`, `<slug>_3`; the dialog offers a rename field. `condor` is reserved
and refused by the same path that already reserves it.

### Path containment

Every `files[].path` part goes through `condor.memory.paths.safe_slug` (`:75`),
the guard `resolve_agent_file` already applies — no absolute path, no `..`, no
`/`, `\` or NUL in a part. The write root is `agent_home(slug)`, computed from
the resolver, never joined from the bundle. A bundle naming a path outside is
refused whole, nothing written. This is the local-layer twin of
[[SEC-648]]'s rule, and the test file that covers it
(`tests/test_agents_layering.py:766`) is where the import case belongs.

### Safety: imported code arrives switched off

Every imported routine and skill is muted on arrival —
`condor.memory.mutes.set_muted(slug, "routines"|"skills", name, True)`
(`condor/memory/mutes.py:111`). This is not a new gate: FEAT-090's mute set is
the existing, reversible curation control that "genuinely removes it from the
run rather than merely hiding it from a list", it is written to a file **no
agent-facing tool opens** (so an imported agent cannot unmute itself), and the
panel that switches items back on already exists (`PUT /agents/{slug}/mutes`).
The operator unmutes item by item after reading each one.

Controllers need no mute: a controller only executes after an explicit
`manage_agent_controllers` sync to a Hummingbot server, which is already a
separate deliberate act.

**Import never starts anything** — no `control_agent(start)`, no `delegate`, no
tick. The result names the next step rather than taking it.

### Surfaces

| | where |
|---|---|
| export (HTTP) | `GET /api/v1/agents/{slug}/bundle` → `Response(content=…, media_type="application/json", headers={"Content-Disposition": …})`, bearer-guarded, mirroring `get_attachment` (`condor/web/routes/conversations.py:204`). The frontend fetches with `authFetch` and makes an object URL, because a bare `<a href>` cannot carry the bearer — the reason `useAuthedImage` exists. |
| export (chat/MCP) | `manage_agents(action="export", agent_slug=…)` writes `data/exports/<slug>-<date>.condor-agent.json` (`data/` is gitignored, `.gitignore:178`) and returns `{path, bytes, files, refusals}`. The chat offers it with `send_document`. |
| export (UI) | an item beside the existing Delete on `WorkspaceHeader`, wired from `AgentWorkspace.tsx` the way `onDelete` is. |
| import preview | `POST /api/v1/agents/import/preview` — multipart `UploadFile`, size cap, 413/415, modelled on `upload_attachment` (`conversations.py:171`). Writes nothing. |
| import apply | `POST /api/v1/agents/import` — same body plus `as_slug`; 409 on collision. |
| import (chat/MCP) | `manage_agents(action="import", path=…)`, beside `publish_agent` in `_manage_agent`. |
| import (UI) | a new **Agents** tab in Settings' "Condor" group, rendering `AgentsSettings.tsx` with `ImportAgentDialog`. The dashboard has no "New Agent" affordance today — agents are created through chat — so Import needs a home of its own rather than being hung off a button that does not exist. `ImportGatewayWallet.tsx` is the naming and shape precedent. |

### Relationship to `agent_builder`

Import is not a second ingest path. `agent_builder`'s Path B takes an
*unstructured* folder a human assembled and uses the model to map it onto the
agent layout; import takes a *structured* bundle this product emitted and needs
no model at all. They meet at the end — both finish having written under
`agent_home(slug)`. The skill gains a third row in its "Pick the path" table —
"A bundle someone exported → `manage_agents(action="import")`, then review its
muted routines before unmuting" — so the model never re-derives an import by
hand with `run_code`. That edit is part of this feature, not a follow-up.

## Implementation plan

1. **`condor/agents/bundle.py`** — headless, no Telegram and no FastAPI import,
   the rule `condor/updates/__init__.py` states for itself. `SCHEMA`, `KIND`,
   `MAX_BUNDLE_BYTES`, pydantic `Bundle`/`BundleFile`/`ImportPlan`,
   `build(slug)`, `render(bundle)`, `parse(bytes)`, `plan(bundle, as_slug)`,
   `apply(plan, user_id)`. One builder behind export and preview.
2. **`tests/test_agent_bundle.py`** — round-trip, each scrub, the schema
   refusal, the digest refusal, the non-UTF-8 refusal, the traversal refusal,
   the collision, the mutes, and that `_shared/` skills are absent.
3. **`condor/web/routes/agents.py`** — the three endpoints.
4. **`mcp_servers/condor/tools/trading_agent.py`** — `export_agent` /
   `import_agent` actions on `manage_agents`, with the action-alias entries the
   module already keeps (`:730`, `:778`).
5. **Frontend** — `api.ts` helpers, `AgentsSettings.tsx` +
   `ImportAgentDialog.tsx` (+ test), the Settings tab, the Export item on
   `WorkspaceHeader`.
6. **Docs** — the third path row in `agent_builder/SKILL.md`; a short section in
   `condor/agents/README.md` beside §4, since that file is the mental model for
   "an agent is a folder".

## Acceptance criteria

- [x] Exporting an agent whose home contains `AGENT.md`, `skills/s/SKILL.md`,
      `routines/r.py`, `controllers/c/c.py`,
      `controllers/c/sample_configs/a.yml`, `loops/l/loop.md` and `shutdown.md`
      produces a bundle whose `files` paths are exactly those seven
- [x] That same export, against a home that **also** contains `store/user_7/x`,
      `sessions/`, `dry_runs/`, `learnings.md`, `config.yml`, `mutes.yml`,
      `proposals/`, `state/op.json` and `routines/__pycache__/r.pyc`, carries
      none of them (the last two require [[CORR-720]])
- [x] A skill in `agents/_shared/skills/` that the agent reads is **not** in the
      bundle, asserted on a fixture where the agent resolves it successfully
      through `SkillStore` in the same test
- [x] The exported `AGENT.md` text has `server_name: ''`, `created_by: 0` and no
      `forked_from`/`forked_at`, starting from a home where all four are set to
      non-default values; the file on disk still has all four unchanged
- [x] A bundle with `schema: 2` is refused by `parse`, the message names both
      `2` and `1`, and `agent_home(slug)` does not exist afterwards
- [x] A bundle whose `kind` is anything else is refused with a message, not an
      exception
- [x] A bundle one of whose `files[].text` has been altered after `sha256` was
      computed is refused, naming that path, and nothing is written
- [x] A bundle containing `files[].path` of `../../../agents/condor/AGENT.md`,
      `/etc/passwd` and `skills/../../x` is refused whole for each: nothing is
      written, and the tracked `agents/condor/AGENT.md` is byte-identical
      afterwards
- [x] Importing a bundle whose slug already exists (local **and**, in a second
      case, stock-only) answers 409, names the taken slug and a free
      alternative, and leaves the existing agent's `AGENT.md` byte-identical
- [x] Importing with `as_slug` set to a free name writes under
      `local_agents_root()/<as_slug>` and nothing under `stock_agents_root()`,
      asserted by comparing the stock tree's file list before and after
- [x] The imported `AGENT.md` has `created_by` equal to the importing user's id
      and `server_name: ''`
- [x] After import, `mutes.yml` lists every imported routine name and every
      imported skill slug, and `AgentStore().get(slug)` returns the agent — so
      it is registered and listed while its code is switched off
- [x] After import, the agent's prompt index contains none of the imported
      skills and `assistant_routines` offers none of the imported routines;
      after `PUT /agents/{slug}/mutes` unmutes one routine, that one and only
      that one is offered
- [x] Import starts nothing: with a bundle whose `AGENT.md` default_config sets
      a frequency, no `TickEngine` exists for the slug after import and no
      delegation is recorded
- [x] A non-UTF-8 file in the home makes **export** refuse, naming that file,
      rather than producing a bundle the importer will reject
- [x] The preview endpoint and the apply endpoint report the same file list,
      the same scrubs and the same mute list for one bundle — asserted by
      comparing the two responses field by field, so the dialog cannot show
      something the write does not do
- [x] The preview names every `.py` in the bundle, with its byte count, in a
      field distinct from the non-executable files
- [x] A round trip is lossless for everything that travels: export an agent,
      import it under a new slug on a clean local root, and the two homes'
      `publishable_files` sets and file contents match, modulo the three
      scrubbed fields and the new slug
- [x] No restart is needed to use an imported agent: within one process,
      import, then `AgentStore().get`, `StrategyStore().list`,
      `agent_controllers`, `SkillStore(slug)` and
      `discover_routines_from_path` each see the new content on their next call
- [ ] `uv run pytest` reports no failure that is not already failing on the
      merge-base: run it on the merge-base and on the change in the same turn
      and compare the two lists, rather than against a recalled count —
      **scoped, not skipped**: this shipped inside plan `89937873-9f5`, whose
      instruction is to run this item's own criteria plus the tests covering
      the touched files and leave the whole-suite comparison to the plan's
      final verification chunk. 450 tests across the 17 related modules pass,
      and `uv run pytest --collect-only` collects all 6805 (no import broken
      anywhere). See `## Notes`.
- [ ] `cd frontend && npm run build` succeeds, and the frontend suite reports no
      failure absent on the merge-base, compared the same way — the build
      succeeds and `npm run lint:ci` exits 0 ("no new lint errors", 41
      baselined); 69 settings tests and 488 agent/page tests pass. The
      whole-frontend-suite comparison is scoped to the plan's final chunk for
      the same reason as the line above.

## Risks and notes

- **The review is the sandbox, and the design says so.** An imported routine
  runs in the Condor process with no isolation, and this feature builds none.
  What it provides is: the code arrives inert (muted), the preview names every
  executable file before anything is written, nothing is started on import, and
  the operator unmutes item by item. A bundle from an untrusted source is
  untrusted code — the UI copy should say that in one sentence rather than
  implying a guarantee that does not exist.
- **Refusing non-UTF-8 is a real limitation.** A skill folder with a PNG
  reference file cannot be exported until the file is removed. Chosen over a
  base64 branch because it keeps the artifact reviewable; it is a loud failure
  naming the file, and if it turns out to bite, the fix is additive (a
  `bytes_b64` field under the same `schema: 2`).
- **Deploying this needs one restart.** New endpoints in
  `condor/web/routes/agents.py` are not in `main.py`'s `modules_to_reload`, so
  the live API keeps the old shape until `main.py` is restarted
  ([[web-backend-no-hot-reload]]). Do not force-restart while a live loop or dry
  run is trading. Verifying without touching the live bot: a second app on a
  spare port, `WEB_PORT=8099 uv run python -c "import uvicorn; from
  condor.web.app import create_app; uvicorn.run(create_app(), port=8099)"` —
  it registers no engines, so anything derived from a running `TickEngine` reads
  idle there.
- **Depends on [[CORR-720]]**, which is open. Without it the bundle carries a
  root `state/` folder and `__pycache__`, which is the one thing this feature
  promises not to do. Do not work around it in `bundle.py` with a second filter
  — that would leave two selectors disagreeing about what an agent is.
- `agents/` **and** `.condor/agents/` are both excluded from git on this
  install ([[agents-dir-locally-excluded]]; `.gitignore:195`). That is the
  problem being solved, and it also means an implementer must run
  `git check-ignore -v` on any fixture path they expect to commit.
- This doc lives in `features/todo/`, which `.gitignore:187` excludes, so
  filing it leaves no commit. See [[TASK-001]].

## Notes

**Shipped as designed.** Alternative 4 whole: one JSON document, one builder
(`plan`) behind the preview and the write, export scrubbing the three
install-local fields, import refusing a collision rather than replacing, and
every imported routine and skill arriving muted. `[[CORR-720]]` was already
closed, so `publishable_files` is used unchanged and `bundle.py` carries no
second filter — the root `state/` and `__pycache__` cases are asserted here
anyway, so this file fails if that selector ever regresses.

**Three justified deviations.**

1. `plan(bundle, as_slug)` takes an optional `user_id` as well. The preview has
   to show the `created_by` it will stamp and the `agent_key` it will resolve,
   and the only way the dialog and the write can be *guaranteed* to agree on
   those — the criterion two lines up — is for both to come out of one call.
2. `build(slug)` reads **both** layers, local shadowing stock per file, rather
   than only `publishable_files(agent_home(slug))`. The design's reasoning for
   the local layer is about where an import *writes*; an export that could not
   carry a shipped agent would have been an arbitrary hole, and the read order
   is the one `resolve_agent_file` already applies.
3. `frontend/src/pages/AgentWorkspace.tsx` is **not** touched, though the design
   listed it. Export needed no page state: `ExportAgentButton` takes a slug and
   does its own fetch, so `WorkspaceHeader` renders it directly. `onDelete` had
   to be threaded from the page only because it opens that page's confirm
   dialog. Nothing is owed.

**`mcp_servers/condor/server.py` and `tests/test_agents_routes_order.py` were
touched beyond the declared list.** The former is where `manage_agents`'
`Literal` action set and docstring live, so the two new actions are unreachable
without it; the latter is the repo's standing guard that a literal first segment
(`/agents/import`) is not swallowed by `/agents/{slug}`, which is exactly the
shape these routes add.

**Deploying this needs one restart**, as the design says: the three new
endpoints in `condor/web/routes/agents.py` are not in `main.py`'s
`modules_to_reload`, so the live API keeps the old shape until the process is
restarted. Everything above was verified offline against the router directly.

**`FEAT-143` and `FEAT-144` build on this** — schema 2's identity block and a
content-review pass. Both land in `bundle.py` and in `plan()`, which is why the
schema constant, the refusal list and the single plan builder are the shapes
left behind.

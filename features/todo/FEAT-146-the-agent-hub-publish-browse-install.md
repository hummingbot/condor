---
id: FEAT-146
title: The agent hub — publish one of your agents, browse the hub, install an agent without leaving Condor
summary: >-
  FEAT-133 makes an agent one portable file and stops there, so moving one still
  means emailing it. Add the distribution half: publish to the Botcamp hub with an
  account token, browse and install from Condor, and state the trust model —
  any bundle installs, the badge is provenance, the gate is import.
status: todo
effort: L
risk: high
brain: developer
new_files:
  - condor/hub.py
  - tests/test_hub.py
  - frontend/src/components/settings/HubBrowser.tsx
  - frontend/src/components/settings/HubBrowser.test.tsx
touched_files:
  - condor/agents/bundle.py
  - condor/web/routes/agents.py
  - condor/web/routes/settings.py
  - config_manager.py
  - mcp_servers/condor/tools/trading_agent.py
  - frontend/src/lib/api.ts
  - frontend/src/components/settings/AgentsSettings.tsx
  - frontend/src/components/settings/ImportAgentDialog.tsx
  - frontend/src/components/agent/workspace/WorkspaceHeader.tsx
  - condor/agents/README.md
  - agents/condor/skills/agent_builder/SKILL.md
  - PRIVACY.md
depends_on:
  - "[[FEAT-143]]"
  - "[[FEAT-144]]"
commits: []
created: 2026-10-15
---

## Objective

An operator publishes one of their agents to a Botcamp-run hub and installs
somebody else's from it, without leaving Condor and without handling a file.
Done means: a Browse panel that lists what the hub offers with its provenance,
an Install that runs through the same preview the file path uses, a Publish
action beside Export that uploads the same bundle bytes the operator could have
downloaded, and a written trust model that says what a hub badge does and does
not mean.

## Context and constraints

**[[FEAT-133]] is the wire format and stops deliberately at the file.** It
builds one JSON bundle per agent, scrubs the three install-local fields,
previews before it writes, refuses a slug collision and lands routines and
skills muted. Its "Deliberately out of scope" names the two things this item
needs: import from a URL ("the Condor process can reach `localhost` and cloud
metadata endpoints, so a server-side fetch of a user-supplied URL is an SSRF
surface that needs its own design") and overwriting an existing agent (which is
[[FEAT-145]]). **Nothing in this item invents a second format.**

**Nothing in the repo mentions Botcamp.** `git grep -il botcamp` over the tree
returns nothing, so every surface here is new and the service itself lives in a
codebase that is not this one ([[TASK-004]]).

**A project-run collector that Condor talks to already exists, with its address
compiled in.** `condor/sharing/outbox.py:52` is
`COLLECTOR_URL = "https://telemetry.hummingbot.org/v1/conversations"`, and the
module's docstring states the rule: "the collector address is compiled in, like
telemetry's, and no environment variable redirects it." That is not tidiness
here — it is the SSRF answer. A compiled-in base plus a listing id validated by
`validate_identifier` (`condor/fetchers/_identifiers.py:31`, whose docstring
already explains that an unchecked identifier "is an endpoint pivot") means the
server-side fetch [[FEAT-133]] refused to build is no longer a fetch of a
user-supplied URL. Making the base env-overridable would hand that property
back.

**Condor's own account system cannot carry a Botcamp login.** `create_jwt`
(`condor/web/auth.py:48`) issues a 24-hour token from a Telegram-derived
identity, with a one-time login-token exchange (`:258`); there is no external
identity provider anywhere, and the dashboard is served on a configurable
`WEB_PORT`, so an OAuth redirect URI would differ per install. Taking an
outside credential as a pasted value is how this product already does it —
`ApiKeysSettings.tsx`, `ConnectHyperliquid.tsx`, `ImportGatewayWallet.tsx`.

**A local credential lives in a `config.yml` section, and that has a known
trap.** The sharing identity is stored that way
(`condor/sharing/consent.py:266-287`), and `ConfigManager._save_config`
(`config_manager.py:265`) writes a **hand-enumerated allowlist** of sections
whose own code comments record two items closed for exactly this — ARCH-177
("omitting this dropped every preference on the next save") and [[CORR-244]]
("without this the admin's sharing veto … were re-minted on every restart").
A new `hub` section that is not added to that list is silently dropped on the
next save. [[ARCH-738]] is that hazard filed on its own.

**A durable outbox is the wrong shape here, and the reason is in its own
docstring.** `condor/sharing/outbox.py` exists because the sweep "sends what
nobody has read" from a worker thread and because "a user who pressed Unshare
and then lost the network has still revoked". A publish is an interactive act
with a human watching the result; a failure is reported and retried by pressing
the button again. Not reusing the pattern needs its reason stated as much as
reusing one does.

**The scrub guarantee is client-side.** [[FEAT-133]]'s export scrub and
[[FEAT-144]]'s review both run in the submitter's own process, and a submitter
who wants to publish a key can simply not run them. The sharing package reached
the same conclusion about its own scrubber after five closed misses
([[SEC-230]] in `improvements/backend/`, [[SEC-283]], [[SEC-331]],
[[SEC-336]], [[CORR-245]]). So the hub re-runs [[FEAT-144]]'s review on
submission — which is why that module is pure and importable outside Condor's
web app.

## Alternatives considered

**1. Condor installs only bundles signed by the hub.** The strongest-sounding
option. Rejected on four measured grounds. Condor has no key infrastructure —
`install_secret` and `share_secret` (`condor/sharing/consent.py:266-277`) exist
to *prove* something locally and never leave the box — so issuance,
distribution and revocation would all be new. It would require removing
[[FEAT-133]]'s file-import path, or that path becomes the documented way around
the check. A signature attests who built the bundle, and the payload that
matters here is free-text instructions a verified author ships just as easily.
And a signature over the submitted bytes says nothing about the bundle the
operator runs after editing it locally. [[FEAT-143]] rejects signatures as the
*identity* mechanism for the same reasons; this rejects them as the *permission*
mechanism.

**2. The browser talks to the hub directly; Condor only handles the file.** No
server-side fetch, no SSRF question at all. Rejected: the dashboard cannot carry
its bearer on a bare `<a href>` (the reason `useAuthedImage` exists), and more
importantly the bytes the dialog previewed and the bytes installed would come
from two separate requests, which breaks the one-builder rule [[FEAT-133]]
states as its acceptance criterion ("pressing Share sends exactly the bytes the
dialog displayed, and the only way to promise that is for the dialog and the
sender to call one function").

**3. Publish by git — push the agent to a Botcamp-hosted repo.** Rejected for
the reasons [[FEAT-133]] already rejected git as transport: a remote and
credentials per agent, `.condor/` gitignored wholesale (`.gitignore:195`), and
`_publish_agent`'s own docstring calling it "a distribution system nobody asked
for".

**4. Require a Botcamp account to browse as well as to publish.** Rejected: the
listing is public content, and gating it would make the feature useless for an
operator who has no Botcamp login — which is most of them on day one. Publishing
is the act that needs an identity, because it is the act that creates a claim.

## Decision

**Browse and download anonymously; publish with a Botcamp token the operator
pastes into Settings.** The Condor process fetches from a **compiled-in** hub
base URL with a `validate_identifier`-checked listing id, and installs through
[[FEAT-133]]'s existing `plan`/`apply` (or [[FEAT-145]]'s `plan_update`, when
the `agent_uid` matches an agent already here).

### The trust model, stated

This is the decision the rest hangs off, and it is three sentences.

1. **A bundle is untrusted code wherever it came from, and the gate is on the
   receiving install.** Condor installs any well-formed bundle — from a file,
   from a friend, from the hub — and never requires a hub signature. The gate is
   what already exists: every executable file named before anything is written,
   routines and skills muted on arrival, nothing started, [[FEAT-144]]'s
   findings shown beside the file list, and the operator unmuting item by item
   after reading each one. [[FEAT-133]]'s "the review is the sandbox, and the
   design says so" is not revoked here.
2. **The hub is a discovery and moderation service, not a security boundary.**
   A `validated` badge is a claim that a human reviewed one submitted revision
   at one point in time. It is carried in the listing, rendered as provenance,
   and read by nothing that decides whether to write. Condor never refuses an
   unbadged bundle, and a badge never shortens the import review — a bundle
   validated as submitted is not the bundle the operator will run after editing
   it.
3. **Integrity between hub and client is a content digest over HTTPS, not a
   signature.** The listing carries the bundle's sha256; the client verifies the
   downloaded bytes against it before parsing, and [[FEAT-133]]'s per-file
   digests catch any later edit. Author identity is an account claim the hub
   records at upload and renders as a handle; nothing binds it to the bytes
   cryptographically, and the surface says so.

The consequence, and the one hard requirement this places on the service:
**the hub re-runs [[FEAT-144]]'s review on every submission** and publishes its
findings with the listing. The export-side review is advice to the author; the
submission-side review is the only one whose input the author does not control.

## Design

### `condor/hub.py`, headless

The `condor/updates/__init__.py` rule — nothing imports Telegram or FastAPI, the
surfaces are views over this API:

    HUB_URL                                  compiled in, one constant
    browse(query="", page=1) -> Listing[]    the catalogue
    get(listing_id) -> Listing               one entry, with its digest
    fetch(listing_id) -> bytes               the bundle, digest verified here
    publish(slug, token, notes="") -> Receipt
    unpublish(listing_id, token) -> None

`fetch` verifies the sha256 of what it received against the digest `get`
returned and raises on mismatch, before any caller parses it. `publish` builds
the bundle through [[FEAT-133]]'s `build(slug)` — the same bytes `GET
/agents/{slug}/bundle` would hand the operator — so there is no second
serializer.

`Listing` carries: `listing_id`, `agent_uid`, `slug`, `name`, `description`,
`version`, `notes`, `author` (handle, attested by the hub), `badge`
(`validated` | `community`), `digest`, `bytes`, `published_at`, `requires`
([[FEAT-143]]) and `findings` ([[FEAT-144]], as the hub computed them).

**No delete-token capability.** `condor/sharing/wire.py:203`'s
`delete_token_hash` exists because that collector "must not know who you are";
a hub publish is authenticated by an account, so the same token authenticates
the unpublish. Reusing the capability there would be cargo cult.

### Routes

| route | does |
|---|---|
| `GET /api/v1/hub/agents` | proxies `browse` — the browser never talks to the hub, and Condor's bearer stays Condor's |
| `GET /api/v1/hub/agents/{listing_id}` | proxies `get`; `listing_id` through `validate_identifier` |
| `POST /api/v1/hub/agents/{listing_id}/install/preview` | `fetch` → `parse` → `plan` or `plan_update`; writes nothing |
| `POST /api/v1/hub/agents/{listing_id}/install` | the same fetch and plan, then `apply`/`apply_update` |
| `POST /api/v1/agents/{slug}/publish` | `publish`, admin-or-owner guarded like the other agent writes |
| `DELETE /api/v1/hub/agents/{listing_id}` | `unpublish` |
| `PUT /api/v1/settings/hub-token` | stores the token in the `hub` section of `config.yml` |

The install pair re-fetches rather than caching the preview's bytes, and the
digest check is what makes that safe: both requests are verified against the
same listing digest, so they are the same bundle or the second one fails.

### Surfaces

- **Browse / Install** — `HubBrowser.tsx` inside the **Agents** settings tab
  [[FEAT-133]] creates, with `ImportAgentDialog` reused verbatim for the
  preview: the same file list, the same scrubs, the same muted list, the same
  findings, plus the listing's badge and author rendered as provenance with the
  one-sentence statement from the trust model.
- **Publish** — an item beside [[FEAT-133]]'s Export on `WorkspaceHeader`,
  showing what will be uploaded (the same preview) before it goes, and refusing
  with [[FEAT-144]]'s export refusal if a `certain` secret is present.
- **Chat / MCP** — `manage_agents(action="hub_browse"|"hub_install"|"publish_hub")`,
  beside the existing `publish_agent`, with the action-alias entries that module
  already keeps.
- **Token** — one field in Settings. Never logged, never returned by a GET
  (the route answers "set" / "not set"), stored in `config.yml`'s `hub` section.
- **`PRIVACY.md`** — publishing sends content off this box, so it belongs in the
  document that already states what leaves and when.

### What this item does not do

No automatic updates (an update is always an operator act, [[FEAT-145]]); no
ratings, comments or download counts; no hub-side account creation from inside
Condor; no model-assisted review ([[FEAT-144]] rejected it as the gate); no
sandbox.

## Implementation plan

1. `condor/hub.py` with the API above, `HUB_URL` compiled in, digest
   verification in `fetch`.
2. `config_manager.py` — the `hub` section, **including its line in
   `_save_config`'s allowlist** (`:265`), and in the `_load_config`
   `setdefault` block (`:191-198`) and `_default_data` (`:230`).
3. `condor/web/routes/agents.py` + `settings.py` — the routes above.
4. `mcp_servers/condor/tools/trading_agent.py` — the three actions.
5. Frontend — `HubBrowser.tsx`, the Publish item, the token field, `api.ts`.
6. `tests/test_hub.py` — against a stub transport, as the sharing outbox tests
   do; no network in the suite.
7. Docs — `condor/agents/README.md`, the `agent_builder` skill's path table, and
   `PRIVACY.md`.
8. Order of landing: browse + install first (the read half is useful alone and
   needs no token), publish second.

## Acceptance criteria

- [ ] `condor/hub.py` imports in a subprocess whose `sys.modules` then contains
      no `fastapi` and no `telegram`
- [ ] `HUB_URL` is a module constant and **no** environment variable changes it:
      asserted by setting every plausible env name and re-importing, mirroring
      the sharing outbox's compiled-in rule
- [ ] `get` on a `listing_id` containing `../`, `?`, `#` or whitespace raises
      `IdentifierError` before any request is made, asserted by a transport stub
      that records zero calls
- [ ] `fetch` raises, naming the listing, when the bytes returned do not hash to
      the digest `get` reported, and no caller receives the bytes
- [ ] `fetch` on matching bytes returns them, and the bundle then parses through
      [[FEAT-133]]'s `parse` with no second code path
- [ ] `publish(slug, token)` sends byte-identical content to what
      `GET /agents/{slug}/bundle` returns for the same agent, compared byte for
      byte in one test
- [ ] `publish` refuses, sending nothing, when [[FEAT-144]]'s review reports a
      `certain` secret finding
- [ ] A publish failure (stub returns 500, then a timeout) raises a readable
      error, enqueues nothing anywhere, and leaves no file behind — there is no
      durable queue to inspect
- [ ] The hub token round-trips a `ConfigManager` save/reload cycle: set it,
      call the save path, build a fresh `ConfigManager` over the same file, and
      read it back — the ARCH-177/[[CORR-244]] failure asserted rather than
      assumed
- [ ] `GET /api/v1/settings/hub-token` never returns the token value, for a set
      and an unset token, and the token appears in no log record emitted during
      a publish (asserted with `caplog`)
- [ ] Installing a listing whose `agent_uid` is not present locally runs
      [[FEAT-133]]'s `plan`/`apply`; installing one whose `agent_uid` matches an
      installed agent runs [[FEAT-145]]'s `plan_update`, asserted by which plan
      shape the preview returns
- [ ] The install preview and the install carry the same plan for one listing,
      compared field by field, with two separate fetches behind them
- [ ] A listing with `badge: "community"` installs exactly as one with
      `badge: "validated"`: same plan, same mutes, same findings shown — the
      badge changes no decision, asserted by comparing the two responses with
      only the badge differing
- [ ] An installed agent's routines and skills are muted and no `TickEngine`
      exists for it, on the hub path as on the file path
- [ ] `unpublish` sends the account token and no `delete_token`, asserted on the
      request body
- [ ] The suite makes no network call: every test drives a stub transport, and a
      run with outbound sockets blocked passes
- [ ] `uv run pytest` reports no failure that is not already failing on the
      merge base: run it on the merge base and on the change in the same turn
      and compare the two lists
- [ ] `cd frontend && npm test` and `cd frontend && npm run build` report no
      failure absent on the merge base, compared the same way

## Risks and notes

- **Measured gate at `87043ad6`** (this workstream's worktree): `make test` =
  `uv run pytest` → **6683 passed, 2 skipped, 0 failed, exit 0 in 105.57 s**;
  `cd frontend && npm test` (`vitest run`) → **220 files, 2286 tests, 0 failed,
  exit 0 in 12.18 s**. Provenance only; re-measure, and treat neither count as
  expected — [[FEAT-139]] landed immediately afterwards and added
  `tests/test_preflight.py`, so the pytest count has already moved.
- **The service does not exist and is not in this repo.** Everything here is
  testable against a stub transport, but nothing here is *verified against a
  real hub* until one is standing. The decisions that are not Condor's to make —
  the hostname, who reviews a submission for the `validated` tier, how a publish
  token is issued, and whether the hub runs [[FEAT-144]] as a Python dependency
  or a reimplementation — are [[TASK-004]], which also carries the live
  round-trip verification. Do not put a live-service criterion on this doc: the
  session implementing it cannot observe one.
- **A reimplementation of the review on the hub side would drift, and the drift
  would be in the half nobody looks at.** If the service cannot depend on
  `condor.agents.bundle_review`, say so in [[TASK-004]] and make the hub expose
  which version of the review produced its findings, so a client can see when
  the two disagree.
- **Publishing sends content off this box and is irreversible in practice.**
  Unpublish removes the listing; it does not recall a download. The publish
  preview is therefore the last gate, and the copy should say that in one
  sentence — the same posture as the share dialog.
- **`.condor/` is gitignored wholesale** (`.gitignore:195`), which is why the
  locally authored agents are the ones that most need this. An implementer
  fixture under `agents/` needs `git check-ignore -v` first
  (`.gitignore:195` and the local excludes).
- **Deploying this needs one restart.** New endpoints in
  `condor/web/routes/agents.py` are not in `main.py`'s `modules_to_reload`, so
  the live API keeps the old shape until `main.py` restarts — the same note
  [[FEAT-133]] carries. Do not force-restart while a live loop or dry run is
  trading.
- `/features/` is excluded by `.gitignore:187` and the docs tracked on this
  branch are force-added, so committing this doc needs `git add -f`.

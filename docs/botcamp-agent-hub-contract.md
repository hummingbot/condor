# Botcamp agent hub — service contract

**Audience.** This document is for the team implementing `hummingbot/botcamp-site`
(hereafter "the hub"). It is self-contained: everything a reader needs to build
the endpoints, the two deterministic refusal checks, the submission queue and
the reviewer API is stated here, with examples. It does not assume access to
the Condor repository, and it does not require reading Condor's own feature
docs (FEAT-133, FEAT-143, FEAT-144, FEAT-145, FEAT-146, FEAT-147) to implement
against.

**What the hub owns.** Validating that a submitted agent is structurally sound,
running a human-gated safety review before anything is publicly listed, serving
the public catalogue, and moderating what it has published. **What Condor owns.**
Deciding whether to install anything, whatever its badge — the badge is
provenance, never an install permission. That split is stated in full in
[Trust model](#trust-model-and-what-a-badge-is-not), and it is why the deterministic
checks in this contract exist on the hub side at all: they are the one thing a
badge can actually promise, because they are reproducible.

**What is out of scope here.** Standing up the service, provisioning its
hostname, and performing the live round-trip against a real Condor install are
[[TASK-004]]'s remaining work, which this document exists to unblock, not to
replace. Per-user profiles under one Botcamp account are deferred; every
credential and link this contract defines is install-wide on the Condor side.

## Contents

1. [The portable agent bundle](#1-the-portable-agent-bundle)
2. [API base, transport rules, and errors](#2-api-base-transport-rules-and-errors)
3. [Catalogue: agent lookup and version download](#3-catalogue-agent-lookup-and-version-download)
4. [Account linking (OAuth2 device flow)](#4-account-linking-oauth2-device-flow)
5. [Submission lifecycle](#5-submission-lifecycle)
6. [Deterministic refusal checks](#6-deterministic-refusal-checks)
7. [Review queue and reviewer API](#7-review-queue-and-reviewer-api)
8. [Moderation and delisting](#8-moderation-and-delisting)
9. [Trust model and what a badge is not](#9-trust-model-and-what-a-badge-is-not)
10. [Rejected alternatives](#10-rejected-alternatives)
11. [Appendix: field reference](#11-appendix-field-reference)

---

## 1. The portable agent bundle

A Condor "agent" is a named set of library files — `AGENT.md`, `skills/`,
`routines/`, `controllers/`, `loops/*/loop.md` and `shutdown.md`. Condor
exports it as **one JSON document**, built from exactly the files
`publishable_files` selects (the agent's *library*, never its runtime state,
memory, journals, or mutes) and rendered deterministically, so the same agent
content always renders to the same bytes. This is the document the hub stores,
serves back on download, and reviews.

### 1.1 Wire shape

```json
{
  "schema": 1,
  "kind": "condor-agent-bundle",
  "exported_at": "2026-10-21T12:00:00Z",
  "version": 3,
  "notes": "Added a drawdown guard to the grid loop.",
  "author": { "name": "federico", "handle": "" },
  "requires": {
    "connectors": ["binance_perpetual"],
    "tools": [],
    "agent_key": "claude-sonnet-5",
    "skills": { "carried": ["liquidation_guard"], "inherited": ["risk_basics"] }
  },
  "agent": {
    "slug": "adaptive_grid_trader",
    "name": "Adaptive Grid Trader",
    "description": "Runs a grid strategy with a volatility-aware spread.",
    "when_to_consult": "",
    "agent_uid": "8f1e2c3a4b5d6e7f8091a2b3c4d5e6f7"
  },
  "files": [
    { "path": "AGENT.md", "sha256": "…64 hex…", "text": "---\n...\n---\n..." },
    { "path": "skills/liquidation_guard/SKILL.md", "sha256": "…", "text": "..." }
  ]
}
```

This is the document the hub stores and reviews on submission (§5, §6).
**It is not what the implemented download path actually serves.** §3.3's
`GET /api/agents/{slug}/versions/{version}` — the one download endpoint PR
#111 ships — answers with a ZIP of the agent's library files, a different
wire shape entirely; see §3.3 and §3.4 for what Condor's client reads off
that response instead.

Field notes, normative for the hub as much as for Condor:

- **`kind`** must equal `"condor-agent-bundle"`. Anything else is refused before
  any other check runs (§6.1) — it means "this is not one of these documents,"
  not "this document is invalid."
- **`schema`** is `1` today. A hub that receives any other integer refuses the
  submission whole, rather than attempting a partial read — an unknown schema
  is a newer document shape this hub does not understand, and *guessing* at it
  is the one failure mode worth refusing outright. Do not add fields to this
  shape from the hub side; a field Condor does not already read is simply
  dropped on install, so any new carried data belongs in a schema bump, which
  is Condor's decision to make (not this contract's).
- **`agent.agent_uid`** is the identity that survives a rename. Two listings
  of different versions with the same `agent_uid` are the same agent's
  lineage. It is **not** the catalogue's listing identity (§3) — a hub mints
  its own opaque `listing_id` per published revision and must never treat
  `agent_uid` as safe to interpolate into a URL path or trust as globally
  unique across authors (two different authors could, in principle, submit
  bundles claiming the same `agent_uid`; the hub's own authorization model,
  §4, is what actually controls who may update a listing lineage, not this
  field).
- **`version`** orders two bundles *within one submitter's own lineage*. It is
  advisory — the hub does not need to enforce monotonicity, and should not
  reject a resubmission for carrying a version number it has seen before; what
  it must do is let the author's own history page order by it.
- **`author`** is a claim with no cryptographic backing. `author.handle` is
  **not sent by Condor** — see §5.2 — and the hub attaches it itself from the
  account that authenticated the submission. Never accept a client-supplied
  `handle`.
- **`requires`** is derived by Condor from the other files, never hand-authored.
  The hub carries it opaquely in the listing; it does not need to validate it
  beyond "is this JSON" and must not attempt to re-derive it.
- **`files[].sha256`** is the full lowercase hex sha256 of `text`, encoded as
  UTF-8. The hub SHOULD verify each file's digest against its own text on
  submission (cheap, and it catches a malformed submission before the review
  even starts) but this is advisory for the hub's own data integrity. The
  implemented download path (§3.3) does not serve this rendered document at
  all, so there is no digest of it for the client to check today — it checks
  a header-carried digest of the ZIP bytes §3.3 actually returns instead,
  which has no relationship to any field in this shape.

### 1.2 Limits

- **Maximum rendered size: 2,000,000 bytes** of UTF-8 JSON. A submission over
  this size is refused with `413` before anything else runs. Condor's own
  client reads at most this many bytes from a download reply before giving up
  on the digest check, so a hub that served more would simply make every
  install-side download fail a digest comparison — there is no reason to
  implement that unless the hub is deliberately refusing in a way clients can
  diagnose, which is exactly what the explicit `413` gives them instead.
- **Catalogue page replies (one agent lookup; a future browse page, if
  §3.1 is ever built) are capped at 1,000,000 bytes** by the client; see
  §3.2.
- The whole document must be valid UTF-8 JSON. A submission that is not valid
  UTF-8, or not valid JSON, is refused `400` before any field is read.

---

## 2. API base, transport rules, and errors

### 2.1 Base URL

```
https://botcamp.xyz/api
```

Condor compiles this in as a constant (`condor/hub.py`'s `HUB_URL`) and ships
no environment variable or setting that redirects it in production (a narrow,
loudly-logged dev-only override exists for testing against a non-production
hub; it never changes what a production install talks to). This is a
deliberate SSRF defense, not an oversight to design around: a server-side
fetch of a **user-supplied** URL is the surface Condor refuses to build; a
fetch of a **compiled-in** one, to a single path segment that has already
passed an identifier check, is not. There is no `hub.` subdomain and no
versioned `/v1` path segment — the hub lives on the `botcamp.xyz` app itself,
under two path families: `/api/condor/*` (this install's own account and
device-login state — §4) and `/api/agents/*` (the catalogue and submission
surface — §3, §5, §7, §8). Two consequences for the hub's design:

- **Never require a redirect.** Condor's HTTP client refuses every redirect
  response outright (it raises rather than following a `3xx`), on every verb,
  including the bundle download. If a future CDN or load balancer in front of
  the hub issues a `302`, every Condor install breaks at once. Terminate
  redirects before they reach the client, or don't use them.
- **The path prefix is part of the contract.** Every endpoint below is given
  as a full path under this base. A new, incompatible shape for an existing
  path is a new path segment Condor would need a release to pick up — version
  by path, not by header, so an old client simply keeps working indefinitely
  against the paths it already calls.

### 2.2 Identifiers in a URL path

Every `listing_id`, `submission_id`, and **agent slug** this contract
addresses by path segment is validated by Condor against the charset
`[A-Za-z0-9._-]` **before any request is built**, and `.` / `..` alone are
refused as values even though they match that charset (`condor/hub.py`'s
`_segment`). A **version** path segment (§3.3) is validated separately and
more narrowly: only the literal `latest`, or a positive integer with no
leading zero, is ever sent — anything else is refused locally
(`_version_segment`) before a request leaves the box. (§4's device flow
interpolates no caller-controlled identifier into a URL path at all —
`device_code` travels only in a POST body, read back by the hub from what it
itself issued, so neither rule constrains it.) What follows for the hub:

- **Mint `listing_id`/`submission_id` from that charset.** An opaque id
  containing `/`, `?`, `#`, or whitespace cannot be used by the Condor client
  at all — it will never reach the hub, because Condor refuses to build the
  request locally. A UUID4, a ULID, or a base32/base62 string are all safe
  choices.
- **An agent slug is not hub-minted**, but it is held to the same charset for
  the same reason: a slug containing `/` would never reach
  `GET /api/agents/{slug}` (§3.2) or its version download (§3.3) either.
- **Never answer with a different slug or version than was asked for.**
  There is no earlier listing call `fetch` (§3.3) cross-checks a download
  against — the integrity check lives entirely inside that one response (its
  own `X-Agent-Sha256` header), never a comparison against a value read from
  a separate call. Don't try to be helpful by normalizing or redirecting a
  near-miss slug or version — answer `404`.
- **A malformed slug or version never reaches the hub at all.** Condor's
  client raises locally before building the request; the hub is free to
  reject one of these itself for defense in depth, but it will never
  actually receive the malformed case from this client.

### 2.3 Headers

- `Authorization: Bearer <credential>` carries every credential this contract
  defines — the author credential (§4), the reviewer credential (§7.1). Never
  accept a credential in a query string or a request body; Condor never sends
  one that way, and an access log is exactly the leak this defends against.
- Condor always sends `Accept: application/json` and `User-Agent:
  condor-hub/1`. It sends `Content-Type: application/json` only when a body
  is present.
- Condor's client reads the full response body before classifying it by HTTP
  status, so a `4xx`/`5xx` response SHOULD still carry a JSON body shaped
  `{"detail": "<message>"}` (or `{"error": "<message>"}`) — not an HTML error
  page. Condor renders this `detail`/`error` string to a human (truncated at
  400 characters), and `401`/`403` get an additional canned sentence from
  the client regardless of what the body says, so don't rely on the body text
  being shown verbatim for those two statuses specifically.

### 2.4 Status codes, as Condor's client interprets them

| Status | Condor's reading |
|---|---|
| `2xx` | Success; body parsed as the endpoint's documented shape. |
| `400` | One of three distinct shapes, told apart by body, not by status alone: the caller's request was malformed; a deterministic publish refusal (§5.2), shaped `{"status": "rejected", "reasons": [...]}`; or, from the device-token poll alone (§4.3), an OAuth2 device-flow error shaped `{"error": "authorization_pending" \| "slow_down" \| "access_denied" \| "expired_token" \| "invalid_grant"}`. Condor retries automatically only on `authorization_pending`/`slow_down`; every other case ends the polling attempt. |
| `401` / `403` | The credential is missing, wrong, or revoked. Rendered to the admin as "set a new one in Settings." |
| `404` | The thing named in the path does not exist (or no longer does). |
| `409` | A conflict Condor's own retry logic does not resolve by itself — reserved for a submission resubmitted with changed bytes under an id already decided (§5.4). The device flow (§4) has no endpoint that returns `409`: a login in flight is simply forgotten locally rather than cancelled on the hub, and a device code is never replayed for a second credential. |
| `413` | The body exceeds a size limit named in this contract. |
| `429` | Rate limited. Include `Retry-After` if you can; Condor does not currently read it, but a future client version will, and it costs the hub nothing to send now. |
| `5xx` | Upstream failure. Condor's web layer turns this into its own `502` to the dashboard, i.e. it is reported as "the hub is unreachable," not attributed to the operator. |

A transport failure (connection refused, timeout, TLS failure, a redirect)
never reaches this table at all — Condor's client raises locally with no
status before any of the above applies.

---

## 3. Catalogue: agent lookup and version download

Every endpoint in this section is **anonymous**. No credential is required —
the public catalogue is Botcamp's one surface with no login wall, by design
(most Condor installs will install an agent long before anyone on the box has
a Botcamp account at all). A credential is *accepted*, optionally, on §3.2
and §3.3: a linked profile's own token is what turns a membership/purchase
gate into a 200 for an account that actually has access to a gated agent.

§3.2 and §3.3 are **implemented today** — `condor/hub.py`'s `get()` and
`fetch()`, exercised against PR #111 of `hummingbot/botcamp-site`. §3.1 is
not: there is no browse or search endpoint yet, and this section states that
plainly rather than describing one as if it existed.

### 3.1 Browse — prospective, not implemented

PR #111 ships no `GET /agents?query=&page=` or any other discovery endpoint.
A caller must already know an agent's **slug** — there is nothing in
`condor/hub.py` a Condor install can call to find one, and no Condor surface
offers a search box today. Closing that gap is [[TASK-004]]'s remaining
work, not this contract's.

This subsection is kept, unimplemented, as the target shape for that future
catalogue, because the submission, review and moderation sections later in
this document (§5, §7, §8) already define and produce the richer `Listing`
object (`listing_id`, `badge`, `author`, `digest`, `findings`,
`review_version`, …) it would serve — see §11.2 for that shape. **None of it
is reachable through any endpoint today.** Do not read the JSON example
below, or any field name in it, as something `GET /api/agents/{slug}` (§3.2)
answers with — §3.2 returns a different, narrower shape, defined in its own
section.

```
GET /v1/agents?query=&page=
```

- `query` (optional, default `""`) — free text, URL-encoded by the client.
  Match it however the hub likes (title, description, slug); it is never used
  to address a resource, so it needs no identifier validation on the hub
  side either.
- `page` (optional, default `1`, 1-indexed) — the client would clamp any
  value below 1 up to 1 before sending.

A future response, `200`, capped at **1,000,000 bytes**:

```json
{
  "listings": [
    {
      "listing_id": "lst_8f3a9c2e1b",
      "agent_uid": "8f1e2c3a4b5d6e7f8091a2b3c4d5e6f7",
      "slug": "adaptive_grid_trader",
      "name": "Adaptive Grid Trader",
      "description": "Runs a grid strategy with a volatility-aware spread.",
      "version": 3,
      "notes": "Added a drawdown guard to the grid loop.",
      "author": { "name": "federico", "handle": "cardosofede" },
      "badge": "validated",
      "digest": "…64 hex sha256 of the exact bytes a future unified download would return…",
      "bytes": 48213,
      "published_at": "2026-10-20T09:14:00Z",
      "requires": {
        "connectors": ["binance_perpetual"],
        "tools": [],
        "agent_key": "claude-sonnet-5",
        "skills": { "carried": ["liquidation_guard"], "inherited": ["risk_basics"] }
      },
      "findings": [
        {
          "category": "endpoint",
          "label": "public",
          "path": "controllers/grid.py",
          "line": 14,
          "start": 302,
          "end": 329,
          "certain": false,
          "context": ""
        }
      ],
      "review_version": "bundle-review-2026-10-01"
    }
  ]
}
```

No pagination metadata (no `total`, no `has_more`) would be required — a
future client reads only the `listings` array and stops paging when a page
comes back empty or shorter than the hub's own page size. **Do not include a
`"trust"` key or any other top-level key** — that field would be appended by
Condor's own web layer from a sentence it owns, not read from the hub's
response.

### 3.2 `GET /api/agents/{slug}` — look up one agent (implemented)

**Implemented today** (`condor/hub.py`'s `get()`). Given a slug the caller
already knows — there is no discovery endpoint yet, §3.1 — this answers with
the agent's catalogue entry and its `APPROVED` versions.

`slug` is validated against the charset `[A-Za-z0-9._-]` (§2.2) before any
request is sent.

`token` (optional, `Authorization: Bearer <credential>`): omitted, this is
the anonymous read a free agent answers; a linked profile's own token is
what turns a membership/purchase gate into a 200 for an account that
actually has access.

Response `200`:

```json
{
  "slug": "adaptive_grid_trader",
  "title": "Adaptive Grid Trader",
  "summary": "Runs a grid strategy with a volatility-aware spread.",
  "latest": 3,
  "versions": [
    {
      "version": 3,
      "sha256": "…64 hex…",
      "size": 48213,
      "fileCount": 6,
      "sourceRef": "",
      "createdAt": "2026-10-20T09:14:00Z",
      "manifest": {}
    }
  ]
}
```

See §11.1 for the full field reference (`AgentListing`/`AgentVersion`). In
brief:

- `title`/`summary` map to Condor's own `name`/`description` — send either
  the alias shown or the bare field name; Condor's client (`populate_by_name`)
  accepts both.
- `versions` lists only `APPROVED` revisions, newest first; `latest` is the
  hub's own pick among them, never computed by Condor.
- `sha256` here is a hint for a version picker to render. It is **not** what
  the download (§3.3) is checked against — that check is answered entirely
  within the download response itself, via a header, never by comparing
  against a field read from this call first.
- `manifest` is the hub's own coarse summary (name/agent_key/tools/loops, at
  the hub's discretion), rendered as a hint only. The bundle Condor actually
  acts on is the one `condor.botcamp_import.from_zip` (§3.4) builds from the
  downloaded files, never this field.
- **There is no `listing_id`, `agent_uid`, `author`, `badge`, `digest`, or
  `findings` on this read path.** Those are §11.2's prospective `Listing`
  fields, produced only once the submission/review surface (§5, §7, §8)
  exists. Do not add them to this response — rendering a badge here would
  claim a human review happened on an agent nothing has reviewed at all
  (§9).

Capped at **1,000,000 bytes** (`MAX_LISTING_BYTES`).

Errors (`condor/hub.py`'s `_access_refusal`):

| Status | Meaning |
|---|---|
| `402` | This agent requires a Botcamp membership or purchase to clone. |
| `401` | Sign in — no token was sent, or the hub no longer honours the one sent. |
| `403` | Private agent, or a token without `agents:read` scope. |
| `404` | No such agent, or no `APPROVED` version exists yet. |

### 3.3 `GET /api/agents/{slug}/versions/{version}` — download one version (implemented)

**Implemented today** (`condor/hub.py`'s `fetch()`). Returns the **raw ZIP
bytes** of one approved version's library files — not the JSON bundle
document §1 describes, and not wrapped in a JSON envelope. This replaces the
abandoned `GET /v1/agents/{listing_id}/bundle` design entirely: there is no
earlier listing call this cross-checks a `digest` field against, because the
integrity check is answered in this **same** response, not a second round
trip.

`version` is the literal `latest` (the hub resolves it) or a positive
integer as a string, with no leading zero (§2.2); anything else is refused
locally before a request is even sent. `token` is accepted the same way as
§3.2.

Response `200`, a zip archive, not JSON:

- **`X-Agent-Sha256`** (required): the lowercase hex sha256 of the exact
  response body. Condor's client computes its own sha256 over the bytes it
  received and refuses the whole exchange — raising before `from_zip` (§3.4)
  ever runs — if the two disagree, or if this header is absent entirely. A
  hub that cannot say what it served is treated as offering no integrity,
  not as trusted by default.
- **`X-Agent-Version`**: the concrete version number this response resolved
  to. When the caller asked for `latest`, this is how Condor's client learns
  which integer version it actually received, for its own bookkeeping;
  absent or unparseable, the client falls back to the literal version it
  requested if that was already a number, or records `0`.
- Capped at **2,000,000 bytes** (`MAX_BUNDLE_BYTES`) — a reply over this is
  refused locally before the digest is even checked.
- Never redirect. Condor's client refuses every `3xx` outright, on every
  verb, this one included (§2.1).

Errors: the same table as §3.2 (`402`/`401`/`403`/`404`), via
`_access_refusal`.

### 3.4 Receiving the download: Condor's own conversion

What §3.3 returns is a zip of an agent's library files — `AGENT.md`,
`skills/`, `routines/`, `loops/`, and either Condor's own
`controllers/<name>/<name>.py` layout or Hummingbot V2's upload-side layout,
`controllers/<generic|market_making|directional_trading>/<name>.py`. Turning
it into a Condor `Bundle` (`condor.botcamp_import.from_zip`) is entirely
install-side and runs independently of whatever the hub already validated at
upload:

- Every `.py` found one level under one of the three Hummingbot V2
  controller-type directories is rewritten to `controllers/<name>/<name>.py`
  — the only layout Condor's own controller loader looks for. A file already
  in Condor's own layout, or a loose one neither layout recognizes, travels
  unchanged.
- Local validation runs again, regardless of what the hub's own upload-time
  checks already enforced: at most 300 files, each at most 1,048,576 bytes,
  a running total under the same 2,000,000-byte cap §3.3's own download
  limit uses, no symlink, no path that `plan`-time path containment would
  refuse (absolute, a `..` segment, a NUL or control character), every file
  valid UTF-8 text, and an `AGENT.md` present among the members — otherwise
  the whole archive is refused before a single file is installed. (This
  pass does not itself refuse two members that collide on the same path
  after the controller rewrite above; `plan`'s own later check does.)
- The resulting `Bundle`'s `agent.slug` is the **caller's own** already-
  validated slug — the one §3.2/§3.3 were called with — never a value read
  out of the zip; nothing inside the archive states one.

This conversion is the receiving half of the trust model §9 states: an
`APPROVED` version is still untrusted code to the install that downloads it,
and every one of FEAT-133/FEAT-144/FEAT-147's own gates — naming every
executable file before anything is written, muted routines and skills on
arrival, no execution consent for any imported `.py` — runs on a Botcamp
download exactly as it runs on a plain file import.

---

## 4. Account linking (OAuth2 device flow)

Publishing and withdrawing act under one Botcamp account's identity, and that
identity is **install-wide** on the Condor side — one credential per Condor
install, usable only by that install's admin, with no per-user identity inside
Condor yet (deferred). The design problem this section solves: Condor's web
dashboard runs on an operator-chosen `WEB_PORT` that varies per install and is
frequently not even internet-reachable, so a standard OAuth redirect back to
the dashboard has no stable URI to register. See §10 for why a redirect-based
OAuth flow and a pasted token were both considered and rejected in favor of
this one.

This section describes the standard OAuth2 device authorization grant
([RFC 8628](https://www.rfc-editor.org/rfc/rfc8628)) as Condor's client
(`condor/hub.py`'s `device_start`/`device_state`/`device_cancel`/`me`,
exercised by `tests/test_hub.py`) actually calls it against PR #111 of
`hummingbot/botcamp-site`. It replaces both the one-time pasted-token UX and
FEAT-148's own `/v1/link` pairing-code protocol, which was built and shipped
against this contract's earlier draft before the hub's actual PR #111
endpoints were available to reconcile against — that flow was never wired to
a live hub and no Condor release ever called it against one. The Botcamp PR's
own source was not accessible from this workspace during this rewrite; the
request and response shapes below are reconstructed from Condor's client
implementation and the fixtures its tests assert against, not read from the
hub's source, and should be verified against the actual hub implementation
before being treated as settled.

### 4.1 Flow, end to end

1. Condor's backend calls `POST /api/condor/device/code` with **no
   credential** (there isn't one yet) and an empty JSON body.
2. The hub mints a `device_code` (opaque, used only for polling — never shown
   to a human), a short `user_code` (shown to the human so they can confirm
   the approval page matches the login Condor started), and a
   `verification_uri`/`verification_uri_complete` to open. It returns all of
   these plus `expires_in` and a polling `interval`.
3. Condor builds the approval link **from its own configured hub base plus
   the returned `user_code`** (`<configured base origin>/connect?code=<user_code>`)
   and opens that in a new browser tab, showing the `user_code` beside it so
   the admin can confirm it matches. It does **not** open or display
   `verification_uri`/`verification_uri_complete` at all — not even when
   their host matches. A hub response is not something Condor treats as safe
   to redirect an admin's browser to sight unseen, since nothing before that
   point vetted it: the device-code call carries no credential, so nothing
   proves who answered. The hub's own pages MUST therefore live on the same
   origin as its API; a `verification_uri` pointing anywhere else is ignored
   and logged once, and the admin sees only the locally built link.

   Condor implements this at the backend boundary (SEC-819): the two fields
   are parsed and then dropped inside `condor.hub`, and the only link that
   reaches a response — `approval_url`, on both `POST
   /settings/hub-device/start` and every `pending` `GET /settings/hub-device`
   — is the derived one. A dashboard therefore cannot reintroduce the hole by
   preferring a field it was never sent.
4. On that page, the hub requires the human to already be signed into
   Botcamp (or sign in first), shows the `user_code` again for the human to
   compare against what Condor showed, and offers Accept / Deny. This page
   itself is out of this contract's scope beyond "it must show the
   `user_code` before Accept" — it is server-rendered UI, not an API response
   Condor parses.
5. Condor's backend polls `POST /api/condor/device/token` (no credential —
   the point of the flow is that none exists yet) with the `device_code`, at
   the hub's own `interval`, until it sees a terminal state.
6. On success, that **one** poll response carries the access token, its
   granted scope, and the identity it was issued to. Condor stores the token
   server-side, immediately, through `update_hub`, and never sends it to its
   own browser — see §4.6. The pending attempt is then forgotten; there is no
   further poll of the same `device_code` to repeat anything to.
7. Condor's backend may stop polling a `device_code` at any point before a
   terminal state (e.g. the admin navigated away, or started a new login) —
   see §4.5; the device-flow contract has no hub-side cancel endpoint, and an
   abandoned `device_code` simply expires at the hub on its own, at
   `expires_in`.

### 4.2 `POST /api/condor/device/code` — start a device login

Request body: none required (an empty JSON object `{}` is fine; Condor sends
no `client_id`/`client_name`/`scope` — see the note below on scope).

Response `200`:

```json
{
  "device_code": "dev_3f8c1a9e2b7d4f60",
  "user_code": "WXYZ-4821",
  "verification_uri": "https://botcamp.xyz/connect",
  "verification_uri_complete": "https://botcamp.xyz/connect?code=WXYZ-4821",
  "expires_in": 600,
  "interval": 5
}
```

- `device_code` is never shown to a human and never logged by Condor — it is
  the polling secret. `user_code` is the one the human compares; it alone
  must not be sufficient to approve a login (one that leaked into a browser
  history or a screen share must not authorize anything by itself without
  the human seeing and confirming the matching approval page).
- `verification_uri`/`verification_uri_complete` are read but never used:
  per §4.1 step 3 Condor derives the link it opens from its own configured
  base, so a hub may send either, both or neither without changing what the
  admin sees. An off-origin value is logged once and otherwise discarded.
- `expires_in` (seconds; default assumed `600` if omitted) and `interval`
  (seconds; default assumed `5` if omitted, floored at `1`) govern how long
  Condor keeps polling and how often. A login still `pending` past
  `expires_in` is answered `expired_token` on every subsequent poll (§4.3),
  never a token.
- **No explicit scope request.** Condor's client omits `scope` entirely from
  this request; it expects the hub to grant the one all-or-nothing,
  install-wide scope (`agents:read` and `agents:publish` together) that the
  pasted-token flow it replaces always implied. There is no way, today, for
  Condor to request a narrower grant, and the hub does not need to support
  one.
- **Rate limit this endpoint per source** (IP, or whatever the hub already
  keys abuse limits on) — it is the one step of this flow that needs no
  credential to call at all, which makes it the cheapest one to hammer.
- **One active login per install, enforced on Condor's side, not the hub's.**
  If Condor starts a second device login while an earlier `device_code` is
  still pending, Condor's own backend simply forgets the earlier
  `device_code` and stops polling it — the hub has no install identity to key
  "one pending login per install" on before a token is issued, and does not
  need to implement that itself.

### 4.3 `POST /api/condor/device/token` — poll / exchange

Request body: `{"device_code": "<the value from §4.2>"}`. No credential.

Response `200` once the human has approved:

```json
{
  "access_token": "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60",
  "token_type": "bearer",
  "scope": "agents:read agents:publish",
  "user": { "id": "u_1", "name": "cardosofede" }
}
```

- `access_token` is read exactly once, by this one response, and immediately
  handed to `update_hub` — see §4.6. It is never repeated on any later call
  for the same `device_code`; the hub MAY therefore invalidate `device_code`
  the moment it answers `200` the first time (a replay of the same
  `device_code` after that point has nothing to answer with that Condor would
  accept as new — it has already stopped polling).
- `scope` may be a space-separated string (as above, the standard OAuth2
  shape) or a JSON array of strings; Condor's client accepts either.
- `user` carries at least one of `name` or `id`; Condor renders whichever is
  present as the linked profile's handle (`name` preferred). `token_type` is
  read by nothing on Condor's side today but SHOULD be sent as `"bearer"` for
  forward compatibility.

Response `400` while the login has not yet reached a terminal state, or has
ended in one Condor must stop on, shaped `{"error": "<code>"}`:

| `error` | Condor's reading |
|---|---|
| `authorization_pending` | Normal — the human has not yet acted. Keep polling at `interval`. |
| `slow_down` | Condor is polling too fast; it widens its own `interval` by 5s and keeps polling. |
| `access_denied` | The human denied it. Reported once, to the poll that observed it, then the attempt is forgotten. |
| `expired_token` | Past `expires_in` with no decision. Reported once, then forgotten. Condor also stops polling locally once its own copy of `expires_in` elapses, without spending a further request on an attempt the hub has likely already retired on its side. |
| `invalid_grant` | A `device_code` the hub does not (or no longer) recognize. Reported once, as its own distinct state, then forgotten. |

An `error` value outside this set is treated as a protocol violation Condor's
client does not know how to handle, and raises rather than guessing at a
state. Any other status code (a transport failure, a `5xx`, a `401`/`403`) is
read per §2.4's general table, not as a device-flow-specific case.

### 4.4 `GET /api/condor/me` — who a token identifies

Credential-authenticated (`Authorization: Bearer <access_token>`). Used both
to show the admin who is currently linked (read fresh, not from the
device-login record cached at link time) and to detect a token Botcamp's own
profile page revoked out from under this install.

Response `200`:

```json
{
  "data": {
    "user": { "id": "u_1", "name": "cardosofede", "slug": "cardosofede" },
    "scopes": ["agents:read", "agents:publish"]
  }
}
```

`401` means the hub no longer honours this token (revoked from the account's
own token list, most likely, rather than anything the request itself did
wrong) — Condor clears its local copy of the token on any `401` from this or
any other token-bearing call, the same way, and the admin sees "connect a
profile" rather than a stale linked identity on the next read. `400` with no
token presented at all is a local Condor refusal (`NoTokenError`) that never
reaches the hub.

### 4.5 `DELETE /api/condor/me` — revoke

Credential-authenticated. Called when the admin disconnects a linked profile
(never for cancelling a login still in flight — that case has no hub call at
all; see §4.1 step 7). A `2xx` is read as "the hub's own copy of this token is
now revoked"; Condor does not require a specific response body to confirm
this, though returning one (e.g. `{"data": {"revoked": true}}`) costs the hub
nothing and gives the hub's own audit trail a consistent shape with §4.4.

Condor clears its local copy of the credential **unconditionally**, whether
this call succeeds, is refused, or the hub cannot be reached at all — a
disconnect must never leave an install stuck pretending to be connected because
the network dropped a request. What differs is only what the admin is told:
a successful revoke, a refusal or unreachable hub (the local credential is
still gone, but Botcamp's own copy may still be valid until it expires or is
revoked some other way), or nothing to revoke in the first place (there was no
token — e.g. only a pending login was cancelled).

### 4.6 Credential storage and use

The resulting `access_token` is what Condor's `Authorization: Bearer` header
carries on every subsequent **author-scoped** call — submit (§5.2), check
submission status (§5.3), unpublish (§8.1), and §4.4/§4.5 above; see those
sections for the exact paths, which are reconciled separately
([[TASK-010]]). It is stored in Condor's own `config.yml` under `hub.token`
(the field name predates this
document and is being kept rather than renamed; "token", "credential" and
"access token" name the same thing in this contract). It is never returned by
any Condor route to its own browser, never logged, and never placed in a URL.

A hub-side revocation (the admin revokes access from within Botcamp itself,
rather than through Condor's "disconnect") takes effect the next time the
token is used: any author-scoped call with a revoked token answers `401`,
which Condor's client reads as "the hub refused this token" and clears its
own copy — see §4.4.

---

## 5. Submission lifecycle

A submission is **not** a listing. It becomes one only after it clears both
deterministic checks (§6) and a human-gated review grants it a badge (§7).
Until then it is in a pending state that the public catalogue (§3) never
shows.

### 5.1 States

```
pending  ──(both deterministic checks pass, queued, human verdict: approve)──▶ published
   │
   └────(either deterministic check fails, OR human verdict: refuse)────────▶ rejected
```

There is no `pending` → `pending` loop and no re-review of a `published`
submission's same bytes — a new revision is a new submission (§5.4).

### 5.2 `POST /v1/agents` — submit

Authenticated with the author credential (§4.6).

Request body:

```json
{
  "slug": "adaptive_grid_trader",
  "agent_uid": "8f1e2c3a4b5d6e7f8091a2b3c4d5e6f7",
  "version": 3,
  "notes": "Added a drawdown guard to the grid loop.",
  "digest": "…sha256 of the bytes in `bundle`, computed by Condor…",
  "bundle": "{\"schema\":1,\"kind\":\"condor-agent-bundle\", ... the whole document from §1, as a JSON string ...}"
}
```

Notes on fields the hub must not take on trust:

- `slug`, `agent_uid`, `version`, `notes` are **echoes** of what `bundle`
  itself carries (Condor fills them from the same `Bundle` object it
  rendered into `bundle`). The hub MUST parse `bundle` and read these values
  from the parsed document for anything it records or displays — `name`,
  `description`, and `requires` are **only** available by parsing `bundle`;
  they are not sent as separate top-level fields at all.
- `digest` is the sha256 the author's own client computed over the exact
  bytes of `bundle`. The hub MUST **recompute** this independently
  (`sha256(bundle_bytes)`) and refuse the submission (`400`) if the submitted
  `digest` does not match what the hub itself computes — never store or serve
  a digest the hub has not itself verified against the bytes it will later
  return from §3.3. This is the one place a mismatched claim is cheap to
  catch before it becomes a promise to every future downloader.
- `author.handle` is **not** a field of this request at all. The hub attaches
  the authenticated account's own handle when constructing the eventual
  listing (§3.1's `author.handle`); a client-supplied handle must never be
  accepted from this body.

Response `202` (accepted for processing — not yet decided):

```json
{
  "submission_id": "sub_1a2b3c4d5e6f7081",
  "agent_uid": "8f1e2c3a4b5d6e7f8091a2b3c4d5e6f7",
  "slug": "adaptive_grid_trader",
  "version": 3,
  "status": "pending",
  "digest": "…same digest, now hub-verified…",
  "bytes": 48213,
  "submitted_at": "2026-10-21T12:03:00Z"
}
```

Response `400` — a **deterministic refusal** (§6), either check, decided
synchronously and never queued for review:

```json
{
  "status": "rejected",
  "reasons": [
    {
      "rule": "secret-scan",
      "detail": "skills/s/SKILL.md carries a 12-word recovery phrase and will not be queued for review. Remove it and resubmit."
    }
  ]
}
```

The response body for a deterministic rejection intentionally has **no**
`submission_id` — nothing was queued, and there is nothing a later `GET
/v1/submissions/{id}` could look up. See §6.3 for exactly what `reasons[].detail`
may and must not contain.

### 5.3 `GET /v1/submissions/{submission_id}` — check status

Authenticated with the **same author credential** that submitted it (the hub
must refuse a different author credential's attempt to read someone else's
submission — `403`, not `404`, so the author can tell "not mine" from "never
existed," though either is defensible; `403` is preferred since the id was
valid).

```json
{
  "submission_id": "sub_1a2b3c4d5e6f7081",
  "status": "pending",
  "submitted_at": "2026-10-21T12:03:00Z"
}
```

```json
{
  "submission_id": "sub_1a2b3c4d5e6f7081",
  "status": "rejected",
  "submitted_at": "2026-10-21T12:03:00Z",
  "decided_at": "2026-10-21T14:40:00Z",
  "reasons": [
    { "rule": "human-review", "detail": "Tool-steering language in skills/scalp/SKILL.md reads as an instruction to open leveraged positions on a connector this submission does not declare in requires.connectors." }
  ]
}
```

```json
{
  "submission_id": "sub_1a2b3c4d5e6f7081",
  "status": "published",
  "submitted_at": "2026-10-21T12:03:00Z",
  "decided_at": "2026-10-21T14:40:00Z",
  "listing_id": "lst_8f3a9c2e1b"
}
```

`404` if `submission_id` was never issued (i.e. the original `POST` hit a
deterministic refusal and never got one at all — that is by design, not an
error to work around).

### 5.4 Resubmission and idempotency

- **Retrying an identical `POST /v1/agents` body** (same `digest`, same
  author credential) before a decision is reached SHOULD return the existing
  `submission_id` rather than creating a duplicate queue entry — a client
  retry after a dropped connection must not multiply the review queue's work.
  Key this on `(author account, digest)`.
- **A new revision** (new bytes, new `digest`, same `agent_uid`) is always a
  **new submission** with its own `submission_id`, queued and reviewed from
  scratch. A prior `published` badge never carries forward — see §9 on why a
  badge is a claim about one revision, never about a lineage.
- **Changed bytes under a `submission_id` already decided** cannot happen
  through this API (there is no "edit a submission" endpoint — only
  "submit," which always mints fresh state), so the hub does not need to
  guard against it structurally. If an internal bug ever allows it, `409` is
  the right status, per §2.4's table.

---

## 6. Deterministic refusal checks

Two checks run, in order, **before** anything is queued for human review.
Both are deterministic — same bytes in, same verdict out, no model call, no
judgment — because a deterministic check is the one thing about a submission
the hub can *prove* ran, and a bundle that fails either one is not advisory
work for a human reviewer to repeat. Condor runs its own copy of
`condor.agents.bundle_review` on export as a courtesy to the author (so a
publish from the dashboard can say "this will be refused" before the network
call), but **the hub must reimplement both checks independently** — the
export-side run is advice to an author who controls their own exporter and is
never the check that decides anything on this side. Treat every submission
exactly as adversarial as an attacker who edited their own Condor's Python
before exporting.

### 6.1 Check 1 — structural validation

In order, refuse `400` on the first that fails (do not continue checking once
one has failed — later checks may assume the earlier ones held):

1. The body is valid UTF-8 and valid JSON.
2. `kind == "condor-agent-bundle"`.
3. `schema` is an integer the hub recognizes (currently: exactly `1`).
4. Every file in `files[]` has a non-empty `path`, a `sha256` that is 64 lowercase
   hex characters, and `text` that is a string (may be empty).
5. **Path containment.** No `path` may:
   - be absolute (start with `/`),
   - contain a `..` path segment (either literally or after normalizing `.`),
   - contain a NUL byte or any other control character,
   - duplicate another file's `path` exactly (case-sensitive) within the same
     submission.

   The failure mode this guards is a submission whose `files[]` could escape
   whatever directory the hub (or, later, an installing Condor) would write
   it under, or silently overwrite one file with another entry claiming the
   same name. Refuse the whole submission on the first offending path; name
   that path in the refusal detail (paths are not secret).
6. Every `files[].sha256` matches `sha256(text.encode("utf-8"))`, recomputed
   by the hub. A mismatch here means the submission is internally
   inconsistent — not necessarily malicious, but not safe to trust as
   described either.
7. The whole rendered body is **≤ 2,000,000 bytes** (§1.2).

### 6.2 Check 2 — secret scan

Scans the **text of every file** in the submission for four shapes, and
refuses the whole submission (`400`, never queued) if any **certain** finding
exists. The two certain shapes:

| Shape | Example (fixture, not a real key) |
|---|---|
| A BIP-39 recovery phrase: 12 or more consecutive words of 3–8 letters each, matched against the standard BIP-39 English wordlist (2048 words) | `legal winner thank year wave sausage worth useful legal winner thank yellow` |
| A Solana keypair exported as a JSON array of exactly 64 integers in `0..255` | `[174, 47, 154, 16, 202, 193, 206, 113, ... 64 total ...]` |

Two more shapes are scanned and **reported but never refuse on their own**
(they are ambiguous — a bare 64-hex string is equally a transaction hash or a
sha256 digest, and an 87–88 character base58 string is equally a Solana
transaction signature):

| Shape | Also commonly is |
|---|---|
| 64 hex characters, `0x` prefix optional | a tx hash, a sha256 digest |
| Base58, 87–88 characters | a Solana transaction signature |

A submission containing **only** the ambiguous shapes is not refused by this
check — it proceeds to the review queue, where a human can use the
surrounding context a regex cannot. A submission containing **any** certain
finding (a recovery phrase or a raw keypair array) is refused outright and
never reaches a human.

**The refusal must never include the secret text.** Name the file and, if
useful, the line — never the matched bytes, not even partially. This is the
one invariant that matters more than any other part of this check: a refusal
response is not a safer place for a recovery phrase than the submission it
is refusing.

```json
{
  "status": "rejected",
  "reasons": [
    {
      "rule": "secret-scan",
      "detail": "skills/wallet_helper/SKILL.md:9 carries a value shaped like a BIP-39 recovery phrase and will not be queued for review. Remove it and resubmit — nothing from this file was stored."
    }
  ]
}
```

### 6.3 Test vector

A fixture the hub's own test suite should assert against, end to end —
structurally valid, single file, with a planted secret:

```json
{
  "schema": 1,
  "kind": "condor-agent-bundle",
  "version": 1,
  "notes": "",
  "author": { "name": "", "handle": "" },
  "requires": { "connectors": [], "tools": [], "agent_key": "", "skills": { "carried": [], "inherited": [] } },
  "agent": { "slug": "fixture_agent", "name": "", "description": "", "when_to_consult": "", "agent_uid": "00000000000000000000000000000000" },
  "files": [
    {
      "path": "skills/s/SKILL.md",
      "sha256": "<sha256 of the text field below>",
      "text": "---\nname: s\ndescription: x\nwhen_to_consult: x\n---\nMy recovery phrase is: legal winner thank year wave sausage worth useful legal winner thank yellow\n"
    }
  ]
}
```

Expected behavior: `POST /v1/agents` with this body (wrapped per §5.2, with a
correct `digest`) answers `400`, `reasons[0].rule == "secret-scan"`,
`reasons[0].detail` names `skills/s/SKILL.md` and its line number, **no
`submission_id` is minted**, and the response body (and every log line the
hub writes about this request) contains none of the twelve words above.

### 6.4 Rule-set versioning

Both checks evolve. The hub reports which version of its rule set produced a
listing's `findings` in the listing's own `review_version` field (§3.1) — this
is **not** the AI review's version, it is this section's deterministic rule
set plus whatever human-gated review ran, bundled into one opaque string
Condor can compare across listings to notice when its own copy has drifted
from the hub's. Bump it whenever either check's matched shapes, path rules,
or size limits change in a way that could flip a verdict on bytes that didn't
change.

---

## 7. Review queue and reviewer API

Everything in §6 is enforced automatically before a submission is queued here
at all. What remains for a human (optionally AI-assisted, per [[TASK-005]]'s
brief — the AI pass is advisory input to the human, never a second gate that
can itself grant a badge) is **judgment**: does this agent's prose steer a
model toward a tool that moves money in a way that reads as hostile, do its
risk limits look reasonable for what it claims to do, does an endpoint it
dials look like it belongs to the operator or to a stranger. This is exactly
what Condor's own `bundle_review` reports as *findings* (§1's `findings`
field) — the reviewer's starting point, never their replacement.

### 7.1 Reviewer credential

A **separate credential scope** from the author credential in §4 — minted
through whatever account/authorization system the hub runs for its own
reviewer accounts (out of scope for this contract; the only requirement is
that it is never the same bearer value as an author's publish credential,
and that it cannot be obtained through the device-login flow in §4, which
only ever grants `agents:read`/`agents:publish` scope — see §4.3's `scope`
field). The review worker itself — whether
run by a human directly or orchestrated by MetaBrain on a human's behalf — is
explicitly out of this Condor-only filing's scope; this section defines only
the API surface such a worker calls.

### 7.2 `GET /v1/review/queue?page=`

Reviewer-authenticated. Lists `pending` submissions that have cleared both
deterministic checks, oldest first (so a worker draining the queue makes
visible progress rather than cherry-picking).

```json
{
  "submissions": [
    {
      "submission_id": "sub_1a2b3c4d5e6f7081",
      "agent_uid": "8f1e2c3a4b5d6e7f8091a2b3c4d5e6f7",
      "slug": "adaptive_grid_trader",
      "version": 3,
      "author_handle": "cardosofede",
      "digest": "…",
      "bytes": 48213,
      "submitted_at": "2026-10-21T12:03:00Z",
      "leased_by": null,
      "leased_until": null
    }
  ]
}
```

### 7.3 Lease semantics

A queue entry may be **leased** so two reviewers (or two runs of the same
automated worker) do not both verdict it:

- `POST /v1/review/queue/{submission_id}/lease` (reviewer-authenticated) —
  `200` with `{"leased_by": "<reviewer id>", "leased_until": "<timestamp>"}`
  on success; `409` if already leased by someone else and not yet expired.
  A lease has a bounded TTL (a few minutes is reasonable) after which it is
  available again — a worker that crashed mid-review must not permanently
  hold a submission hostage.
- A verdict (§7.4) from a reviewer who does not currently hold the lease is
  refused `409` — this is what makes "a reviewer cannot approve changed
  bytes behind another reviewer's back" actually hold, not just a policy
  stated in prose.

### 7.4 `POST /v1/review/queue/{submission_id}/verdict`

Reviewer-authenticated, and **idempotent on `(submission_id, digest)`** — a
retried verdict call for the same submission and the same bytes must not
double-apply (re-answer the original result rather than erroring, unless the
digest in the request no longer matches the submission's current digest, in
which case `409` — the bytes the reviewer looked at are not the bytes this
call is about to decide on).

```json
{
  "digest": "…must match the submission's current digest…",
  "decision": "approve",
  "badge": "validated",
  "findings_reviewed": true,
  "notes": "Reviewed the leverage skill's prose; it names the connector explicitly and matches requires.connectors."
}
```

or

```json
{
  "digest": "…",
  "decision": "refuse",
  "reasons": [
    { "rule": "human-review", "detail": "Tool-steering language in skills/scalp/SKILL.md reads as an instruction to open leveraged positions on a connector this submission does not declare in requires.connectors." }
  ]
}
```

On `approve`: the hub mints a `listing_id`, stores `published_at` as now,
sets the listing's `badge` to the submitted value (today, always
`"validated"` — a reviewer-approved submission has no path to a `"community"`
badge; `"community"` is reserved for whatever un-reviewed tier, if any, the
hub decides to offer later, which is explicitly **not** part of this
contract), and the submission's status flips to `published` with that
`listing_id` attached (visible from §5.3). On `refuse`: status flips to
`rejected` with the given `reasons`, visible from §5.3, and no listing is
ever created.

Record, durably, for every verdict: the reviewer's identity, which
deterministic rule-set version and which AI-assist version (if any) produced
the findings the reviewer saw, the findings themselves, and the decision
timestamp. This is what `review_version` (§3.1, §6.4) is derived from, and
it is the hub's own audit trail for "why was this badge granted" independent
of anything Condor ever sees.

---

## 8. Moderation and delisting

### 8.1 Author-initiated: `DELETE /v1/agents/{listing_id}`

Author-credential-authenticated (§4.6), scoped to listings published under
that same credential's account. `200` with `{"unpublished": true}`. `403` if
the credential does not own this listing. `404` if the listing is already
gone.

Effect: the listing immediately stops appearing in the future browse/get
endpoints this section's `listing_id` addresses (§3.1, §11.2) — **not** the
slug-keyed `GET /api/agents/{slug}` and `.../versions/{version}` §3.2/§3.3
implement today, which are a different identifier space entirely and are
unaffected by a `listing_id`-keyed unpublish until the hub unifies the two.
**This does not, and cannot, recall a copy some other install already
downloaded.** Condor's own user-facing documentation (`PRIVACY.md`) already
states this to the operator in those words; the hub's job is only to make the
*technical* half true — stop serving it, immediately and permanently — not
to attempt the impossible half.

### 8.2 Hub-initiated moderation

The hub may unpublish any listing and revoke any badge **unilaterally** — a
human-gated judgment call, author consent not required. There is no new
endpoint this contract mandates for it beyond "whatever admin tooling the
hub's own operators use internally produces the same effect as §8.1": the
listing stops being servable, immediately. What this contract **does**
require is that the action is logged, durably, with the actor, the reason,
and the time — the same audit trail §7.4 asks for on a verdict, because a
moderation action is a verdict reversed.

### 8.3 Tombstones

A `GET /v1/agents/{listing_id}` for a listing that existed and was later
removed (by either path above) SHOULD distinguish "never existed" from
"removed" if the hub wants to support a future Condor client warning an
install that something it has installed was later pulled. This contract does
not require Condor to consume that distinction today (no Condor client code
reads it yet), but recommends the hub's `404` body carry a machine-readable
reason when it has one — e.g. `{"detail": "...", "reason": "unpublished"}` vs.
`{"detail": "...", "reason": "not_found"}` — so that capability exists on the
hub side whenever a client version is written to use it. Do not block on
this; it is forward-looking, not a blocking requirement of this contract.

---

## 9. Trust model and what a badge is not

Stated here because every surface that renders a badge — Condor's dashboard,
and presumably the hub's own catalogue page — must carry the same three
sentences, verbatim in spirit if not in wording:

1. **A badge is a claim about one submitted revision, reviewed once.** It is
   not a signature, not a sandbox, and not an install permission. A
   `"validated"` listing installs on Condor's side through **exactly** the
   same gate a `"community"` one does — every routine and skill arrives
   muted, no imported `.py` file gets execution consent, nothing starts, and
   the operator reviews and unmutes item by item. Nothing in Condor's install
   path reads `badge` to decide anything; it is rendered as provenance and
   nothing more.
2. **The hub is a discovery and moderation service, not a security
   boundary.** The receiving Condor install is where the actual gate lives
   (FEAT-133/FEAT-144/FEAT-147, summarized in point 1). The hub's job is
   making sure nothing structurally broken or carrying a live secret ever
   becomes publicly listed, and giving a human reviewer's judgment a visible,
   auditable outcome. It is not making sure nothing bad is ever installable
   — that promise cannot be kept by any catalogue, and this contract does
   not pretend otherwise.
3. **Integrity between hub and client is a content digest over HTTPS, not a
   signature.** §3.3's `fetch` verifies the downloaded ZIP bytes against the
   `X-Agent-Sha256` header the **same response** carries, before anything
   parses them — there is no earlier listing call this cross-checks against,
   and no cryptographic signature anywhere in this contract. `author` and
   `author.handle` are claims the hub would attach at upload time, once the
   submission surface (§5) exists; they are not part of today's implemented
   `AgentListing` (§3.2, §11.1) at all, and no surface should render one as
   if a Botcamp lookup carries it today.

**Versioning and unknown-client tolerance.** `findings[].category` is a
closed set today (`secret`, `endpoint`, `tool_steer`, `code_run`,
`risk_limits`) but the hub should expect this set to grow — Condor's own
review module is where categories are defined, and a hub-side AI-assist pass
may surface its own advisory categories that a human reviewer sees but that
never need to reach a listing's `findings` at all (that field carries the
*deterministic* review's output, per §1's provenance note — an AI-assist
finding that influenced a human's `refuse` decision belongs in §7.4's
`verdict` audit trail, not fabricated into the published listing's
`findings`). An older Condor client reading a `findings` entry with an
unrecognized `category` string must not crash — pass it through and render
it generically. The hub should likewise tolerate an older bundle schema's
absent fields (all default to empty, per §1) rather than refusing a
submission for omitting something optional.

---

## 10. Rejected alternatives

Recorded here so the decision does not need re-litigating the next time
someone reads this contract wondering why it looks the way it does.

- **A redirect-based OAuth2 authorization-code grant back to Condor's
  dashboard**, instead of the device flow in §4. Rejected because `WEB_PORT`
  varies per install and many installs are not internet-reachable at all, so
  there is no stable redirect URI to register with an OAuth provider. The
  device flow needs no inbound reachability on Condor's side at all — it
  only needs to be able to make outbound HTTPS calls, which every install
  already does for the Hummingbot Backend API. (An earlier draft of this
  contract specified a bespoke polling pairing protocol, `/v1/link/*`,
  instead of the standard device grant; that was superseded once PR #111
  shipped the device-flow endpoints this section now documents, and no
  Condor release ever called the bespoke protocol against a live hub.)
- **A shared Python dependency between Condor and the hub for the review
  (§6).** Rejected because the hub is explicitly a separate codebase the
  Condor team may not deploy into, and because trusting an exporter's own
  copy of the review is the one thing this whole document exists to not do
  (§6's opening paragraph). The two checks are specified here in enough
  detail (exact shapes, exact path rules, exact size caps, a runnable test
  vector in §6.3) to be reimplemented from this document alone, in whatever
  language the hub's stack already uses.
- **AI review as the gate itself**, rather than advisory input to a human
  verdict. Rejected because an AI judgment is not reproducible the way §6's
  two checks are, and because the one thing a `"validated"` badge needs to
  mean is "a human looked at this," not "a model scored this below a
  threshold." The AI pass (however it is built, including a MetaBrain-driven
  one) feeds the same findings a human reviewer already sees from §6 and
  from Condor's own `bundle_review`; it does not get its own verdict
  endpoint, and §7.4 accepts only a human-attributable `decision`.

---

## 11. Appendix: field reference

### 11.1 `AgentListing` / `AgentVersion` (implemented — returned by §3.2)

`AgentListing`:

| Field | Type | Who sets it |
|---|---|---|
| `slug` | string | Echoed from the caller's own request path. |
| `title` | string | Hub. Maps to Condor's `name`. |
| `summary` | string | Hub. Maps to Condor's `description`. |
| `latest` | integer | Hub's own pick among `versions`; Condor never computes it. |
| `versions` | array of `AgentVersion` | `APPROVED` revisions only, newest first. |

`AgentVersion`:

| Field | Type | Notes |
|---|---|---|
| `version` | integer | |
| `sha256` | string (64 lowercase hex) | A hint for a version picker. **Not** what the download (§3.3) is checked against — that check never reads this field. |
| `size` | integer | |
| `fileCount` | integer | |
| `sourceRef` | string | |
| `createdAt` | string (ISO-8601 UTC) | |
| `manifest` | object | Hub's own coarse summary (name/agent_key/tools/loops, at the hub's discretion), rendered as a hint only. |

There is no `listing_id`, `agent_uid`, `author`, `badge`, `digest`, `bytes`,
`published_at`, `requires`, or `findings` on this read path. Those belong to
§11.2's `Listing` below — the prospective object the publish/review/
moderation surface (§5, §7, §8) would produce, not what
`GET /api/agents/{slug}` answers with today.

### 11.2 `Listing` (prospective — not returned by any implemented endpoint today)

The browse endpoint that would serve this shape (§3.1) is not built. This
table exists so the submission, review and moderation sections that already
reference these field names (§5, §7, §8) have one place defining them, and
so a future catalogue browse ([[TASK-004]]) has a target to build against.

| Field | Type | Who sets it |
|---|---|---|
| `listing_id` | string | Hub, at publish (§7.4 `approve`). Opaque, charset `[A-Za-z0-9._-]`, never `.` or `..`. |
| `agent_uid` | string | Echoed from the submitted bundle's `agent.agent_uid`. |
| `slug` | string | Echoed from the submitted bundle's `agent.slug`. |
| `name` | string | Parsed from the submitted bundle's `agent.name`. |
| `description` | string | Parsed from the submitted bundle's `agent.description`. |
| `version` | integer | Echoed from the submitted bundle's `version`. |
| `notes` | string | Echoed from the submitted bundle's `notes`. |
| `author.name` | string | Parsed from the submitted bundle's `author.name` (a claim). |
| `author.handle` | string | **Hub-attached**, from the account that authenticated the submission. Never client-supplied. |
| `badge` | `"validated"` \| `"community"` | Hub, at publish. Today always `"validated"` via this contract's one approval path (§7.4); `"community"` is reserved, not currently reachable through this API. |
| `digest` | string (64 lowercase hex) | **Hub-recomputed** from the submitted bytes (§5.2), never trusted from the submission as-is. |
| `bytes` | integer | Hub-measured length of the exact bytes a future unified download would return for this listing. |
| `published_at` | string (ISO-8601 UTC) | Hub, at publish. |
| `requires` | object | Parsed from the submitted bundle's `requires`, carried opaquely. |
| `findings` | array of `Finding` (§11.3) | **The hub's own run** of the deterministic review (§6) over the submission — never the author's export-side copy. |
| `review_version` | string | Hub, identifying which rule-set + review process produced `findings` and the badge (§6.4). |

### 11.3 `Finding`

| Field | Type | Notes |
|---|---|---|
| `category` | string | `"secret"`, `"endpoint"`, `"tool_steer"`, `"code_run"`, or `"risk_limits"` today; treat as an open set (§9). |
| `label` | string | The kind within the category — never a substring of the file, never the secret value. |
| `path` | string | Which file. |
| `line` | integer | 1-based. |
| `start` / `end` | integer | Byte offsets into that file's `text`, so a renderer can slice context itself if it has the file in hand — never pre-sliced into the finding by the hub. |
| `certain` | boolean | Only meaningful for `category: "secret"`; `false` for every other category. |
| `context` | string | Populated only for `tool_steer` (the matched line) and `risk_limits` (the figure); empty otherwise. **Never populated for a `secret` finding** — that category's whole point is never carrying the value. |

### 11.4 `Submission`

| Field | Type | Notes |
|---|---|---|
| `submission_id` | string | Minted only once both deterministic checks pass (§5.2). Never exists for a deterministic rejection. |
| `status` | `"pending"` \| `"rejected"` \| `"published"` | §5.1. |
| `digest` | string | Hub-recomputed, as `Listing.digest`. |
| `reasons` | array of `{rule, detail}` | Present only on `"rejected"`. `rule` is `"secret-scan"`, `"structural"`, or `"human-review"`. `detail` never carries a secret value (§6.2, §6.3). |
| `listing_id` | string | Present only on `"published"`. |

---

## Known gap in the current Condor client

Condor's existing hub client (`condor/hub.py`, shipped by FEAT-146 against a
stub transport, never against a real service) calls `POST /agents` expecting
an **immediate** `Receipt` — `listing_id`, `badge`, `published_at` — on the
same response, because it was written before this contract settled on a
pending-review queue. This document's §5 defines the correct target shape
(`202` with a `submission_id`, no listing until a human verdict lands), which
is a breaking change to that client's `publish()`/`Receipt` model. Updating
Condor's client to speak the submission/polling protocol is tracked
separately — it is Condor-side work this document does not perform, and it
does not block the hub team from implementing this contract as written.

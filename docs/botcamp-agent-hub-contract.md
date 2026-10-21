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
3. [Catalogue: browse, get, download](#3-catalogue-browse-get-download)
4. [Account pairing](#4-account-pairing)
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
  even starts) but this is advisory for the hub's own data integrity — the
  one digest the client actually verifies (§3.3) is the digest of the whole
  rendered document, not of an individual file.

### 1.2 Limits

- **Maximum rendered size: 2,000,000 bytes** of UTF-8 JSON. A submission over
  this size is refused with `413` before anything else runs. Condor's own
  client reads at most this many bytes from a download reply before giving up
  on the digest check, so a hub that served more would simply make every
  install-side download fail a digest comparison — there is no reason to
  implement that unless the hub is deliberately refusing in a way clients can
  diagnose, which is exactly what the explicit `413` gives them instead.
- **Catalogue page replies (browse, one listing) are capped at 1,000,000
  bytes** by the client; see §3.1.
- The whole document must be valid UTF-8 JSON. A submission that is not valid
  UTF-8, or not valid JSON, is refused `400` before any field is read.

---

## 2. API base, transport rules, and errors

### 2.1 Base URL

```
https://hub.botcamp.xyz/v1
```

Condor compiles this in as a constant (`condor/hub.py`'s `HUB_URL`) and ships
no environment variable or setting that redirects it. This is a deliberate
SSRF defense, not an oversight to design around: a server-side fetch of a
**user-supplied** URL is the surface Condor refuses to build; a fetch of a
**compiled-in** one, to a single path segment that has already passed an
identifier check, is not. Two consequences for the hub's design:

- **Never require a redirect.** Condor's HTTP client refuses every redirect
  response outright (it raises rather than following a `3xx`), on every verb,
  including the bundle download. If a future CDN or load balancer in front of
  the hub issues a `302`, every Condor install breaks at once. Terminate
  redirects before they reach the client, or don't use them.
- **The path prefix is part of the contract.** Every endpoint below is
  relative to `/v1`. A `v2` is a new base Condor would need a release to pick
  up — version the API by path, not by header, so an old client simply keeps
  working against `/v1` indefinitely.

### 2.2 Identifiers in a URL path

Every `listing_id`, `submission_id`, and `link_id` this contract defines is
validated by Condor against the charset `[A-Za-z0-9._-]` **before any request
is built**, and `.` / `..` alone are refused as values even though they match
that charset. Two things follow for the hub:

- **Mint identifiers from that charset.** An opaque id containing `/`, `?`,
  `#`, or whitespace cannot be used by the Condor client at all — it will
  never reach the hub, because Condor refuses to build the request locally.
  A UUID4, a ULID, or a base32/base62 string are all safe choices.
- **Never answer with a different id than was asked for.** `fetch` (§3.3)
  compares the id it requested against the id the listing it already holds
  describes, and refuses the whole exchange on a mismatch. Don't try to be
  helpful by normalizing or redirecting a near-miss id — answer `404`.

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
| `400` | The caller's request was malformed, or (publish) a deterministic refusal. Not retried automatically. |
| `401` / `403` | The credential is missing, wrong, or revoked. Rendered to the admin as "set a new one in Settings." |
| `404` | The thing named in the path does not exist (or no longer does). |
| `409` | A conflict Condor's own retry logic does not resolve by itself — reserved for the pairing replay case (§4.4) and a submission resubmitted with changed bytes under an id already decided (§5.4). |
| `413` | The body exceeds a size limit named in this contract. |
| `429` | Rate limited. Include `Retry-After` if you can; Condor does not currently read it, but a future client version will, and it costs the hub nothing to send now. |
| `5xx` | Upstream failure. Condor's web layer turns this into its own `502` to the dashboard, i.e. it is reported as "the hub is unreachable," not attributed to the operator. |

A transport failure (connection refused, timeout, TLS failure, a redirect)
never reaches this table at all — Condor's client raises locally with no
status before any of the above applies.

---

## 3. Catalogue: browse, get, download

Every endpoint in this section is **anonymous**. No credential is accepted or
required, and none should be requested — the public catalogue is Botcamp's
one surface with no login wall, by design (most Condor installs will browse
and install long before anyone on the box has a Botcamp account at all).

### 3.1 `GET /v1/agents?query=&page=`

Lists published listings, newest first.

- `query` (optional, default `""`) — free text, URL-encoded by the client.
  Match it however the hub likes (title, description, slug); it is never used
  to address a resource, so it needs no identifier validation on the hub
  side either.
- `page` (optional, default `1`, 1-indexed) — the client clamps any value
  below 1 up to 1 before sending.

Response, `200`, capped at **1,000,000 bytes**:

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
      "digest": "…64 hex sha256 of the exact bytes the bundle download returns…",
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

No pagination metadata (no `total`, no `has_more`) is required — Condor reads
only the `listings` array and stops paging when a page comes back empty or
shorter than the hub's own page size. Return however many rows you like per
page; there is no contractual minimum or maximum, though something in the
range of 20–50 is a reasonable default. **Do not include a `"trust"` key or
any other top-level key** — that field is appended by Condor's own web layer
from a sentence it owns, not read from the hub's response, and a hub-supplied
one is simply ignored (cheap to omit, worth stating so nobody wonders why
their version is discarded).

One malformed row does not fail the whole page on Condor's side — it is
logged and dropped — so if a defensive posture is wanted, the hub can lean on
this slightly rather than needing the whole listings table to be perfectly
clean before this endpoint can go live. That said, do not rely on it: a
`listing_id` is still required on every row, or that row's `listing_id` ends
up empty and useless to the reader.

### 3.2 `GET /v1/agents/{listing_id}`

One listing, in the shape of exactly one entry of §3.1's `listings` array —
not wrapped in another `listings` key, just the object itself:

```json
{
  "listing_id": "lst_8f3a9c2e1b",
  "agent_uid": "…",
  "slug": "adaptive_grid_trader",
  "...": "... (same shape as one row of §3.1)"
}
```

- `404` with `{"detail": "..."}` if `listing_id` does not exist or was
  unpublished (§8).
- Capped at the same **1,000,000 bytes**.

### 3.3 `GET /v1/agents/{listing_id}/bundle`

Returns the **raw bundle bytes**, not JSON — the exact UTF-8-encoded document
described in §1, served with no transformation. `Content-Type:
application/json` is correct for it but Condor's client does not actually
check it; it only checks the bytes.

- Capped at **2,000,000 bytes** of response body.
- `404` if the listing does not exist or was unpublished.
- The response's byte-for-byte sha256 **must equal** the `digest` field the
  matching `GET /v1/agents/{listing_id}` call reports. This is the entire
  integrity mechanism between hub and client — there is no signature, only a
  content digest over HTTPS. Condor's client:
  1. Fetches the listing first (or reuses one already in hand) and reads its
     `digest`.
  2. Refuses to download at all if that `digest` is empty — a hub that cannot
     say what it is serving is treated as offering no integrity, not as
     trusted by default.
  3. Downloads the bundle, computes its own sha256 over the exact bytes
     received, and refuses the whole exchange (raising before anything
     parses the document) if the two hashes disagree.

  **The hub must therefore serve, byte for byte, the document whose sha256
  equals the `digest` it reports for that `listing_id` — always**, even
  across a hub restart, a CDN cache, or a database migration. If a listing's
  underlying bytes ever need to change, that is a new submission with a new
  `listing_id` (or a new version sharing the `agent_uid`), never an edit of
  bytes already served under one.
- Never redirect. Never gzip in a way the client doesn't transparently
  decode to the original bytes before the digest check — if compression is
  used at the HTTP layer, it must be content-encoding that the client's HTTP
  stack already reverses (standard `gzip`/`deflate` `Content-Encoding` is
  fine; serving a *different* compressed document and expecting the client to
  decompress it itself is not).

---

## 4. Account pairing

Publishing and withdrawing act under one Botcamp account's identity, and that
identity is **install-wide** on the Condor side — one credential per Condor
install, usable only by that install's admin, with no per-user identity inside
Condor yet (deferred). The design problem this section solves: Condor's web
dashboard runs on an operator-chosen `WEB_PORT` that varies per install and is
frequently not even internet-reachable, so a standard OAuth redirect back to
the dashboard has no stable URI to register. See §10 for why OAuth and a
pasted token were both considered and rejected in favor of this flow.

### 4.1 Flow, end to end

1. Condor's backend calls `POST /v1/link/start` with **no credential** (there
   isn't one yet).
2. The hub mints a `link_id` (opaque, used only for polling — never shown to
   a human), a short `code` (used in the URL Condor opens), and a longer
   `verification` string (shown to the human on both ends, to resist a
   phished `code`). It returns all three plus an expiry.
3. Condor's dashboard opens `https://hub.botcamp.xyz/v1/link?code=<code>` in
   a new browser tab and shows the **same** `verification` string beside an
   "open Botcamp" button, so the admin can compare the two strings and not
   just click through blind.
4. On that page, the hub requires the human to already be signed into
   Botcamp (or sign in first), shows the `code`'s `verification` string again
   for the human to compare against what Condor showed, and offers Accept /
   Deny. This page itself is out of this contract's scope beyond "it must
   repeat the verification string before Accept" — it is server-rendered UI,
   not an API response Condor parses.
5. Condor's backend polls `GET /v1/link/{link_id}` (no credential — the
   point of the flow is that none exists yet) at a bounded interval until it
   sees a terminal state.
6. On `accepted`, that **one** poll response carries the credential and the
   linked profile handle. Condor stores the credential server-side,
   immediately, and never sends it to its own browser. Every later poll of
   that same `link_id` must **not** repeat the credential (§4.4) — a leaked
   `link_id` does not reveal the credential a second time.
7. Condor's backend may call `DELETE /v1/link/{link_id}` at any point before
   acceptance to cancel its own pending attempt (e.g. the admin navigated
   away, or started a new link).

### 4.2 `POST /v1/link/start`

Request body: none required (an empty JSON object `{}` is fine).

Response `200`:

```json
{
  "link_id": "lnk_3f8c1a9e2b7d4f60",
  "code": "WXYZ-4821",
  "verification": "porcupine-harbor-violet-trellis",
  "expires_at": "2026-10-21T12:10:00Z",
  "poll_interval_s": 3
}
```

- `code` is short because it rides in a URL an admin may need to retype on a
  second device; it is **not** the secret — `verification` is the thing a
  human actually compares, and `code` alone must not be sufficient to approve
  a link (a code that leaked into a browser history or a screen share must
  not approve anything by itself without the human seeing and confirming the
  matching `verification` string).
- `expires_at` SHOULD be short — 10 minutes is a reasonable default. A link
  that is still `pending` past this time answers `expired` on every
  subsequent poll (§4.3), never `accepted`.
- **Rate limit `POST /v1/link/start` per source** (IP, or whatever the hub
  already keys abuse limits on) — it is the one pairing endpoint that needs
  no credential to call at all, which makes it the cheapest one to hammer.
- **One active pairing per install.** If Condor starts a second `link/start`
  while an earlier `link_id` is still `pending`, the hub is not required to
  track which Condor install issued which `link_id` (it has no way to know
  yet) — Condor's own backend is responsible for invalidating its *local*
  memory of the previous attempt and simply stops polling it. The hub does
  not need to implement "only one pending link per install" itself; it has no
  install identity to key that on before acceptance.

### 4.3 `GET /v1/link/{link_id}`

No credential. Response `200` always (a `link_id` the hub never issued, or
one already fully consumed and forgotten, is the one case to answer `404`
instead — see §4.4):

```json
{ "status": "pending", "expires_at": "2026-10-21T12:10:00Z" }
```

```json
{ "status": "denied" }
```

```json
{ "status": "expired" }
```

```json
{
  "status": "accepted",
  "profile_handle": "cardosofede",
  "credential": "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60",
  "credential_scope": "author"
}
```

- `credential_scope` is always `"author"` for this endpoint — a pairing never
  grants the reviewer scope (§7.1), which is minted differently and never via
  this flow.
- **One-time delivery.** The very next poll of the same `link_id` after an
  `accepted` response was already returned must come back **without** the
  `credential` field (keep `status: "accepted"` and `profile_handle`, drop
  `credential`), or the hub may choose to answer `404` for that `link_id`
  from that point on. Either is acceptable; what is not acceptable is
  answering the credential a second time. This is the pairing flow's replay
  defense: a `link_id` is not a secret (it rode in a server-to-server poll,
  but it could still end up in a log), and the actual credential must not be
  extractable twice from it.
- **Poll rate limiting.** Reject polls faster than roughly `poll_interval_s /
  2` for one `link_id` with `429`. Condor's backend is the only caller and
  honors `poll_interval_s`; this guards against a bug or a malicious replay
  hammering the endpoint, not against a well-behaved client.

### 4.4 `DELETE /v1/link/{link_id}`

No credential. Cancels a `pending` link. `200` with `{"cancelled": true}` on
success. `409` with `{"detail": "..."}` if the link was already `accepted` —
cancelling a link whose credential may already be in flight to Condor's
backend is a race the hub should refuse rather than silently resolve either
way, and Condor's backend treats a `409` here as "assume it went through,"
not as an error to surface.

### 4.5 Credential storage and use

The resulting `credential` is what Condor's `Authorization: Bearer` header
carries on every subsequent **author-scoped** call — `POST /v1/agents`
(§5.2), `GET /v1/submissions/{submission_id}` (§5.3), `DELETE
/v1/agents/{listing_id}` (§8.1). It is stored in Condor's own `config.yml`
under `hub.token` (the field name predates this document and is being kept
rather than renamed; "token" and "credential" name the same thing in this
contract). It is never returned by any Condor route to its own browser, never
logged, and never placed in a URL.

A hub-side revocation (the admin revokes access from within Botcamp itself,
rather than through Condor's "disconnect") takes effect the next time the
credential is used: any author-scoped call with a revoked credential answers
`401`, which Condor's client already renders as "the hub refused this token,
set a current one."

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

Authenticated with the author credential (§4.5).

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
and that it cannot be obtained through the pairing flow in §4, which only
ever mints `credential_scope: "author"`). The review worker itself — whether
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

Author-credential-authenticated (§4.5), scoped to listings published under
that same credential's account. `200` with `{"unpublished": true}`. `403` if
the credential does not own this listing. `404` if the listing is already
gone.

Effect: the listing immediately stops appearing in `GET /v1/agents` (§3.1)
and `GET /v1/agents/{listing_id}` / `.../bundle` (§3.2, §3.3) start answering
`404`. **This does not, and cannot, recall a copy some other install already
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
   signature.** §3.3's digest check is the entire guarantee that what a
   client downloads is what a listing claims. `author` and `author.handle`
   are claims the hub attaches at upload time and nothing cryptographically
   binds them to the bytes — every surface rendering them must say so (and
   Condor's dashboard already does).

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

- **OAuth redirect back to Condor's dashboard**, instead of the polling
  pairing flow in §4. Rejected because `WEB_PORT` varies per install and many
  installs are not internet-reachable at all, so there is no stable redirect
  URI to register with an OAuth provider. The polling flow needs no inbound
  reachability on Condor's side at all — it only needs to be able to make
  outbound HTTPS calls, which every install already does for the Hummingbot
  Backend API.
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

### 11.1 `Listing` (returned by §3.1, §3.2, and embedded in §7.2)

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
| `bytes` | integer | Hub-measured length of the exact bytes `GET .../bundle` returns. |
| `published_at` | string (ISO-8601 UTC) | Hub, at publish. |
| `requires` | object | Parsed from the submitted bundle's `requires`, carried opaquely. |
| `findings` | array of `Finding` (§11.2) | **The hub's own run** of the deterministic review (§6) over the submission — never the author's export-side copy. |
| `review_version` | string | Hub, identifying which rule-set + review process produced `findings` and the badge (§6.4). |

### 11.2 `Finding`

| Field | Type | Notes |
|---|---|---|
| `category` | string | `"secret"`, `"endpoint"`, `"tool_steer"`, `"code_run"`, or `"risk_limits"` today; treat as an open set (§9). |
| `label` | string | The kind within the category — never a substring of the file, never the secret value. |
| `path` | string | Which file. |
| `line` | integer | 1-based. |
| `start` / `end` | integer | Byte offsets into that file's `text`, so a renderer can slice context itself if it has the file in hand — never pre-sliced into the finding by the hub. |
| `certain` | boolean | Only meaningful for `category: "secret"`; `false` for every other category. |
| `context` | string | Populated only for `tool_steer` (the matched line) and `risk_limits` (the figure); empty otherwise. **Never populated for a `secret` finding** — that category's whole point is never carrying the value. |

### 11.3 `Submission`

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

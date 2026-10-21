---
id: TASK-005
kind: action
title: Write the Botcamp hub contract for the external team and resolve TASK-004's owner decisions
summary: Condor's hub client exists but Botcamp has no buildable service contract. Write a self-contained handoff for hummingbot/botcamp-site covering pairing, submission review, the public catalogue, reviewer verdicts and delisting; record the settled decisions against TASK-004.
status: done
brain: developer
actor: session
effort: M
risk: medium
files:
  - condor/hub.py
  - condor/agents/bundle.py
  - condor/agents/bundle_review.py
  - condor/web/routes/agents.py
  - condor/web/routes/settings.py
  - config_manager.py
depends_on: []
commits:
  - "a9de6bf5 docs(hub): write the Botcamp hub contract for the external team (TASK-005)"
created: 2026-10-21
---

## Goal

Create a handoff document in this Condor repository for the team building the hub in hummingbot/botcamp-site. TASK-004 already asks for a live round trip and is blocked on decisions this document settles. This action writes the contract and records that decision block as resolved; the outside team still owns implementation and the live round trip.

## Steps

1. Write docs/botcamp-agent-hub-contract.md as a standalone normative contract. State that Condor publishes one portable .condor-agent.json made from publishable_files over AGENT.md, skills/, routines/, controllers/, loops/*/loop.md and shutdown.md. Runtime state, memory and mutes do not travel. Identify the existing schema, kind, limits and wire fields from condor/agents/bundle.py; do not make the hub depend on Condor's Python package.
2. Specify https://hub.botcamp.xyz/v1 as the sole API base. Preserve the existing anonymous GET /agents?query=&page=, GET /agents/{listing_id}, GET /agents/{listing_id}/bundle, authenticated POST /agents and DELETE /agents/{listing_id}. Include request and response examples, HTTP errors, pagination and id rules. Listing must carry listing_id, agent_uid, slug, name, description, version, notes, author.handle, badge, digest, bytes, published_at, requires, findings and review_version. Bundle download uses the same listing id, host and bytes digest, with no redirect.
3. Specify install-wide account pairing: Condor starts a link, receives a short-lived single-use code plus verification string and polling handle, opens https://hub.botcamp.xyz/v1/link?code=... in a tab, and polls from its backend until the signed-in operator accepts. The Accept page repeats the verification string to resist code phishing. The credential arrives only in the backend poll response; no token in URL, fragment, browser response or log. Define expiry, denial, replay, cancellation, polling interval and rate limits. One linked profile owns publishing and withdrawal; Condor restricts link/publish/unpublish to admins. Per-user profiles are deferred.
4. Specify the submission lifecycle separately from listings. POST /agents returns a Receipt with submission_id and pending | rejected | published state; the author can GET /submissions/{submission_id} with the publish credential. A rejection includes safe, file-named reasons; pending never appears in public browse; only a reviewed, badged submission becomes a listing. Define idempotency for retry, immutable submitted bytes and how an existing agent_uid/version is updated.
5. Specify Botcamp's two mandatory independent deterministic refusal checks before queueing AI review. Structure: schema and kind, required fields, a well-formed agent layout, path containment and no duplicate/escaping paths, per-file sha256, UTF-8 and size caps. Secret scan: portable rules for key-like values, BIP-39 recovery phrases and suspicious endpoint hosts across carried text; refuse and name the file without echoing a secret. State rule-set version and test vectors, including a well-formed bundle with a planted key. Botcamp reimplements these checks in its own stack. AI review is advisory, works only surviving pending submissions, and cannot waive a deterministic refusal.
6. Specify the review queue and MetaBrain-facing reviewer API: list pending submissions with pagination/lease semantics; fetch the exact submitted bundle with digest; POST an idempotent verdict tied to submission id and digest, using a reviewer credential with scope separate from author tokens. Record reviewer identity, rule-set and AI review versions, findings and decision time. Define how advisory findings reach a human reviewer and who grants or refuses the validated badge. A reviewer cannot approve changed bytes. Only a granted badge publishes; a rejection stays visible only to the author.
7. Specify moderation: Botcamp may unpublish any listing and revoke any badge unilaterally, logs actor, reason and time, removes it from browse and denies future bundle fetches. State explicitly that this stops future installs but cannot recall a downloaded copy. Define a status/tombstone response the client can later use to warn existing installs.
8. State the trust model prominently: a badge records a review of one revision, not a sandbox or install permission. Condor still verifies the listing digest and runs the receiving install's preview, findings, mute and per-file execution-consent gates. Include a versioning rule for additive finding kinds and unknown-client tolerance. Explain the chosen pairing flow against variable WEB_PORT redirect URIs, and explain why a shared Python review dependency and AI-as-gate were rejected.
9. Update TASK-004's decision notes or status so its five owner decisions point to this contract and no longer claim to be undecided. Keep the action blocked only on the external team's service deployment and authorized live round trip; do not claim that writing the contract has stood up the hub.

## Acceptance criteria

- [x] The handoff names hummingbot/botcamp-site as service owner and is sufficient for that team to implement the endpoints, security checks, state transitions and reviewer API without reading FEAT-133 through FEAT-147. `docs/botcamp-agent-hub-contract.md`'s opening section states the audience and scope explicitly and is self-contained end to end (§1–§11).
- [x] It contains concrete success and refusal examples for pairing, a pending submission, deterministic rejection, reviewer verdict, publication and delisting, with credential scope and response status stated. `docs/botcamp-agent-hub-contract.md` §4.2–4.4 (pairing), §5.2 (submission `202`/`400`), §6.2–6.3 (deterministic rejection), §7.4 (reviewer `approve`/`refuse`), §8.1 (delisting `200`/`403`/`404`).
- [x] A fixture bundle that is structurally valid but contains a key or BIP-39 phrase is rejected before entering the review queue; the contract names the file and never returns the secret bytes. `docs/botcamp-agent-hub-contract.md` §6.3 ("Test vector").
- [x] TASK-004's resolved decisions cite the contract, while its remaining external deployment and live verification are represented truthfully. TASK-004 amended (revision 2): `## Decisions (resolved by TASK-005)` section added, `blocked_reason` updated to name only the external deployment and live round trip as the remaining block.
- [x] The document and TASK-004 edit are committed on this workstream; no code in hummingbot/botcamp-site is changed from this repository. Doc committed as `a9de6bf5`. TASK-004 is a db-backed row in this project (no file materialized in this worktree); `edit_backlog_doc` persisted the edit as its revision 2, which is that doc's own commit-equivalent. No file outside this Condor checkout was touched.

## Notes

FEAT-146 shipped Condor's publish, browse and install client and deliberately left the outside service to TASK-004. The existing client treats the badge as provenance and keeps the import gate local. The contract must preserve that property while requiring review before a submission is publicly listed. The paired credential removes the pasted-token UX without a redirect URI. The review worker itself belongs to MetaBrain and is outside this Condor-only filing scope.

Condor's own `condor/hub.py` client (FEAT-146, built against a stub) does not yet speak this contract's submission/pending protocol — its `publish()`/`Receipt` assumes an immediate listing. The contract's closing section ("Known gap in the current Condor client") names this; updating that client is separate Condor-side work this action did not perform.

---
id: FEAT-150
kind: feature
title: Show the author's submission state from receipt through review
summary: Condor's publish Receipt assumes a listing exists immediately, so an author cannot track a pending or rejected submission. Carry submission id and pending, rejected or published state, poll the author's submission with the linked credential, and show truthful review status in the dashboard.
status: todo
brain: developer
effort: M
risk: medium
new_files: []
touched_files:
  - condor/hub.py
  - condor/web/routes/agents.py
  - frontend/src/lib/api.ts
  - frontend/src/components/settings/AgentsSettings.tsx
  - tests/test_hub.py
depends_on:
  - TASK-005
  - FEAT-148
commits: []
created: 2026-10-21
---

## Objective

After publishing an agent, its author sees whether Botcamp still has it pending, rejected it with reasons, or published it with a badge and listing id. A pending submission is not represented as a public listing. Condor uses the linked install-wide author credential from [[FEAT-148]] to read the private status contract in [[TASK-005]].

## Context and constraints

condor/hub.py's Receipt currently carries listing_id, badge and published_at, which presumes POST /agents immediately creates a browseable listing. The current dashboard shows a publish result but no in-flight review. The public browse() path is anonymous and must continue returning only reviewed, badged listings; the receiving install's digest, preview, mute and execution-consent checks remain in force. Botcamp owns the queue and badge decision. Condor must not synthesize a published listing from a pending receipt.

## Alternatives considered

- Return a provisional Listing with badge community: falsely exposes unreviewed submissions to public browse and changes the meaning of a listing.
- Hold the publish request open until review finishes: ties an interactive request to a potentially long human queue and exceeds the synchronous client's bounded call budget.
- Return a durable Receipt and poll private submission status: makes the queue visible to its author without widening the public catalogue. Choose this.

## Decision

Extend Receipt with submission_id and state: pending | rejected | published. A published receipt may include listing_id, badge and published_at; a pending or rejected receipt need not. A rejected status carries a human-readable verdict and file-named deterministic findings without secret bytes. Condor stores enough of the receipt to resume status after a dashboard reload, and polls GET /submissions/{submission_id} through its backend using the linked author credential. Public browse remains listing-only.

## Design

- Add a typed SubmissionState and status model to condor/hub.py. Validate a returned submission id as one URL path segment before any request. Use the existing _send seam, timeout, response-body cap and refusal mapping; refuse redirects. The status read must never send the credential to a caller-selected host.
- Make the publish result truthful: return accepted/pending, rejected or published according to the hub response. No branch may claim published solely because POST /agents returned 2xx. Preserve the exact bundle bytes and digest already submitted by hub.publish.
- Add an authenticated Condor route for reading the current admin's submission status. Keep the author credential server-side and make private status admin-only, matching publish and pairing. Poll at a bounded interval while pending; stop on a terminal state, logout or unmount. Distinguish network failure from a rejected verdict and allow retry of status without re-upload.
- Update the dashboard and API types to render Pending review with the submission id, Rejected with safe reasons, or Published with the listing link and badge. Persist only the non-secret receipt metadata needed across reloads. A newer upload has a new submission id; the earlier record must not be silently overwritten as though it were the same review.
- Keep hub.browse and HubBrowser listing-only. A rejected or pending submission must not be installable through the public hub, while the existing import-from-file path remains available under its own local gate.

## Implementation plan

1. Extend hub response models and add the authenticated status call.
2. Add the backend status route and durable, non-secret receipt state for the install's own submissions.
3. Update dashboard API types and publish UI for pending, rejected and published states.
4. Test every transition, page reload and status failure; test that pending and rejected submissions are absent from browse and that the author credential never reaches the browser.

## Acceptance criteria

- [ ] POST /agents returning pending produces a Receipt with submission_id and state pending; Condor does not show a listing id, badge or Published message until the status reaches published.
- [ ] The admin can poll the same submission after a dashboard reload and see published listing metadata or a rejected verdict with file-named, secret-free reasons. A transient status network error does not cause a re-upload.
- [ ] A non-admin cannot read the install's private submission status or author credential; the browser never receives the credential.
- [ ] Public browse and install expose only the hub's published, badged listings. A pending or rejected id is never substituted into a listing fetch.
- [ ] Focused hub, route and frontend tests cover pending-to-published, pending-to-rejected, terminal-state polling stop and failure recovery, with no new failures relative to the merge base.

## Risks and notes

The review is performed on Botcamp's immutable submitted bytes. The listing badge records provenance of one revision and does not replace Condor's local import gate. The linked credential is install-wide and admin-only; this item does not introduce per-user submissions or run the MetaBrain review worker.

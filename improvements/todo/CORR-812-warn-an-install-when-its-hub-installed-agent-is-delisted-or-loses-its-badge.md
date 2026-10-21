---
id: CORR-812
kind: improvement
title: Warn an install when its hub-installed agent is delisted or loses its badge
summary: Condor installs a hub bundle but does not retain and check listing moderation state, so an operator may keep treating a delisted agent as reviewed. Record hub provenance at install and update, then surface a warning when Botcamp pulls the listing or revokes its badge without deleting local files.
category: correctness
impact: medium
effort: M
risk: medium
status: todo
brain: developer
files:
  - condor/hub.py
  - condor/web/routes/agents.py
  - condor/agents/bundle.py
  - frontend/src/components/settings/HubBrowser.tsx
  - frontend/src/lib/api.ts
depends_on:
  - FEAT-150
commits: []
created: 2026-10-21
---

## Problem

A hub-installed agent is locally usable after download. FEAT-145's update path reasons about agent_uid and bundle version, but Condor does not retain a durable association to the listing and its badge. Botcamp may later unpublish a listing or revoke its badge. Delisting prevents future hub downloads, but cannot recall files already installed. Without a status check, the install never tells its operator that the hub withdrew the listing or its review claim. A 404 may also mean a transient or malformed upstream response, so a warning must distinguish confirmed moderation from an unavailable hub.

## Proposed solution

Record non-secret hub provenance when a listing is installed or updated: listing id, agent_uid, installed digest, badge seen at install and the last successful status check. Keep it outside portable publishable_files so it does not travel in a re-exported bundle. Extend the hub client with the listing or tombstone status defined in [[TASK-005]], through the compiled-in host, validated id and no-redirect _send path. After [[FEAT-150]] defines the reviewed listing lifecycle, check status when the operator views the installed agent and during the existing update-availability flow, with bounded caching so a page does not fan out repeated hub calls. A definite delisted or badge-revoked response marks the local provenance as changed and displays a clear warning; a network error displays an unknown/check-failed state. Never delete or disable a local agent from a remote status alone.

The warning says which listing was affected, the previous badge, current hub state and last checked time. It states that downloaded copies remain on this install and that the operator must review the agent and decide whether to keep, mute or remove it. A badge change does not bypass or retroactively alter local import review, mutes or per-file execution consent.

## Acceptance criteria

- [ ] Installing or updating from a hub listing records listing id, agent_uid and installed digest in local non-portable provenance; exporting the agent does not include that record.
- [ ] A hub response that explicitly marks the listing delisted or its badge revoked produces a visible warning for an already installed agent, including the last successful check time. No future install or bundle fetch is offered for a delisted listing.
- [ ] A transient network failure, 5xx or malformed response is shown as status unknown and does not claim moderation occurred; the local files and execution state are unchanged in either case.
- [ ] A listing with an unchanged badge remains in its ordinary state. Tests drive unchanged, delisted, badge-revoked and unavailable responses through the hub seam and verify the UI language.
- [ ] The status request uses the compiled-in host, validates the listing id and refuses redirects; no arbitrary URL or listing-supplied path is fetched.
- [ ] Focused hub, route and frontend checks pass with no new failures relative to the merge base.

## Notes

Botcamp has unilateral moderation and logs it under [[TASK-005]]. This improvement gives an existing install notice of that decision; it cannot recall a download and should not pretend to. The validated badge remains provenance, never an import or runtime permission check. It depends on [[FEAT-150]] because that item establishes the reviewed listing state and separates a submission from a public listing.

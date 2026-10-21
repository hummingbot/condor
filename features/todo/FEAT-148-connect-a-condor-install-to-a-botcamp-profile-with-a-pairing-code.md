---
id: FEAT-148
kind: feature
title: Connect a Condor install to a Botcamp profile with a pairing code
summary: Publishing currently requires an admin to paste a Botcamp token. Add an admin-only Connect flow that starts a short-lived pairing, opens Botcamp's approval page, polls from Condor's backend and stores the resulting install-wide credential without exposing it to the browser.
status: todo
brain: developer
effort: L
risk: high
new_files: []
touched_files:
  - condor/hub.py
  - condor/web/routes/settings.py
  - condor/web/routes/agents.py
  - config_manager.py
  - frontend/src/lib/api.ts
  - frontend/src/components/settings/AgentsSettings.tsx
  - tests/test_hub.py
depends_on:
  - TASK-005
commits: []
created: 2026-10-21
---

## Objective

An admin clicks Connect to Botcamp, sees a verification string, accepts the matching request on Botcamp in a browser tab, and Condor is linked to that profile without copying a token. Publishing and withdrawing then use the stored install-wide credential. Browsing remains public. The hub owns identity and pairing approval; Condor only calls its contract in [[TASK-005]].

## Context and constraints

FEAT-146 stores one install-wide hub token in config.yml and allows only the admin to set, publish or unpublish with it. Its paste-a-token design rejected OAuth because WEB_PORT varies between installs, making a stable redirect URI impossible. The hub client is synchronous urllib through _send and web routes must call it with asyncio.to_thread. The credential must reach Condor's backend on a poll response, never the browser URL, fragment, browser API response or logs. ConfigManager persists only enumerated sections, so a new link field must survive _save_config. The linked profile is install-wide; per-user identities are deferred.

## Alternatives considered

- OAuth redirect to the dashboard: fails for arbitrary WEB_PORT and remote dashboards; would require a redirect registration or proxy.
- Keep paste-a-token: works today but does not satisfy an automated profile link and asks an admin to handle a publish credential manually.
- Pairing code and server-side polling: works without a redirect URI and leaves the credential on the backend. Choose this, with a verification string, short TTL and single use to reduce code phishing and replay.

## Decision

Use the Botcamp pairing endpoints defined by [[TASK-005]]. Condor requests a new link, shows the verification string and opens https://hub.botcamp.xyz/v1/link?code=... in a new tab. The signed-in Botcamp page repeats that string before Accept. Condor polls server-side until accepted, denied or expired. An accepted response delivers a scoped author credential and profile handle to Condor's backend, which stores them under config.yml's hub section. Link start, poll, disconnect, publish and unpublish remain admin-only. A non-admin may browse and install as today.

## Design

- Add hub client methods for link start and status, with typed responses and bounded response bodies through the existing _send seam. Validate the returned verification URL against the compiled-in Botcamp host before the dashboard opens it; reject redirects. Enforce short expiry and one active pairing per install. A new start invalidates the previous local attempt.
- Add admin-only Condor web routes to start, poll and disconnect. Keep the pairing handle server-side; if the browser needs an opaque local request id, bind it to the authenticated admin and expire it. The poll response to the browser contains state, expiry and profile handle only, never the author credential. Rate-limit polling and stop on denied, expired or replayed status.
- Store the linked handle and credential in the existing hub config section; ensure ConfigManager's save allowlist preserves the values. Never return the credential from GET, log it or put it in a URL. Clearing the link removes local credential and reflects disconnected status even if the hub is unavailable. Document hub-side revocation separately from local disconnect.
- Replace the pasted-token panel with Connect, pending, linked and disconnect states. Show the verification string beside the open-tab action and an expiry/retry path. Publish and unpublish call the existing backend verbs, which read the linked credential; their authorization remains admin-only.
- Preserve the existing compiled-in host, validated listing ids, no-redirect fetch and receiving-install review. No pairing state should alter anonymous browse or import behavior.

## Implementation plan

1. Implement typed pairing client calls and the credential storage path behind the current hub client seam.
2. Add admin-only web routes and bounded server-side polling using asyncio.to_thread.
3. Replace the settings token entry with the browser handoff, matching verification string and connection state.
4. Add unit and route tests for accepted, denied, expired, mismatched, replayed and network-failed pairings, persistence after unrelated config saves, and absence of the credential from URL, browser responses and logs.

## Acceptance criteria

- [ ] An admin can initiate a pairing, see the verification string, open Botcamp's page, accept there and reach a linked state without pasting a token or requiring a redirect URI.
- [ ] Publish and unpublish use the resulting stored credential; a non-admin cannot start, poll, disconnect, publish or withdraw under the install's identity.
- [ ] A mismatched verification string, denial, expiry, replay or unexpected URL leaves the install unlinked. Re-starting a pairing invalidates the prior local attempt.
- [ ] The credential appears only in the Botcamp-to-Condor backend poll exchange and persisted server config; no GET, dashboard response, URL, fragment or log exposes it.
- [ ] A successful link survives ConfigManager._save_config after an unrelated settings change, and disconnect removes the local credential.
- [ ] Pairing and hub tests pass with no new failures relative to the same suites at the merge base; blocking network calls do not run on Condor's event loop.

## Risks and notes

A pairing code can be phished if a user accepts a code initiated by an attacker. The short TTL, single use and repeated verification string are part of this feature, not optional polish. A linked author credential has publish and withdrawal power, so it must be scoped separately from the reviewer credential in [[TASK-005]]. This doc implements the install-wide shape already shipped by FEAT-146 and leaves per-user linking for a later decision.

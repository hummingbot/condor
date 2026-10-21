---
id: CORR-810
kind: improvement
title: Point the compiled-in agent hub URL at Botcamp
summary: Condor's hub client still sends every catalogue, publish and install request to hub.hummingbot.org. Change its compiled-in base to https://hub.botcamp.xyz/v1 and keep the no-redirect, validated-id SSRF guard and no environment override.
category: correctness
impact: high
effort: S
risk: low
status: done
brain: developer
files:
  - condor/hub.py:95
  - tests/test_hub.py
depends_on: []
commits:
  - "3c35179a fix(hub): point the compiled-in agent hub URL at Botcamp (CORR-810)"
created: 2026-10-21
---

## Problem

condor/hub.py currently sets HUB_URL to https://hub.hummingbot.org/v1, while the chosen Botcamp service base is https://hub.botcamp.xyz/v1. Every existing hub request derives from that constant. Changing it at runtime through an environment variable would weaken the client-side SSRF boundary: server-side fetches are safe because the host is compiled in, listing ids are validated before interpolation and redirects are refused.

## Proposed solution

Change only the compiled-in HUB_URL value to https://hub.botcamp.xyz/v1. Keep _listing_id validation, _NoRedirect and the single _send seam intact. Update the focused hub tests to assert the new host on browse, get, fetch, publish and unpublish, and that plausible environment variables cannot redirect it. Retain the existing digest verification before bundle parsing.

## Acceptance criteria

- [ ] HUB_URL is exactly https://hub.botcamp.xyz/v1 and every hub verb addresses that base through the existing client seam.
- [ ] A caller-supplied listing id containing traversal, query or fragment characters is refused before any request, and a 302 never re-aims a fetch.
- [ ] Setting plausible hub URL environment variables leaves the base unchanged; the existing no-override test passes with the Botcamp host.
- [ ] The focused hub test suite passes against the change, with no new failure relative to the same suite on its merge base.

## Notes

The external service contract is [[TASK-005]]. This change only repoints Condor's client. It does not stand up the service or change the receiving install's bundle preview, mute and execution-consent gates. The endpoint is an explicit owner decision recorded in the workstream.

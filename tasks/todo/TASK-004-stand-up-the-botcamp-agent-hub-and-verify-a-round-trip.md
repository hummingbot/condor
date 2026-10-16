---
id: TASK-004
title: Stand up the Botcamp agent hub and verify one agent round-trips through it
summary: >-
  FEAT-146 builds the Condor-side hub client against a stub, so nothing is
  verified against a real service. Settle what is not Condor's to decide — the
  hostname, token issuance, who reviews the validated tier, whether the hub runs
  Condor's review module — then build it and round-trip one agent.
status: blocked
blocked_reason: >-
  The service lives in a Botcamp codebase that is not this repo and not in this
  workspace, and the decisions below (hostname, token issuance, who reviews a
  submission, delisting policy) are the owner's to make; no unattended session
  stands up a public service or writes in a repo it cannot see.
actor: human
effort: L
risk: medium
files:
  - condor/hub.py
depends_on:
  - "[[FEAT-146]]"
commits: []
created: 2026-10-15
---

## Goal

A real hub exists, Condor's compiled-in `HUB_URL` points at it, and one agent
has demonstrably travelled: exported here, published, listed with its digest and
provenance, installed on a second install, and unpublished again. Everything
[[FEAT-146]] can prove against a stub is proved there; this is the half that
needs a service and a person.

## Steps

1. **Record where the service lives.** Which Botcamp repository, and who owns
   it. Note it here, because nothing in the Condor tree mentions Botcamp today
   (`git grep -il botcamp` returns nothing) and the next reader will look.
2. **Settle the base URL** — hostname and path prefix — and write it into
   `condor/hub.py`'s `HUB_URL`. It is compiled in deliberately: that, plus the
   `validate_identifier` check on a listing id, is what makes Condor's
   server-side fetch safe, and is why [[FEAT-133]] could refuse "import from a
   URL" as an SSRF surface while [[FEAT-146]] can fetch. **Do not make it
   environment-overridable** — `condor/sharing/outbox.py:52` holds the same rule
   for the share collector, with the reason in its docstring.
3. **Decide how a publish token is issued** from a Botcamp account, and where in
   the Botcamp UI an operator finds it to paste into Condor's Settings. A short
   TTL and a visible revoke are worth having; Condor stores the token in
   `config.yml`'s `hub` section and never returns it from a GET.
4. **Decide whether the hub runs Condor's own review.** [[FEAT-144]]'s
   `condor/agents/bundle_review.py` is pure and importable precisely so the
   service can run the receiver's own checks on a submission — the export-side
   review is advice to the author, and the author controls their own exporter.
   If the service cannot depend on it, say so here, and make the hub report
   which review version produced its findings so a client can see the two
   disagree. Either way the submission-side review is **required**: the
   client-side scrub has a five-item history of misses in this repo
   ([[SEC-230]] in `improvements/backend/`, [[SEC-283]], [[SEC-331]],
   [[SEC-336]], [[CORR-245]]).
5. **Decide what `validated` means and who says it** — the checklist a reviewer
   applies, how long a badge survives a new revision of the same agent, and the
   delisting path when a validated agent is later found bad. Record that
   delisting does not recall a download, so the badge can never be the import
   gate; [[FEAT-146]]'s trust model already states that and this step is where
   the operational half is agreed.
6. **Implement the endpoints** [[FEAT-146]]'s contract names: the listing, one
   entry with its sha256 and `bytes`, the bundle download, authenticated submit,
   authenticated delete. Carry `agent_uid`, `version`, `notes`, `author`,
   `badge`, `requires` and `findings` on a listing, as [[FEAT-143]] and
   [[FEAT-144]] define them.
7. **Round-trip one agent.** Export an agent from this install, publish it,
   confirm the listing shows it with a digest matching the bytes, install it on a
   **second** install under a new slug, and confirm there what [[FEAT-133]]
   promises: routines and skills muted, no `TickEngine` for the slug, nothing in
   the stock tree. Then unpublish and confirm the listing no longer offers it.
8. **Round-trip a refusal.** Submit a bundle with a planted BIP-39 phrase in a
   skill and confirm the hub refuses it, naming the file — the submission-side
   review actually running, not merely configured.

## Acceptance criteria

- [ ] This doc names the repository and owner of the hub service, and the
      decisions from steps 2–5 are written down here rather than left in a chat
- [ ] `condor/hub.py`'s `HUB_URL` names the real service, and no environment
      variable redirects it (the test [[FEAT-146]] carries still passes)
- [ ] An operator can obtain a publish token from a Botcamp account and paste it
      into Condor Settings; a `GET` of the token setting answers set/not-set and
      never the value
- [ ] One agent exported from install A appears in the hub listing with a
      `digest` equal to the sha256 of the bytes the download returns
- [ ] That agent installs on install B under a new slug: its routines and skills
      are listed in `mutes.yml` as muted, `AgentStore().get(slug)` returns it,
      no `TickEngine` exists for it, and the tracked `agents/` tree on B is
      byte-identical before and after
- [ ] Unpublishing with the same account token removes it from the listing, and
      a `fetch` of that listing id afterwards fails readably rather than
      returning stale bytes
- [ ] A submission carrying a 12-word BIP-39 phrase in `skills/s/SKILL.md` is
      refused by the hub, the refusal names that path, and the listing never
      shows the agent
- [ ] A listing with `badge: "community"` installs on B exactly as a
      `validated` one does — the live confirmation of [[FEAT-146]]'s trust
      model, which no session implementing that feature can observe
- [ ] `PRIVACY.md` states that publishing sends agent content off the box and
      that unpublishing removes the listing without recalling a download

## Notes

- **Blocked on decisions, not on effort.** Steps 2–5 are owner calls — a public
  hostname, a credential-issuance flow, a review policy and a delisting policy —
  and step 6 is work in a repository this workspace does not contain. Clear the
  gate by editing this doc back to `status: todo` and deleting
  `blocked_reason:` once the decisions are recorded and the repo is reachable.
- **This doc exists because [[FEAT-146]] cannot carry these criteria.** A
  session implementing the Condor client can test everything against a stub
  transport and can observe none of the above, so putting a live-service
  criterion on that doc would give its chunk only one possible outcome.
- **The second install matters.** A round trip through one install proves the
  transport and nothing about the scrub: `created_by`, `server_name` and the
  fork stamps only read wrong on a box that is not the one that exported them.
- `tasks/` is **not** gitignored in this repo — unlike `/improvements/`
  (`.gitignore:186`) and `/features/` (`:187`) — so this doc commits with a
  plain `git add`.

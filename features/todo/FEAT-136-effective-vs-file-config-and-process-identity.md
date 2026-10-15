---
id: FEAT-136
title: Expose effective vs file config, the process start time, and the model a session is actually running
summary: The engine status shows live server_name, agent_key and risk_limits but never diffs them against the file on disk, the process start time and boot_id reach only telemetry and status.json, and the adapter's effective model is surfaced nowhere. Add a drift diff, process identity and the effective model.
status: todo
effort: M
risk: low
brain: developer
new_files:
  - tests/test_config_drift.py
touched_files:
  - condor/web/routes/agents.py
  - condor/web/routes/meta.py
  - condor/agents/engine.py
  - condor/acp/client.py
  - frontend/src/lib/api.ts
  - frontend/src/pages/AgentWorkspace.tsx
depends_on:
  - "[[CORR-732]]"
commits: []
created: 2026-10-14
---

## Objective

An operator can see, without reading logs, whether a running session's config
still matches the file, when its process started, and which model it is actually
running.

## Context and constraints

GitHub issue [#275](https://github.com/hummingbot/condor/issues/275): "it took
log archaeology to see that a process was still running a start-time config."
[[CORR-732]] makes the *write* say it did not reach the process; this item makes
the *running session* say what it is actually using.

Confirmed at `45e60d60`:

- the engine status dict shows the live `server_name`, `agent_key` and
  `risk_limits`, and nothing diffs them against the file on disk.
- the process start time is not exposed. `_started_at`
  (`condor/telemetry/context.py:26`) goes to telemetry only, and `BOOT_ID`
  (`condor/runtime/registry_file.py:34`) and the pid are written only to
  `status.json`.
- `/meta/env` (`condor/web/routes/meta.py:29-45`) returns version, branch,
  python, os, arch and in_docker — no process identity.
- the effective model is computed and then dropped. `advertised_models`
  (`condor/acp/client.py:383-403`) reads the adapter's `currentValue` and
  `_select_model` assigns `self.active_model_id`, but `git grep active_model_id`
  outside `client.py` matches **only two test assertions** — no surface reads it.

That last point is the live residual of
[#221](https://github.com/hummingbot/condor/issues/221). Its protocol-drift
claim is **already fixed** at `45e60d60` (`configOptions` plus
`session/set_config_option`), and its loud-failure ask is implemented on the
unmerged branch `origin/feat/acp-fable-option` (`95729442`, 2026-09-28) — see
[[TASK-002]]. What neither covers is #221's third ask, in its race comment:
"Show the effective model (from the adapter's `currentValue`) in the session
status and dashboard, so a pin audit before race start can check it."

## Alternatives considered

1. **Log the effective config and model at start.** This is approximately what
   happens now, and the issue is a report that log archaeology was the only
   route. Rejected.
2. **Hot-reload config into running engines so drift cannot exist.** A
   behaviour change on a live trading loop, with its own risk, and #275 does not
   ask for it. [[CORR-732]] records the same rejection.
3. **Expose the live values and let the caller fetch the file and compare.**
   Rejected: every caller re-implements the diff and they drift — the repo's own
   recurring finding.
4. **Compute the diff server-side, expose process identity, and surface the
   effective model.** Chosen.

## Decision

Alternative 4. One per-engine effective-vs-file diff in the strategy status
response, `process_started_at` and `boot_id` on `/meta/env`, and
`active_model_id` surfaced on the session status.

## Design

- The diff is computed where both sides are already in hand — the strategy
  status route has the live engines and can load the file through
  `load_full_config`. It reports the drifted keys with both values, not a
  boolean, so the operator sees what would change on restart.
- The drift set is the pins and limits that matter — the same field set
  [[CORR-728]] names as pins — rather than every key, so a cosmetic difference
  does not badge a session.
- `process_started_at` and `boot_id` come from the values that already exist
  (`telemetry/context.py:26`, `registry_file.py:34`); this only routes them to
  `/meta/env`, which exists for exactly this purpose ("a bug report that does
  not say which commit", `meta.py:3`).
- `active_model_id` is read from the adapter's `currentValue`, which
  `advertised_models` already returns, and carried onto the session status. It
  is reported as the *adapter's* answer rather than the requested pin, which is
  what makes it an audit rather than an echo.
- The dashboard badges a drifted session and shows the three new facts.

## Implementation plan

1. Add the diff to the strategy status response, over the pin/limit field set.
2. Add `process_started_at` and `boot_id` to `/meta/env`.
3. Surface `active_model_id` on the session status.
4. Frontend types, the drift badge, and the three facts in the session view.
5. Tests per the criteria below.

## Acceptance criteria

- [ ] A session started with one `server_name` whose file has since changed reports that key as drifted, with both values
- [ ] A session whose file is unchanged reports no drift, and a cosmetic difference outside the pin/limit set does not badge it
- [ ] `/meta/env` returns `process_started_at` and `boot_id`
- [ ] A session's status reports the model the adapter says it is on, taken from `currentValue`, not the requested pin
- [ ] A session whose requested pin was not applied reports the adapter's actual model, so a pin audit can catch it
- [ ] The dashboard badges a drifted session
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Risks and notes

- Verified at `45e60d60` with `git show HEAD:<path>`; the `/meta/env` body and
  the `active_model_id` grep (two test hits only) were both read from the
  committed tree.
- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60`
  with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0
  failed in 98.1s, EXIT=0**. Provenance, not a threshold.
- `depends_on: [[CORR-732]]` — that item establishes the live-engine count this
  diff is reported against, and the two touch the same route.
- #275's fourth ask (surfacing `restart_required` from connector writes) is in
  [[CORR-732]], not here, because it is about not dropping a field that already
  arrives.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.26
  `ARCH-114 [done]` (the /agents hero dropping the picked model), below the
  0.50 fallback bar — adjacent on the model-surfacing side.

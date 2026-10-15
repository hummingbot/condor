---
id: CORR-732
title: update_strategy_config reports success without saying it does not reach running sessions
summary: The route writes config.yml and reports updated, while a running engine keeps the config dict it copied at start — only restart_on_boot is pushed to live engines. An operator who edits a config and sees success has no way to learn the running agent still has the old one.
category: correctness
impact: medium
effort: S
risk: low
status: done
brain: developer
files:
  - condor/web/routes/agents.py:2858
  - condor/web/routes/agents.py:3386
  - condor/agents/engine.py:238
depends_on: []
commits:
  - "77b2e26c fix(agents): say what a config write reached, and stop dropping restart_required (CORR-732)"
created: 2026-10-14
closed: 2026-10-15
---

## Problem

GitHub issue [#275](https://github.com/hummingbot/condor/issues/275): "Several
times during the race, an operator edited a config, saw 'updated', and assumed
the running agent had picked it up. It hadn't … 'I fixed it' and 'it's fixed'
drifted apart silently."

Confirmed at `45e60d60`. `update_strategy_config`
(`condor/web/routes/agents.py:2858-2876`) loads the full config, gates a pin
change, saves, and returns:

```python
save_full_config(strategy.home, config_dict)
return {"updated": True, "config": config_dict}
```

A running engine holds the `config` dict it copied at start
(`condor/agents/engine.py:238`), and the only key pushed to live engines is
`restart_on_boot` (`agents.py:3386-3393`). So `{"updated": True}` is true of the
file and says nothing about the process, and no caller — UI, agent or MCP tool —
is given anything to say otherwise.

The behaviour itself is defensible: a live trading loop should not have its
config swapped under it mid-tick. The defect is that the response cannot be
distinguished from one that did take effect.

## Proposed solution

Report what the write actually reached, rather than changing when it applies:

1. Return `applies_to_running: false` and `live_engines: N` from
   `update_strategy_config`, counting live engines for the strategy through
   `for_strategy()`.
2. Have the UI and the agent-facing surfaces render that as "saved; restart N
   sessions to apply", so the operator reads the state rather than inferring it.
   *As shipped:* the route renders the sentence itself, into a `message` field,
   rather than leaving every caller to compose it from the count — which is what
   lets the agent-facing surface read the state with no client change. See
   `## Notes` for why the dashboard half had nothing to render here.
3. Surface the `restart_required` flag hummingbot-api already returns from a
   connector write, which Condor currently passes through and drops.

The richer half of #275 — a per-engine **effective vs file** diff, the process
start time, and badging drifted sessions — is [[FEAT-136]], which builds on the
count this item exposes.

## Acceptance criteria

- [x] `PUT /{slug}/strategies/{sslug}/config` returns `applies_to_running: false` and a `live_engines` count matching `for_strategy()` — `test_the_write_reports_the_live_sessions_it_did_not_reach` asserts the field against `supervisor.for_strategy("brigado", "mm")` in the same expression
- [x] With no live engine the response says so, rather than reporting the same thing as a drifted save — `test_with_no_live_session_the_response_says_so`; `test_one_live_session_is_counted_in_the_singular` and `test_a_paused_session_counts_because_it_resumes_on_its_old_config` pin the other two readings
- [ ] The dashboard renders "saved; restart N sessions to apply" when `live_engines > 0` — **not applicable, narrowed**: nothing in the dashboard calls this route (see `## Notes`). The response carries that sentence verbatim as `message`, so the surfaces that *do* call it print it; the dashboard half this item could land is the connector-write notice below
- [x] A connector write whose upstream response carries `restart_required` surfaces it instead of dropping it — lifted to the top level of both credential routes (`test_a_credential_add_surfaces_the_servers_restart_required`, `test_a_credential_delete_surfaces_it_as_well`, and a 4-way parametrization for a server that does not send it), typed in `api.ts` and rendered in the Keys tab (`ApiKeysSettings.restart.test.tsx`, 3 tests)
- [x] `restart_on_boot` still reaches live engines as it does today — `test_restart_on_boot_still_reaches_live_engines`: `engine.config` flipped, `read_status` re-recorded, `applied_to_running: 1`. The one test in the new file that is **green at the merge base**, by design
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared — deferred to the plan's final verification chunk, which runs the whole gate once over everything the plan landed; this run measured the item's own criteria and the suites covering the touched files (below)

## Notes

- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60` with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0 failed in 98.1s, EXIT=0**. Quoted as provenance, not as a threshold — unrelated items add tests.
- Verified at `45e60d60` with `git show HEAD:<path>`; the route body is quoted
  above from the committed tree.
- Split from the rest of #275 as the operator asked - this item is the
  correctness half (a response that cannot be told apart from a different
  outcome), [[FEAT-136]] is the new capability (effective vs file, process start
  time, drift badges).
- Deliberately does **not** make config edits hot-apply to a running engine.
  Swapping a trading loop's config mid-run is a behaviour change with its own
  risk, and #275 does not ask for it.
- `condor/web/routes/agents.py` is also declared by the open [[SEC-715]] and by
  [[CORR-729]] - real file overlap for plan ordering.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.22
  `SEC-715 [todo]`, below the 0.50 fallback bar; it concerns which risk limits
  the route persists, not what the response reports.

## Notes (closure)

Shipped in `77b2e26c`.

**What the response says now.** `update_strategy_config` answers
`applies_to_running: false`, `live_engines: N` (straight from
`_get_engines_for` -> `for_strategy()`, so there is no second walk to drift
from) and a `message` of either `"Saved; restart N session(s) to apply."` or
`"Saved. No session is running, so the next one starts on it."`. `updated` and
`config` are unchanged, so no existing caller breaks. A paused engine counts:
it resumes on the dict it was constructed with, so it is exactly as drifted as a
running one.

**The connector half.** `_restart_required()` in `routes/settings.py` lifts the
flag out of the upstream body onto Condor's own answer for both
`POST /settings/credentials` and `DELETE /settings/credentials/{connector}`;
`result` is still passed through verbatim, so nothing that read it loses it. A
server that omits the flag, sends it false, answers with no body, or answers
with something that is not a dict all read as *no restart needed* — an older API
server must not produce a warning. The Keys tab renders the notice after the add
flow has closed, which is where it was invisible before.

**Acceptance criterion 3 is narrowed, not skipped.** There is no dashboard
caller of `PUT /{slug}/strategies/{sslug}/config` to render anything: grepping
`frontend/src` for the path, for a `strategies/.../config` URL and for
`updateStrategyConfig` returns nothing, and the whole-repo grep for the route
matches only its own definition. The dashboard's only strategy-config write is
the `restart_on_boot` toggle in `PlaybookView`'s `ConfigCard`, and that one
*does* reach live engines — it is the exception, not the case this item is
about. `ConfigCard` otherwise renders config read-only. So the operator in #275
who saw "updated" saw it through the API or the agent, both of which now read
the `message` field with no client change. Adding an unused `updateStrategyConfig`
client method, or building the strategy-config editor the criterion presupposes,
would both be scope this item did not ask for. If such an editor ever lands, the
drift rendering for #275's UI half is [[FEAT-136]]'s (it already declares
`frontend/src/lib/api.ts` and the workspace page, and `depends_on: [[CORR-732]]`
for exactly this count); its doc was deliberately left untouched here because it
is a live plan chunk.

**Declared `files:` cross-check.**

- `condor/web/routes/agents.py:2858` — changed: the new response.
- `condor/web/routes/agents.py:3386` — **not changed**, and no edit is owed.
  That anchor is `set_restart_on_boot`, the one write that already pushes into
  live engines and re-records their status; criterion 5 is "still reaches live
  engines as it does today", so the work owed there was a regression lock, not a
  change. `test_restart_on_boot_still_reaches_live_engines` is that lock, and it
  is the one test in the new file green at the merge base.
- `condor/agents/engine.py:238` — **not changed**, deliberately. The start-time
  config copy is the behaviour this item preserves; touching it would be the
  hot-apply the doc explicitly rejects above.

**Verification.**

- `uv run pytest tests/test_config_write_reports_what_it_reached.py` = **11
  passed**. Red/green in one turn: the same file copied into a detached sibling
  worktree at the base `d8f2bf31` = **10 failed, 1 passed** (the one pass is the
  `restart_on_boot` lock). Worktree removed afterwards.
- Suites covering the touched backend files (every test file referencing
  `routes.agents`, `routes.settings` or `/settings/credentials`, 30 files) =
  **1134 passed**.
- Frontend: `npx vitest run src/components/settings/` = **8 files, 65 passed**
  (3 of them new). `npx tsc -b --force` exit 0. `npx eslint` clean on all four
  touched/added frontend files. `npm run lint:ci` exit 0 — "No new lint errors.
  41 baselined errors remain to pay down."
- `uv run black --check` and `uv run isort --check-only` clean on all three
  Python paths. `uv run ruff check` reports 3 F401s on
  `condor/web/routes/agents.py`; the identical 3 report against the base copies
  of both files extracted with `git show d8f2bf31:<path>` in the same turn, so
  they are pre-existing and not on touched lines.

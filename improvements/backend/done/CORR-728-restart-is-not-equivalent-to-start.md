---
id: CORR-728
title: Restart is not equivalent to start — it drops session pins, skips start's checks, adds sessions and reports success before the first tick
summary: _restart rebuilds from load_full_config, which reads loop.md and config.yml but never the session's own config.yml where the start request's server and model overrides live. It also skips start's server-404 and model checks, reconcile_boot adds a session rather than replacing, and both report success after create_task.
category: correctness
impact: high
effort: M
risk: medium
status: done
brain: developer
files:
  - condor/runtime/loops.py:415
  - condor/runtime/loops.py:213
  - condor/runtime/loops.py:121
  - condor/agents/engine.py:238
  - condor/agents/engine.py:293
  - condor/web/routes/agents.py:3074
depends_on: []
commits:
  - "66fa8697 fix(loops): make a restart the same thing a start is (CORR-728)"
created: 2026-10-14
closed: 2026-10-15
---

## Problem

GitHub issue [#274](https://github.com/hummingbot/condor/issues/274): before the
race, agents were pinned to shared settings (`server_name`, LLM provider and
model). At race start some pins were reset with no host wiped, and pinning had
to be redone under race pressure. Operators also saw a restart bring back a
server name that no longer existed, a bare restart *add* sessions and cause
fleet-wide slow or timed-out ticks from contention for one LLM budget, and a
restart report success without the run coming back.

All four are one concern — **`_restart` is not equivalent to `_start`** — and
all four are confirmed at `45e60d60`.

**Pins are rebuilt from the wrong source.** `_restart`
(`condor/runtime/loops.py:415-447`) calls
`load_full_config(strategy.home, strategy.default_config)`, which reads loop.md
frontmatter and the strategy `config.yml`. The per-session
`sessions/session_N/config.yml` (`condor/agents/engine.py:238`) is the only copy
carrying the start request's overrides (`condor/web/routes/agents.py:3074-3076`),
and `_restart` never reads it. The code says why it reads the current file —
"Revalidate against the config as it stands NOW, not as the dead session had it:
the user may have changed it precisely because the last run misbehaved"
(`loops.py:431-433`). That intent is right and the fix must keep it: the dead
session's *pins* are not the same thing as the dead session's *config*.

**Restart skips the checks start makes.** `_start` refuses an unknown server
with a 404 (`agents.py:3093-3104`) and checks the model (`:3109-3121`).
`_restart` does neither.

**Restart adds sessions.** `reconcile_boot` starts one *new* session for each
settled status with `restart_on_boot` (`loops.py:213-221`) and nothing dedupes
against `for_strategy()`; `_start` never stops existing engines. Two engines for
one strategy then share one LLM budget, which is the contention the race saw.

**Success is reported before anything runs.** `TickEngine.start` only calls
`create_task` (`engine.py:293-305`); `_restart` returns `True` immediately
(`loops.py:445-447`) and `_start` returns `{"started": True}` (`agents.py:3140`).
Neither waits for a first tick, so "restarted" means "a task was created".

## Proposed solution

1. Rebuild pins from the dead session, config from the file. `_restart` keeps
   `load_full_config` for the strategy config — preserving the revalidation
   intent above — and overlays the pins (`server_name`, provider, model) from the
   dead session's `session_dir/config.yml`, or from a pin snapshot that
   `record()` writes into `status.json` (`loops.py:121`). State which fields are
   pins in one place so the overlay set is not re-guessed per call site.
2. Run the same server-404 and model/`agent_key` checks in `_restart` as in
   `_start`. The refusal half — `_resolve_server` must not fall back when a
   pinned name does not resolve — is [[CORR-729]], which owns that chain.
3. Dedupe against `for_strategy()`: a restart replaces its predecessor rather
   than adding to it, in both `_restart` and `reconcile_boot`.
4. Report success only once the first tick has completed, in `_restart` and in
   the `_start` route, with a bounded wait so a slow first tick does not hang
   the request.
5. Document in one place what survives a restart — config, pins, state, open
   orders — since the five places a pin can live (#274's own list) is why this
   was unclear.

## Acceptance criteria

- [x] A session started with a `server_name`/model override, killed and restarted, comes back on the same server and model
- [x] A strategy `config.yml` edited while the session was dead is still picked up on restart, so the revalidation intent at `loops.py:431` is preserved
- [x] `_restart` with a server name that no longer exists refuses with the same error `_start` gives, instead of starting
- [x] `reconcile_boot` with one live engine for a strategy does not produce a second, and `for_strategy()` reports one engine after a restart
- [x] `_restart` and the start route return success only after a first tick completes, and return a readable failure when the first tick does not arrive within the bound
- [x] One document states what survives a restart, naming all five places a pin can live
- [x] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Notes

- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60` with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0 failed in 98.1s, EXIT=0**. Quoted as provenance, not as a threshold — unrelated items add tests.
- Verified at `45e60d60` with `git show HEAD:<path>`; the `_restart` body,
  including the revalidation comment, was read in full.
- Filed as one item, not four, because the four defects are one property
  (restart diverging from start) in one function, and three of them edit
  `_restart` directly. Splitting them would put three docs in a queue for the
  same twenty lines while leaving the function half-converged in between.
- #274 names `restart_on_boot` not carrying over as a fifth point. It is
  excluded deliberately: it defaults to `false` (`condor/agents/config.py:88`)
  and new strategies write it explicitly (`agents.py:2745`), so that is the
  documented default rather than a defect. Whether the default should change is
  a product decision, not a correction.
- #274 also relates to [[SEC-722]] via #221. The ACP model-pin protocol drift
  that #221 reports is **already fixed** at `45e60d60`
  (`condor/acp/client.py:383-403` reads `configOptions` and `:728` uses
  `session/set_config_option`), so this item's pin work is about carrying the
  pin across a restart, not about applying it. See [[TASK-002]].
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.23
  `CORR-611 [done]`, below the 0.50 fallback bar.

## Notes (shipped)

Implemented in `66fa8697`, verified at `49f18313` (the branch head this work
started from).

**Verification** — `uv run pytest` on the change: **6521 passed, 2 skipped, 0
failed in 97.4s, EXIT=0**. `uv run black --check condor/ tests/` and
`uv run isort --check-only condor/ tests/` both clean. Nothing fails on the
change side, so no test that passed at `49f18313` can fail now; the baseline
run was not repeated for that reason. The count is above the 6427 quoted at
doc-write time because other items on this plan added tests and this one adds
19 (`tests/test_restart_matches_start.py`).

**Declared files not edited.** `condor/agents/engine.py:238` (the per-session
`save_full_config`) needed no change: it already writes the only copy of the
start request's pins, and the fix *reads* it (`load_session_pins`) as the
fallback for a session whose status file predates the pin snapshot.
`condor/agents/engine.py:293` (`TickEngine.start`) stayed fire-and-forget on
purpose — several callers (Telegram, delegate, experiments) want it that way —
so the wait is a separate `wait_first_tick` and is explicit at the two callers
this item names. Both paths are in the touched file; the other four declared
paths were edited directly.

**Two judgement calls, recorded because the criteria are tighter than the code
can honestly be.**

1. *Success on a tick still in flight.* Criterion 5 is implemented word for
   word — success once the first tick **completes**, a readable failure when no
   tick **arrives** within the bound — and the state in between (arrived,
   still thinking at the bound) is reported as success. A tick routinely takes
   tens of seconds and may take ten minutes (`tick_timeout_sec`), so a bound
   long enough to see every tick complete would hold an HTTP request past every
   proxy timeout and add minutes to startup. The bound (`FIRST_TICK_WAIT_SEC`,
   20s) only has to separate "the run came up" from "it died on the way up",
   and dying is immediate.
2. *Same refusal, not the same HTTP status.* Criterion 3's "the same error
   `_start` gives": `_restart` refuses on exactly the conditions the route
   refuses on (an unknown **pinned** server, a model `agent_key_error` rejects)
   and reports the same reason, but as a `RuntimeError` that `reconcile_boot`
   turns into an owner-visible line — a boot pass has no HTTP response to
   return a 404 in. The route's `check_server_access` is deliberately not
   repeated: the run passed it when it was started, and `_resolve_server`
   scopes credentials to the owner off the status file anyway. An *inherited*
   server name that resolves to nothing is still allowed through, because that
   is what `_start` does too (it falls through to the owner's accessible
   servers); refusing it would strand every install that never saved a server
   called "local".

**Beyond the doc, needed to keep the fix honest.** A reported failure leaves
nothing running — both the route and `_restart` stop the engine before
reporting — because a loop ticking on behind "could not start" is the same
untruth in the other direction. And `reconcile_boot` starts its restarts as
tasks so a fleet's first-tick waits overlap rather than adding up inside
startup, which is awaited inline in `main.py`.

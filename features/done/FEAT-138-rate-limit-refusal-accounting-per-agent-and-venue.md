---
id: FEAT-138
title: Count and surface rate-limit refusals per agent and venue, and attribute call volume to the agent that spends it
summary: Nothing in Condor counts a 429. RefusalLog records only risk-gate refusals, the sole per-server limiter is the dashboard poller, and per-request budgets exist only for GeckoTerminal. Throttling therefore reads as a mysterious strategy bug — one host was "intermittent" at 8 attempts, 8 failures, 27 refusals.
status: done
effort: M
risk: low
brain: developer
new_files:
  - tests/test_throttle_accounting.py
  - mcp_servers/hummingbot_api/throttle.py
  - condor/agents/throttle.py
touched_files:
  - mcp_servers/hummingbot_api/server.py
  - mcp_servers/hummingbot_api/middleware.py
  - mcp_servers/hummingbot_api/settings.py
  - condor/agents/risk.py
  - condor/agents/engine.py
  - condor/agents/prompts.py
  - condor/runtime/toolsets.py
  - condor/web/routes/agents.py
  - frontend/src/lib/api.ts
  - frontend/src/components/agent/workspace/fleet.ts
  - frontend/src/components/agent/workspace/LoopBar.tsx
  - frontend/src/components/agent/workspace/LoopBar.test.tsx
  - tests/test_config_drift.py
depends_on: []
commits:
  - "fa1ddb12 feat(agents): count the 429s — refusals per agent and venue, call volume per host (FEAT-138)"
created: 2026-10-14
closed: 2026-10-15
---

## Objective

A 429 or throttle refusal is counted per tick, per agent and per venue, shown as
a venue condition in the dashboard, and the agent whose call volume consumed a
host's budget can be named.

## Context and constraints

GitHub issue [#276](https://github.com/hummingbot/condor/issues/276): "Sampling
the logs suggested one host was 'intermittent, may recover'. Counting its
attempts showed 8 attempts, 8 failures, 0 successes and 27 refusals: it was
consistently refused." And "Throttling generally looked like a mysterious
strategy bug or a vendor outage, not like what it was."

Confirmed at `45e60d60`: `git grep -n '429\|rate_limit\|RateLimit\|throttl'`
over `mcp_servers/hummingbot_api/` returns **nothing**. The agent tool path does
not count a refusal at all. `RefusalLog` (`condor/agents/risk.py:616`) records
only risk-gate refusals; the only per-server limiter is the dashboard poller
(`condor/server_data_service.py:259`, `:354-357`); per-request budgets exist
only for GeckoTerminal (`condor/pool_data.py:441-592`).

The retry multiplication #276 opens with is in Gateway, not here
(`RATE_LIMIT_MAX_ATTEMPTS = 4`, waits of 5s/10s/20s, so one throttled keyless
quote can take ~35s before it throws 429 and 24 sequential quotes ~840s). That
is **upstream and not filed** — but it is the reason counting matters here:
Condor sees one slow call where the venue refused four times, so the only place
the true refusal count can be attributed is the tool path.

The second half — "Nothing showed that one 1.0s poller was the sole consumer
starving a host" — is the same incident as [[CORR-726]], from the accounting
side.

## Alternatives considered

1. **Log 429s and leave counting to log analysis.** This is today's situation
   plus a log line, and #276's finding is that sampling logs gave the wrong
   answer where counting gave the right one.
2. **Add a Condor-side limiter that pre-empts the venue's.** Rejected as a
   different feature with its own risk: a limiter guesses the venue's budget,
   and guessing it wrong starves a healthy agent. Count first.
3. **Reuse the GeckoTerminal per-request budget machinery for every venue.**
   Rejected: that is a budget enforcer, not a refusal counter, and it would
   couple venue accounting to a pool-data module.
4. **Count refusals in the MCP tool path, record them in `RefusalLog` and the
   journal, attribute call volume per agent and host.** Chosen.

## Decision

Alternative 4 — counting and attribution, no new enforcement. A throttle
refusal is recorded where the call is made, aggregated per tick/agent/venue,
and shown as a "throttled" condition.

## Design

- The count is taken in the `mcp_servers/hummingbot_api` tool path, which is the
  one place that sees every agent call and its status, and is where #276 asks
  for it.
- `RefusalLog` gains the throttle kind beside the risk-gate kind rather than a
  parallel store, so a tick has one refusal surface. Its existing shape already
  carries a reason, which is what a throttle refusal needs.
- Per-agent call volume per host/venue is counted on the same seam, so "which
  agent spent this host's budget" is answerable — the question [[CORR-726]]'s
  incident could not answer.
- "Throttled" is exposed as a venue/connector condition, not an agent error:
  the agent did nothing wrong, and labelling it as an agent failure is what made
  it read as a strategy bug.
- A counter must never change whether a call is made - this item adds no gate.

## Implementation plan

1. [x] Record throttle refusals in the tool path, with venue and agent.
2. [x] Extend `RefusalLog` with the throttle kind; write to the journal.
3. [x] Count per-agent call volume per host/venue on the same seam.
4. [x] Expose the per-tick and per-agent counts, and the throttled condition, on
   the route and the dashboard.
5. [x] Tests per the criteria below.

## Acceptance criteria

- [x] A 429 from the tool path is counted against the calling agent and the venue, and appears in `RefusalLog` and the journal
- [x] Eight attempts with eight refusals report 8 attempts / 0 successes / 8 refusals, so the #276 reading is reproducible from the counters rather than from logs
- [x] A venue being refused is shown as a venue/connector condition, not as an agent error
- [x] Per-agent call volume per host is reported, so the agent consuming a host's budget can be named
- [x] Adding the counters changes no call's outcome, and no new throttle gate is introduced
- [x] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Risks and notes

- Verified at `45e60d60`: the absence is the measurement —
  `git grep -n '429\|rate_limit\|RateLimit\|throttl' HEAD -- mcp_servers/hummingbot_api/`
  returns no hit. The `RefusalLog`, poller and GeckoTerminal anchors were read
  from the committed tree.
- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60`
  with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0
  failed in 98.1s, EXIT=0**. Provenance, not a threshold.
- #276's third proposal — a fail-fast option or total-deadline header on
  Gateway's `withRateLimitRetry` — is **not filed**, being in the Gateway repo.
  The Condor-side consequence (one slow call hiding four refusals) is recorded
  above as the reason this counting is needed here.
- The fifth criterion is deliberate. The tempting next step is a Condor-side
  limiter, and alternative 2 says why that is a separate decision.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.21
  `PERF-717 [todo]` (serial chat WS fan-out), below the 0.50 fallback bar and
  unrelated. #188 (GeckoTerminal rate limits), which #276 cites as related, is a
  GitHub issue and not a backlog doc.

## Notes (shipped)

**How each criterion was shown.** `tests/test_throttle_accounting.py`, 44 tests:

1. *Counted, in `RefusalLog` and the journal* —
   `test_a_429_in_the_tool_path_is_counted_against_the_agent_and_the_venue`
   (the log line carries agent, session, host, venue and tool) and
   `test_a_tick_folds_the_venues_refusals_into_its_journal` (the journal row is
   `venue_throttled`, naming the venue and the 429).
2. *8 / 0 / 8* —
   `test_eight_attempts_and_eight_refusals_report_eight_refused_none_ok`, plus
   `test_attempts_always_equal_the_three_outcomes`, which pins
   `attempts == successes + refusals + errors` so the triple cannot drift.
3. *A venue condition, not an agent error* — four ways, because this is the
   criterion the whole item exists for:
   `test_a_throttled_venue_is_never_recorded_as_the_agents_error` (`_last_error`
   untouched), `test_a_venue_condition_is_folded_across_agents_not_per_agent`
   (the venue row carries no agent at all),
   `test_the_prompt_tells_the_agent_the_venue_refused_not_the_gate` (its own
   prompt block; the gate's block keeps only gate refusals), and
   `LoopBar.test.tsx` → "says it is the venue and never the agent".
4. *Who spent the host's budget* —
   `test_the_agent_consuming_a_hosts_budget_can_be_named` (heaviest consumer
   first, named) and `test_two_sessions_of_one_agent_are_two_consumers`.
5. *No outcome changed, no gate added* — `test_counting_changes_no_calls_outcome`
   walks all four paths through `handle_errors` and asserts each returns or
   raises exactly what it did before, including that an existing `ToolError`
   passes through as the *same object*; `test_a_broken_recorder_cannot_break_a_call`
   makes the recorder raise and the call still succeeds. Nothing in either new
   module can refuse a call — there is no gate to introduce.
6. *Nothing regressed* — `uv run pytest` on the change: **6683 passed, 2 skipped,
   0 failed in 101.65s, EXIT=0**. The doc's provenance count (6427 at
   `45e60d60`) has since grown with the other chunks this plan landed; with no
   failure at all there is nothing to attribute to a merge base ([[CORR-414]]).
   `frontend/dist` was present, so the 21 web-route tests ran rather than
   skipping. `black`, `isort`, `tsc --noEmit` and `eslint` clean on every
   touched path; `vitest` 224/224 over `components/agent/workspace/`.

**Deviations from the declared file list.** Both lists above were corrected to
what shipped. Three things the design implied but the lists did not name:

- *Two new modules instead of one seam.* `mcp_servers/hummingbot_api/throttle.py`
  is the writer (stdlib only, handed `$CONDOR_THROTTLE_LOG` by the spawner) and
  `condor/agents/throttle.py` is the reader. They are split because the tool path
  is a **separate stdio subprocess** — the design's "count where the call is
  made" and "write to `RefusalLog`" are in two processes, so a transport was
  required. Importing `condor` into the MCP server would have dragged
  `condor.agents.__init__` (and so `TickEngine`) and `condor/__init__`'s plotly
  patch into every agent subprocess start, and `settings.py`'s own comment is
  explicit that this server reads no agent registry. Handing the path over is the
  pattern `CONDOR_AGENTS_ROOT` already sets for the condor MCP server.
- *`middleware.py`, `settings.py`, `toolsets.py`, `prompts.py`.* The seam that
  sees every call and its status is `handle_errors`, not a call site in
  `server.py`; `server.py` gains only the two argv flags. `settings.py` holds the
  labels, `toolsets.py` stamps them and the log path on the spawn. `prompts.py`
  is required by criterion 3: left in the existing block, a 429 would have been
  rendered to the agent under "the runtime gate decided this", which is exactly
  the misreading being fixed.
- *`frontend/.../fleet.ts`, `LoopBar.tsx`, `LoopBar.test.tsx`.* `api.ts` alone
  carries types nothing renders, and "is shown as a venue condition" asks for a
  surface. [[FEAT-141]] owns the full fleet diagnostics view and depends on this
  item, so the view is deliberately *not* built here — this adds the badge to the
  bar an operator already watches, following [[FEAT-136]]'s split (a pure rule in
  `fleet.ts`, a chip in `LoopBar.tsx`).
- *`tests/test_config_drift.py`.* One line. It calls the real `TickEngine.get_info`
  over a stub self, by design, so the stub had to answer the one new read
  (`_throttled_venues`). Without it that test failed — found and fixed here, not
  left for the gate.

**`condor/agents/journal.py` was declared and not touched.** No edit was needed:
`append_action(tick, action, reasoning)` already takes an arbitrary action label,
which is how `risk_blocked` and `bot_ownership_blocked` are written. The throttle
kind goes in through that existing seam as `venue_throttled`.

**What was deliberately not built.** No limiter, per alternative 2 — nothing in
either new module can refuse, delay or retry a call. The counters are per-process
and cumulative since the install came up; they are not persisted across a restart
and are not a time series, which is enough for "is this host refusing us now" and
"who is spending its budget" and stops short of the fleet view [[FEAT-141]] owns.
The log is reset rather than rotated once it passes 2 MB; the reader notices the
file shrank and keeps what it has already folded.

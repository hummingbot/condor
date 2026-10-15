---
id: FEAT-138
title: Count and surface rate-limit refusals per agent and venue, and attribute call volume to the agent that spends it
summary: Nothing in Condor counts a 429. RefusalLog records only risk-gate refusals, the sole per-server limiter is the dashboard poller, and per-request budgets exist only for GeckoTerminal. Throttling therefore reads as a mysterious strategy bug — one host was "intermittent" at 8 attempts, 8 failures, 27 refusals.
status: todo
effort: M
risk: low
brain: developer
new_files:
  - tests/test_throttle_accounting.py
touched_files:
  - mcp_servers/hummingbot_api/server.py
  - condor/agents/risk.py
  - condor/agents/journal.py
  - condor/web/routes/agents.py
  - frontend/src/lib/api.ts
depends_on: []
commits: []
created: 2026-10-14
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

1. Record throttle refusals in the tool path, with venue and agent.
2. Extend `RefusalLog` with the throttle kind; write to the journal.
3. Count per-agent call volume per host/venue on the same seam.
4. Expose the per-tick and per-agent counts, and the throttled condition, on the
   route and the dashboard.
5. Tests per the criteria below.

## Acceptance criteria

- [ ] A 429 from the tool path is counted against the calling agent and the venue, and appears in `RefusalLog` and the journal
- [ ] Eight attempts with eight refusals report 8 attempts / 0 successes / 8 refusals, so the #276 reading is reproducible from the counters rather than from logs
- [ ] A venue being refused is shown as a venue/connector condition, not as an agent error
- [ ] Per-agent call volume per host is reported, so the agent consuming a host's budget can be named
- [ ] Adding the counters changes no call's outcome, and no new throttle gate is introduced
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

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

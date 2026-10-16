---
id: FEAT-140
title: Resolve a Gateway swap stuck in SUBMITTED, and alert when one has been unverifiable too long
summary: A swap leaves SUBMITTED only after a successful status read, so a failing Gateway ping, a None status, an unparseable network or an exception keeps it SUBMITTED indefinitely. Condor shows an hourglass and offers get_status and search with no resolve route, so the only fix during the race was redeploying the agent.
status: done
effort: M
risk: medium
brain: developer
new_files:
  - condor/swap_watch.py
  - tests/test_swap_resolve.py
touched_files:
  - mcp_servers/hummingbot_api/tools/gateway_swap.py
  - mcp_servers/hummingbot_api/server.py
  - mcp_servers/hummingbot_api/profiles.py
  - mcp_servers/hummingbot_api/schemas.py
  - mcp_servers/hummingbot_api/formatters/gateway.py
  - handlers/dex/swap.py
  - condor/web/routes/agents.py
  - condor/runtime/danger.py
  - agents/_shared/skills/operate_your_loop/SKILL.md
  - tests/test_mcp_tool_profiles.py
  - tests/test_mcp_gateway_swap_tool.py
  - tests/test_dangerous_gate_names_resolve.py
  - tests/test_acp_permission_gate.py
depends_on: []
commits:
  - "7d13ee0d feat(dex): say how long a swap has been unverifiable, and resolve one without a redeploy (FEAT-140)"
created: 2026-10-14
closed: 2026-10-15
---

## Objective

An operator can resolve a swap that has been `SUBMITTED` and unverifiable, and
is alerted when one crosses a threshold, without redeploying the agent.

## Context and constraints

GitHub issue [#278](https://github.com/hummingbot/condor/issues/278): "One DEX
entry's swap got stuck waiting for confirmation, and nothing in the interface
could get it out of that state. The only fix was to redeploy the agent, which
cost race time and left the operator unsure what had happened on-chain."

Confirmed at `45e60d60`: `git grep resolve` over
`mcp_servers/hummingbot_api/tools/gateway_swap.py` returns nothing. Condor shows
`SUBMITTED` with an hourglass (`handlers/dex/swap.py:204-209`) and offers
`get_status` and `search` (`gateway_swap.py:185-220`). There is no resolve
action.

The polling is upstream (hummingbot-api
`services/gateway_transaction_poller.py`), and #278 records that a swap leaves
`SUBMITTED` only after a *successful* status read — so a failing Gateway ping, a
`None` status, an unparseable network string or an exception all keep it
`SUBMITTED` indefinitely. #278 is explicit that this is deliberate ("a swap
should never be aged out on a poll that couldn't check it") and that the gap is
the missing exit when the condition persists.

**Scope constraint, and it is the important one.** #278's first proposal is an
upstream route — `POST /gateway/swaps/{tx}/resolve` in hummingbot-api, plus
exposing how long a swap has been unverifiable. That is not in this repo. This
feature is the Condor half of #278 (its proposals 2 and 3), and the resolve
action depends on the upstream route existing. The alerting and runbook halves
do not, and are deliverable now.

## Alternatives considered

1. **Have Condor mark the swap resolved in its own state.** Rejected outright:
   Condor does not track swap confirmation and has no chain read, so this would
   be Condor asserting an on-chain outcome it cannot observe — the exact
   uncertainty the operator was left with.
2. **Have Condor read the chain directly to resolve.** Rejected: it duplicates
   Gateway's role, needs RPC credentials and a per-chain implementation, and
   would disagree with the poller.
3. **Alert only, no resolve action.** Deliverable without upstream, and leaves
   the operator with redeploy as the only exit — half the issue.
4. **Alert and surface the unverifiable duration now; expose the upstream
   resolve route as an MCP tool and dashboard action once it exists.** Chosen.

## Decision

Alternative 4, in two parts so the Condor-side value is not blocked on another
repo: the alert and the duration are built against what Condor can already see,
and the resolve action is wired to the upstream route behind a capability check,
reporting plainly when the server does not offer it.

## Design

- Condor computes how long a swap has been `SUBMITTED` from its own first
  observation, and alerts past a threshold. It reports this as "unverifiable for
  N" rather than "failed", since an unverified swap may well have landed.
- The resolve action calls the upstream route when the server offers it. When it
  does not, the action is absent or reports that the connected server does not
  support it — never a local state change, per alternative 1.
- The MCP tool and the dashboard action share the one call, so an agent and an
  operator cannot resolve by different routes.
- The stuck-swap recovery steps go in the agent runbook
  (`agents/_shared/skills/operate_your_loop/SKILL.md`), which is #278's third
  proposal and the part that would have saved the race time on its own.

## Implementation plan

1. Track first-observed `SUBMITTED` time and surface the unverifiable duration.
2. Alert past a threshold, as a venue condition rather than an agent error.
3. Add the resolve MCP tool and dashboard action against the upstream route,
   behind a capability check.
4. Add the recovery steps to the runbook skill.
5. Tests per the criteria below.

## Acceptance criteria

- [x] A swap observed `SUBMITTED` past the threshold raises an alert reporting how long it has been unverifiable
- [x] The alert reads as unverifiable, not as failed
- [x] The resolve action against a server offering the route marks the swap per the server's answer, and Condor sets no swap state of its own
- [x] Against a server not offering the route, the action reports that plainly and changes nothing locally
- [x] The runbook names the stuck-swap recovery steps
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared — **not ticked here**: this item shipped inside plan 89937873-9f5, whose final chunk runs the project's full gate once over everything the plan landed. What this run could show is below.

## Risks and notes

- Verified at `45e60d60` with `git show HEAD:<path>` for the Condor anchors; the
  poller behaviour and the missing upstream route are quoted from #278 and are
  **not** verifiable in this repo, which is stated here rather than presented as
  a confirmed local anchor.
- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60`
  with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0
  failed in 98.1s, EXIT=0**. Provenance, not a threshold.
- The upstream half of #278 (`POST /gateway/swaps/{tx}/resolve` and the
  inconclusive-poll counters) is **not filed**, being in hummingbot-api. If it
  never lands, criteria 1, 2, 4 and 5 are still deliverable and criterion 3
  degrades to the capability check — which is why the item is split this way
  rather than blocked.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.20
  `ARCH-012 [done]` (duplicated network display between trading and dex/swap),
  below the 0.50 fallback bar.

## Notes

Shipped in `7d13ee0d`.

**Verification.** 1870 tests passed, 0 failed, in 26.5s — every test file that
imports any module this item touched (`condor/web/routes/agents.py`,
`handlers/dex/swap.py`, `mcp_servers/hummingbot_api/{server,profiles,schemas,
formatters/gateway,tools/gateway_swap}.py`, `condor/runtime/danger.py`), plus
`tests/test_swap_resolve.py` (48 passed) and the shared-skill and agent-route
suites. `black` and `isort` clean on every touched path; the repo's `make lint`
is those two and nothing else. The absolute `uv run pytest` count in the
acceptance criteria is left to the plan's final verification chunk, which runs
the full gate once over the whole plan — the box stays unticked rather than
ticked on a partial measurement.

**Deviations from the declared files, all additive.**

1. `condor/swap_watch.py` is new and was not declared. The design says "the MCP
   tool and the dashboard action share the one call", and three surfaces need
   the same arithmetic (the Telegram history lines, the MCP search/status
   results, the dashboard route). Putting it in any one of them would have made
   the other two import it from there. It is the shape
   `condor/agents/throttle.py` already uses for a condition folded out of
   observations Condor already has.
2. `mcp_servers/hummingbot_api/{server,profiles,schemas,formatters/gateway}.py`
   were not declared. A new MCP tool cannot exist without them: `server.py`
   holds the `@mcp.tool()` function, `profiles.py` decides which seats mount it,
   `schemas.py` holds the `action` literal and `formatters/gateway.py` renders
   the result. Same four-file shape every tool in the family has.
3. `condor/runtime/danger.py` changed by one comment only, recording why
   `resolve_swap` stays ungated beside the rest of the swap family: it signs
   nothing and moves nothing, and a human in front of the only exit from a
   stuck swap is a brake that does not work for an unattended seat.
4. Four existing test files hold golden lists of the tool surface
   (`test_mcp_tool_profiles.py`, `test_mcp_gateway_swap_tool.py`,
   `test_dangerous_gate_names_resolve.py`, `test_acp_permission_gate.py`).
   `resolve_swap` was added to each, which is what those lists are for.

**One refinement to the design, deliberately.** The design said Condor computes
the duration "from its own first observation". It now prefers the swap row's own
recorded submission timestamp and falls back to the first observation, because
the recorded time survives a Condor restart and is the honest age of the swap,
while a first observation can only understate it. The row carries `since`
(`submitted` / `observed` / `unknown`) so a reader knows which it got, and
`unverifiable_for_seconds` is `None` rather than `0.0` when there is neither —
no statement is not a duration of zero.

**Criterion 3 and the upstream half.** `POST /gateway/swaps/{tx}/resolve` does
not exist in hummingbot-api yet and is not in the pinned
`hummingbot-api-client`, exactly as the doc anticipated. The call therefore goes
through `condor.fetchers.raw_api`, which is the repo's one place for a route the
pin does not wrap, and a 404 comes back as `SwapResolveUnsupported` — a
capability answer, never an outage. So criterion 3 is verified against a
*double* that serves the route (`tests/test_swap_resolve.py`:
`test_resolve_asks_the_server_and_returns_the_server_s_answer`,
`test_resolve_sets_no_swap_state_of_its_own`, and the MCP and dashboard
variants), which pins the request shape, the pass-through of the server's answer
and the absence of any Condor-side state. It cannot be verified against a live
server until the upstream route lands; no spin-off task is filed for that
because the upstream work is not in this repo and the doc already records it as
unfiled for that reason.

---
id: FEAT-140
title: Resolve a Gateway swap stuck in SUBMITTED, and alert when one has been unverifiable too long
summary: A swap leaves SUBMITTED only after a successful status read, so a failing Gateway ping, a None status, an unparseable network or an exception keeps it SUBMITTED indefinitely. Condor shows an hourglass and offers get_status and search with no resolve route, so the only fix during the race was redeploying the agent.
status: todo
effort: M
risk: medium
brain: developer
new_files:
  - tests/test_swap_resolve.py
touched_files:
  - mcp_servers/hummingbot_api/tools/gateway_swap.py
  - handlers/dex/swap.py
  - condor/web/routes/agents.py
  - agents/_shared/skills/operate_your_loop/SKILL.md
depends_on: []
commits: []
created: 2026-10-14
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

- [ ] A swap observed `SUBMITTED` past the threshold raises an alert reporting how long it has been unverifiable
- [ ] The alert reads as unverifiable, not as failed
- [ ] The resolve action against a server offering the route marks the swap per the server's answer, and Condor sets no swap state of its own
- [ ] Against a server not offering the route, the action reports that plainly and changes nothing locally
- [ ] The runbook names the stuck-swap recovery steps
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

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

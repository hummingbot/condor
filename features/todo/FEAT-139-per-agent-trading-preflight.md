---
id: FEAT-139
title: Per-agent trading preflight — capital in the exact asset, account mode, venue rules and pins
summary: doctor.py checks only the install and the trading rule/balance checks run when an executor is created, so an agent starts and then discovers its capital is in the wrong token or its account is in the wrong position mode. Add a condor doctor --agent X section, run at agent start, failing early with a readable reason.
status: todo
effort: L
risk: medium
brain: developer
new_files:
  - condor/preflight.py
  - tests/test_preflight.py
touched_files:
  - condor/doctor.py
  - condor/agents/engine.py
  - mcp_servers/hummingbot_api/tools/executor_create.py
  - condor/web/routes/agents.py
depends_on:
  - "[[CORR-729]]"
commits: []
created: 2026-10-14
---

## Objective

Before an agent starts trading, one check reports in plain language whether it
can: capital present in the exact asset and venue it needs, the account's actual
position mode and leverage, the venue rules for its pairs, clock and
reachability, and the pins the session will really run with.

## Context and constraints

GitHub issue [#277](https://github.com/hummingbot/condor/issues/277): "Several
entries lost race hours to problems a preflight would have caught before the
start." One agent was blocked for hours because its strategy required 780 USDC
*in USDC* while its capital sat in two other tokens of equal value. A wrong
hedge/one-way account mode turned an intended exit into an opening order.
Participants hit size and notional rejections and chose pairs whose fees made
volume work unviable.

Confirmed at `45e60d60`:

- `condor/doctor.py` checks the install only — dependencies, `.env`/config,
  placeholder credentials, dashboard bind, Tailscale, API reachability/auth
  (`main()`, `:709-716`) — and `main()` takes no `--agent` argument; there is no
  argparse at all.
- the partial checks that do exist run only when an executor is created:
  `_trading_rule_violation` (min size and notional,
  `mcp_servers/hummingbot_api/tools/executor_create.py:171`) and
  `_balance_violation` (`:303`). So they fire per order, after the agent is
  running, one failure at a time.
- position mode and leverage are validated only as *inputs* when they are set
  (`mcp_servers/hummingbot_api/tools/trading.py:45-55`). The account's current
  mode is never read before trading.

The constraint that shapes the design: the checks largely **exist**, as
per-executor validators. The feature is running them ahead of time, against the
strategy's stated requirements rather than against one order, and reporting them
together.

## Alternatives considered

1. **Extend `doctor.py`'s install checks with trading checks for all agents.**
   Rejected: the checks are per-agent by nature (which asset, which pairs, which
   account), so a global run has nothing to check against.
2. **Add the checks to the first tick and let the agent report them.** Rejected:
   this is a check whose value is being readable *before* capital is committed,
   and a model reporting it is the failure mode #277 describes ("participants
   hit rejections").
3. **Duplicate the validators in a preflight module.** Rejected on the repo's
   own recurring finding — a duplicated validator drifts from the one that
   actually gates orders, so the preflight would start passing things the
   executor path refuses.
4. **A preflight module that reuses the existing `executor_create.py` helpers,
   exposed as `condor doctor --agent X` and run automatically at agent start.**
   Chosen, and it is what #277 asks for.

## Decision

Alternative 4. `condor/preflight.py` composes the existing validators plus the
account and venue reads, surfaced as a `doctor --agent X` section and run at
agent start, failing early with a readable reason.

## Design

- The checks reuse `_trading_rule_violation` and `_balance_violation` rather
  than re-implementing them, so the preflight's answer and the executor path's
  answer cannot diverge. Anything the preflight needs that those helpers do not
  expose is lifted into a shared helper, not copied.
- The balance check is **per asset and per venue**, against the strategy's
  stated minimum — the 780-USDC case is a total-value check passing while the
  exact-asset check fails, so the figure compared has to be the asset the
  strategy needs.
- The account's position mode and leverage are **read from the account**, not
  taken from config, which is the gap at `trading.py:45-55`.
- Venue rules per configured pair - min size, min notional, tick size and fee
  tier - are reported as values, not as a pass/fail, since #277's fee complaint
  is a judgement the operator makes.
- Clock skew, venue reachability and connector health are reported alongside.
- The pins the session will actually run with come from the resolution
  [[CORR-729]] makes authoritative; before that item a preflight could report a
  pin that the start path would then silently replace, which would make the
  preflight actively misleading.
- Reports in plain language with one line per check, and distinguishes "failed"
  from "could not be checked" — a preflight that cannot reach a venue must not
  read as a pass.

## Implementation plan

1. Lift the shared bits of `_trading_rule_violation`/`_balance_violation` into
   helpers both the executor path and the preflight call.
2. Add `condor/preflight.py` with the checks above and the plain-language report.
3. Add argparse with `--agent` to `doctor.py` and wire the section.
4. Run it at agent start, failing the start with the readable reason.
5. Tests per the criteria below.

## Acceptance criteria

- [ ] A strategy needing 780 USDC with capital of equal total value in two other tokens fails preflight, naming the asset and the shortfall
- [ ] The same strategy with 780 USDC actually in USDC passes
- [ ] The account's position mode and leverage are read from the account and reported, and a mode mismatch against the strategy's requirement fails
- [ ] Min size, min notional, tick size and fee tier are reported per configured pair
- [ ] A venue that cannot be reached is reported as "could not be checked", distinct from a pass and from a failure
- [ ] The preflight reports the same verdict as the executor path for a min-size and a balance violation, asserted through the shared helpers rather than duplicated logic
- [ ] `condor doctor --agent X` runs the section, and agent start fails early with the reason
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Risks and notes

- Verified at `45e60d60` with `git show HEAD:<path>`; `doctor.py`'s lack of any
  argparse was confirmed by grepping the committed file for `add_argument`.
- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60`
  with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0
  failed in 98.1s, EXIT=0**. Provenance, not a threshold.
- `effort: L` and `risk: medium` because this fails an agent start, so a
  false negative stops a working agent. The sixth criterion (preflight and
  executor path agree) is the guard against that, and is why alternative 3 was
  rejected.
- `depends_on: [[CORR-729]]` for the pins check only. If that ordering is
  inconvenient the pins line could ship last, but a preflight that reports a pin
  the start path then silently overrides is worse than not reporting it.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.25
  `SEC-632 [done]` (NaN loss caps clearing the deploy gate), below the 0.50
  fallback bar — adjacent on pre-trade validation, different surface.

---
id: FEAT-139
title: Per-agent trading preflight — capital in the exact asset, account mode, venue rules and pins
summary: doctor.py checks only the install and the trading rule/balance checks run when an executor is created, so an agent starts and then discovers its capital is in the wrong token or its account is in the wrong position mode. Add a condor doctor --agent X section, run at agent start, failing early with a readable reason.
status: done
effort: L
risk: medium
brain: developer
new_files:
  - condor/preflight.py
  - tests/test_preflight.py
touched_files:
  - condor/doctor.py
  - condor/agents/engine.py
  - condor/agents/config.py
  - mcp_servers/hummingbot_api/tools/executor_create.py
  - mcp_servers/hummingbot_api/tools/trading.py
  - condor/web/routes/agents.py
  - Makefile
depends_on:
  - "[[CORR-729]]"
commits:
  - "22952d57 feat(agents): tell a loop whether it can trade before it starts (FEAT-139)"
created: 2026-10-14
closed: 2026-10-15
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

- [x] A strategy needing 780 USDC with capital of equal total value in two other tokens fails preflight, naming the asset and the shortfall
- [x] The same strategy with 780 USDC actually in USDC passes
- [x] The account's position mode and leverage are read from the account and reported, and a mode mismatch against the strategy's requirement fails
- [x] Min size, min notional, tick size and fee tier are reported per configured pair
- [x] A venue that cannot be reached is reported as "could not be checked", distinct from a pass and from a failure
- [x] The preflight reports the same verdict as the executor path for a min-size and a balance violation, asserted through the shared helpers rather than duplicated logic
- [x] `condor doctor --agent X` runs the section, and agent start fails early with the reason
- [x] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## What shipped

`condor/preflight.py` runs every check a loop's own config gives it something
to check against, and reports them as one line each with three states, where
``unknown`` ("could not be checked") is never folded into either verdict.

The strategy's "stated requirements" had nowhere to live, so
`AgentConfig.trading_requirements` (`TradingRequirementsConfig`) is the new
typed block: `connector`, `trading_pairs`, `account_name`, `capital_asset`,
`capital_amount`, `position_mode`, `leverage`. Two defaults keep the common
config from having a second place to edit: `capital_asset` falls back to the
quote currency of the first pair, and `capital_amount` to
`total_amount_quote` — but only when the asset being checked *is* that quote,
since comparing a quote budget against a base holding would invent a
requirement nobody stated.

One definition of a validator, as the Design demanded. The order-viability
check sizes the order this loop's capital would place and hands it to
`executor_create.trading_rule_violation` and `balance_violation` — the very
functions `create_executor` refuses with — and reports their refusal text
verbatim. Those two, plus the reads under them (`balance_rows`,
`available_units`, `describe_balances`, `reference_price`, `numeric_field`,
`usd`) are public now instead of underscored, because they have a second
caller; nothing was copied. The two tests for criterion 6 assert the
preflight's detail is byte-for-byte the string `create_position_executor`
returns for the same inputs. `normalize_position_mode` was lifted into
`tools/trading.py` the same way rather than duplicated, so `one-way` and
`ONEWAY` cannot read as a mismatch.

The gate is `TickEngine._preflight_gate`, called first in `start()`, so the
dashboard start *and* the boot restart go through one gate; `_start` maps
`PreflightFailed` to a 422 naming the reason, and a refused start records
`LoopState.ERROR` rather than leaving its session folder stateless.

### What refuses a start, and what only reports

`risk: medium` was about a false negative stopping a working agent, so every
check was sorted by whether it can actually be sure:

- **refuses**: the capital in the exact asset (after the create path's own
  `refresh=True` re-read, so a cache that has not seen a deposit cannot
  block), a position-mode or leverage mismatch read from the account, the
  create path's own two validators, a pinned server that cannot be used
  (CORR-729's `ServerUnavailable`), and a clock more than 30s off the API
  host;
- **reports only**: venue reachability and the account's connector credential
  — `GET /connectors` answers the API's CEX connectors, and a gateway/DEX
  connector is listed by the gateway and trades from a wallet, so an absence
  there cannot tell a misconfigured venue from a good on-chain one; the
  venue's rules (values, per the Design, since the fee judgement is the
  operator's); and a clock 5–30s out, where the `Date` header's resolution and
  the round trip mean the measurement cannot say which of the two clocks is
  wrong.

Two narrowings on top: a config that states no `trading_requirements` is not
gated at all and makes no API read, and an experiment (`dry_run`/`run_once`)
is reported on but never stopped. So no existing agent's start behaviour
changed.

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

## Closing notes

- Gate on the change: `uv run pytest` = **6721 passed, 2 skipped, 0 failed in
  100.3s, EXIT=0**, in this checkout at `87043ad6` + this work, `frontend/dist`
  present (without it 21 web-route tests skip silently). The count is above the
  doc's `45e60d60` provenance figure of 6427 because the plan has landed other
  items since; nothing failed, so there is no failure to attribute and no
  merge-base run was needed ([[CORR-414]]). The plan's final verification chunk
  runs the gate once over everything it landed.
- `tests/test_preflight.py` (38 tests) plus every suite that touches the
  changed modules — 1506 tests across the files importing `TickEngine`,
  `AgentConfig`, `load_full_config`, `resolve_stored_server`, `condor.doctor`,
  `executor_create` and `set_position_mode_and_leverage` — pass.
- Deviations from the design, all recorded above: `AgentConfig` gained the
  requirements block (the design assumed the stated requirements already
  existed somewhere; they did not); `tools/trading.py` and the `Makefile` are
  touched beyond the declared list, the first to lift the position-mode
  normalizer instead of copying it, the second because there is no `condor`
  console script — the real invocation is
  `uv run python -m condor.doctor --agent SLUG[.LOOP]`, with
  `make doctor AGENT=x` as the documented front door.
- No Hummingbot API server is configured in this checkout
  (`config_manager.list_servers()` is empty), so the venue and account reads
  were exercised against fakes shaped to the `hummingbot_api_client` router
  signatures and to the payload wrappings the repo's own callers read
  (`{"data": [...]}` for positions). The doctor section itself was run end to
  end against a real agent on disk
  (`python -m condor.doctor --agent adaptive_grid_trader --only-agent`).
- `depends_on: [[CORR-729]]` was satisfied before this started: it is in
  `improvements/backend/done/`, and the pins check resolves through the
  `resolve_stored_server` it made authoritative, carrying `pinned` the same
  way the start path does.

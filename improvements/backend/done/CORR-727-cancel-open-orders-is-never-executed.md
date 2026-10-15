---
id: CORR-727
title: cancel_open_orders is configured, defaulted true and documented, but no code path ever cancels an order
summary: ShutdownPolicy.cancel_open_orders defaults to true and ships in two agent shutdown.md files, yet the flag only ever reaches the LLM context as text. Every grep hit is config parsing, the prompt string or a test — nothing cancels an order, so a stop's claim rests on a model's turn.
category: correctness
impact: high
effort: M
risk: medium
status: done
brain: developer
files:
  - condor/agents/shutdown.py:45
  - condor/agents/shutdown.py:284
  - condor/agents/engine.py:352
  - agents/_defaults/shutdown.md:3
depends_on: []
commits:
  - "155b8cdd fix(shutdown): execute cancel_open_orders instead of describing it (CORR-727)"
created: 2026-10-14
closed: 2026-10-15
---

## Problem

GitHub issue [#273](https://github.com/hummingbot/condor/issues/273) reports
that at race close-out one archived agent still listed two Gate spot
`LIMIT_MAKER` sells as non-terminal, and proving what had actually happened on
the venue took an operator.

The first cause is confirmed exactly as reported. `git grep cancel_open_orders`
at `45e60d60` returns twelve hits and **not one of them cancels anything**:

- `condor/agents/shutdown.py:45`, `:48`, `:60`, `:66`, `:72` — the policy field,
  defaulted `True`, parsed and rendered
- `condor/agents/shutdown.py:284` — the only consumer, interpolating
  `f"cancel_open_orders={policy.cancel_open_orders}."` into the LLM context
- `agents/_defaults/shutdown.md:3` and
  `agents/solana_dex_lp_expert/shutdown.md:3` — shipped as `true` with the
  comment "cancel resting orders during winddown"
- `tests/test_shutdown.py:47`, `:56`, `:59`, `:65` — asserting the parse and the
  default, never an effect

So a setting that reads as a guarantee is a suggestion to a language model. If
the model does not act on that sentence, or the session ends before it does,
resting orders survive a stop that reported success — and the shipped default
says `true`, so every agent inherits the appearance of the guarantee.

Condor also has no cancel-order tool to call even deliberately: the MCP trading
tools expose `stop_executor` (`mcp_servers/hummingbot_api/tools/trading.py:7`)
and no order cancel, which is why the flag was never wired to anything.

## Proposed solution

1. Execute the policy deterministically in the stop and shutdown paths rather
   than describing it. `_finish` (`condor/agents/engine.py:352-389`) and the
   emergency shutdown path both consult the resolved `ShutdownPolicy` already;
   when `cancel_open_orders` is true, cancel this session's resting orders in
   code before the session is finished, and record what was attempted.
2. Where no cancel capability exists for a venue path, say so in the shutdown
   record instead of leaving the flag reading as done. A policy that cannot be
   carried out must report that it could not, which is the distinction #273 is
   really about.
3. Keep the prompt sentence, but derive it from the executed outcome so the
   context cannot claim an action the code did not take.

What was cancelled and what was left — checked against the venue rather than
local rows — is the close-out report, and that is [[FEAT-134]]. This item is
only the missing execution.

## Acceptance criteria

- [x] A graceful stop with `cancel_open_orders: true` and one resting order issues a cancel in code, with no LLM turn involved
- [x] A graceful stop with `cancel_open_orders: false` issues none
- [x] A venue or connector with no cancel path available records that the policy could not be carried out, rather than reporting success
- [x] A cancel that fails does not abort the stop, and is recorded as left behind
- [x] The shutdown context sentence reflects what was executed rather than the configured flag
- [x] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Notes

- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60` with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0 failed in 98.1s, EXIT=0**. Quoted as provenance, not as a threshold — unrelated items add tests.
- Verified at `45e60d60`: the twelve `git grep cancel_open_orders` hits are
  listed above in full, which is the whole argument — there is no path to miss.
- The policy text was shipped by `features/done/FEAT-007` (shutdown
  instructions for strategies), whose scope was the instruction. This item is
  not a regression against it; it is the execution that was never added beside
  it.
- #273's fourth proposal is upstream in hummingbot-api (fall back to the venue's
  open orders when an order is absent from `in_flight_orders`, and add retention
  for terminated executors). That is not actionable in this repo and is **not**
  filed here; the Condor-side half of the retention point is [[PERF-733]].
- `find_similar_backlog` (token-overlap fallback, 196 docs) returned no hit
  above 0.21; `grep -rl cancel_open_orders improvements/ features/ tasks/`
  found only `features/done/FEAT-007`, read and discussed above.

## Resolution

`cancel_resting_orders` (`condor/agents/shutdown.py`) executes the policy and a
`CancelOutcome` records what the execution achieved, separately from what the
flag asked for. `run_shutdown` sweeps after its baseline stop and hands the
outcome to `_build_llm_context`, so the context sentence is derived from the
result; `TickEngine._finish` — the one teardown every exit path goes through —
sweeps for a graceful stop and skips when the winddown already left an outcome
on the engine.

Scope is this session's own `(connector, trading_pair)` markets, taken from its
executor rows. That is as narrow as the API allows: an active-order row carries
its account, connector and pair and no controller or executor id, so there is no
per-session order tag to filter on. Orders outside those markets are never
touched.

Deviations and decisions, each deliberate:

- The doc says `_finish` "consults the resolved `ShutdownPolicy` already". It did
  not — it had no policy at all. It loads it now.
- The sweep sits in `_finish` rather than in `stop()` so every exit path (a
  graceful stop, a completed run, an error, `max_ticks`) honours the policy, not
  only the one the user triggers. A `dry_run` is skipped: the risk gate refuses
  every mutating call in that mode, so the session cannot have placed an order.
- Three cases beyond the criteria also record the policy as **not** carried out,
  for the same reason criterion 3 exists: a controller-mode session (its orders
  live inside its bots' own Hummingbot instances, which `/trading` neither lists
  nor cancels — `stop_bot(skip_order_cancellation=False)` is that path and it is
  not this stop), a failed active-order read, and a read that filled its page.
- `condor/fetchers/orders.py` gained optional `connector_names` / `trading_pairs`
  passthrough. Not an optimization: the backend returns the first `limit` of
  *every* account's in-flight orders, so an unfiltered read on a busy server can
  page this session's orders out and read as an empty book — a clean sweep that
  is not one.
- `agents/solana_dex_lp_expert/shutdown.md:3` was edited beside the declared
  `agents/_defaults/shutdown.md:3`: both shipped the same misleading comment, and
  the LP one claimed to cancel "Gateway orders", which is exactly the case that
  has no cancel path. Both bodies now point the cleanup pass at what the
  deterministic cancel achieved instead of asking it to do the cancel.
- `condor/agents/shutdown.py:45` (the `cancel_open_orders` field, still defaulting
  `True`) is unchanged by design: the default was never the bug. It now means
  what it says.

Verification, in this workspace on `ws/issues-of-agent-builder-cup` at
`155b8cdd`:

- `uv run pytest tests/test_shutdown.py` — **60 passed**, 12 of them new, one per
  criterion (`test_graceful_stop_cancels_a_resting_order_in_code`,
  `test_graceful_stop_cancels_nothing_when_the_policy_says_false`,
  `test_a_market_with_no_cancel_path_is_recorded_not_reported_done`,
  `test_a_controller_mode_session_records_its_bot_as_out_of_reach`,
  `test_a_failed_cancel_leaves_the_stop_standing_and_is_recorded`,
  `test_the_winddown_sweeps_and_the_context_reports_what_it_executed`, …).
- `uv run pytest` (full suite) — **6502 passed, 2 skipped, 0 failed in 95.9s**,
  so nothing that passed at the base `eddc8009` fails here. The doc's `45e60d60`
  provenance figure of 6427 is lower because other items on this branch added
  tests; the failure count, not the total, is the criterion.
- `uv run black --check` / `uv run isort --check-only` over the six touched
  paths — clean.

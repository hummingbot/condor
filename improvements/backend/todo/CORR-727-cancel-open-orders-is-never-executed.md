---
id: CORR-727
title: cancel_open_orders is configured, defaulted true and documented, but no code path ever cancels an order
summary: ShutdownPolicy.cancel_open_orders defaults to true and ships in two agent shutdown.md files, yet the flag only ever reaches the LLM context as text. Every grep hit is config parsing, the prompt string or a test — nothing cancels an order, so a stop's claim rests on a model's turn.
category: correctness
impact: high
effort: M
risk: medium
status: todo
brain: developer
files:
  - condor/agents/shutdown.py:45
  - condor/agents/shutdown.py:284
  - condor/agents/engine.py:352
  - agents/_defaults/shutdown.md:3
depends_on: []
commits: []
created: 2026-10-14
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

- [ ] A graceful stop with `cancel_open_orders: true` and one resting order issues a cancel in code, with no LLM turn involved
- [ ] A graceful stop with `cancel_open_orders: false` issues none
- [ ] A venue or connector with no cancel path available records that the policy could not be carried out, rather than reporting success
- [ ] A cancel that fails does not abort the stop, and is recorded as left behind
- [ ] The shutdown context sentence reflects what was executed rather than the configured flag
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

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

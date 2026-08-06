---
name: lp_pool_review
description: Compare valid Orca snapshot candidates using MCDA, raw activity, Whirlpool mechanics, and portfolio fit without turning rank into a command.
when_to_use: Read only when two or more valid lp_snapshot candidates are close, unusual, contradictory, or need deeper rank-override or diversification judgment than the Strategy fast path.
references_routine: lp_snapshot
source: agent:lp_expert
---

# LP Pool Review

Use this playbook only after `lp_snapshot` has supplied technically valid current
evidence. It cannot repair incomplete coverage, identity, token registration, or
range feasibility.

1. Confirm canonical Orca orientation: token A is the base asset and token B is
   canonical USDC. Treat exact pool address, mint, symbol, decimals, price
   orientation, fee metadata, and `tick_spacing` as one identity.
2. Remember that Orca Whirlpools use ticks and tick spacing, not Meteora-style
   bins or strategy types. Let deterministic snapshot/create logic align and
   validate tick bounds; do not estimate technical feasibility in prose.
3. Compare the full returned candidate set using neutral MCDA components, raw
   fees and volume, hourly and sustainable yield, activity persistence,
   acceleration, turnover, price movement, confidence, and current portfolio
   overlap.
4. Treat neutral rank as comparison evidence, not an automatic selector. Prefer
   a lower-ranked candidate only when current evidence supports a more
   sustainable opportunity, better strategic range, or useful diversification.
5. Reject only the candidate whose valid evidence has deteriorated. Do not turn
   one unsuitable pool into a portfolio-wide HOLD.
6. Explain every rank override and candidate rejection with specific returned
   facts. Venue categories describe evidence; they are not token-security
   certification.
7. Return judgment only. Allocation, exact range feasibility, inventory
   preparation, admission, and execution remain with the Strategy and routines.

Risk posture changes evidence emphasis only. It cannot weaken technical gates or
capital limits.

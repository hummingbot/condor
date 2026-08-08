---
name: lp_pool_review
description: Compare valid Orca pool-scan candidates using MCDA, raw activity, Whirlpool mechanics, and portfolio fit without turning rank into a command.
when_to_use: Read only when two or more valid lp_pool_scan candidates are close, unusual, contradictory, or need deeper rank-override or diversification judgment than the Strategy fast path.
references_routine: lp_pool_scan
source: agent:lp_expert
---

# LP Pool Review

Use this playbook only after `lp_pool_scan` has supplied technically valid current
evidence. It cannot repair incomplete coverage, identity, Gateway metadata
conflicts, or range feasibility. A merely absent Gateway token is preparation
work for the selected candidate, not pool-quality evidence.

1. Confirm canonical Orca orientation: candidate `base` is ordered
   `[symbol, mint, decimals]`, token A is that base asset, and token B is
   canonical USDC. Treat `pool`, `base`, `price`, `spacing`, and `lens` as one
   exact identity and pass the entire candidate unchanged.
2. Remember that Orca Whirlpools use ticks and tick spacing, not Meteora-style
   bins or strategy types. Let deterministic swap/create logic align and
   validate tick bounds; do not estimate technical feasibility in prose.
3. Compare the returned top-ranked candidate prefix using `rank`, `score`, `tvl`,
   `yield_24h`, `yield_floor_h`, `accel_1h`, `turnover_24h`, `move_24h`,
   `sources`, `tvl_x`, the configured TVL policy, and current portfolio overlap.
   The hard TVL floor cannot be waived. A stricter context may raise the
   configured default posture target, while a looser context cannot lower it.
   `mcda` is ordered
   `[fee_productivity, recent_activity, price_stability, liquidity_depth,
   execution_simplicity]`. When `transport_limited=true`, lower-ranked valid
   pools remain in the human report but are not Agent trading authority.
4. Treat neutral rank as comparison evidence, not an automatic selector. Prefer
   a lower-ranked candidate only when current evidence supports a more
   sustainable opportunity, better strategic range, or useful diversification.
5. Reject only the candidate whose valid evidence has deteriorated. Do not turn
   one unsuitable pool into a portfolio-wide HOLD.
6. Explain every rank override and candidate rejection with specific returned
   facts. Compact venue evidence is not token-security certification; the
   full routine report is for human diagnosis, not extra execution authority.
7. Return judgment only. Allocation, exact range feasibility, inventory
   preparation, and admission remain with the Strategy and routines; native
   `manage_executors` performs only an unchanged routine-emitted transition.

Risk posture changes evidence emphasis only. It cannot weaken technical gates or
capital limits.

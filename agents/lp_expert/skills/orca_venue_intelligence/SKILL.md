---
name: orca_venue_intelligence
description: Interpret Orca Whirlpool pool discovery, Orca API metrics, token orientation, tick spacing, tick alignment, tick-array feasibility, and CLMM-specific range constraints.
when_to_use: Read only when Orca scan or planner evidence is missing, stale, contradictory, or technically unfamiliar; the ordinary Strategy-declared scan and plan path needs no skill read.
references_routine: orca_pool_scan
source: agent:lp_expert
---

# Orca Venue Intelligence

1. Run `orca_pool_scan` under the exact active Orca Strategy and require complete
   category/lens coverage before deployment.
2. Treat its output as normalized recommendations: metrics, categories, MCDA
   components, neutral rank, technical rejections, and coverage—not a selected
   pool, rigid risk-profile score, or trading action.
3. Surface the returned scan `report_id` in the tick response and journal. A
   report failure is diagnostic and must be stated, but it does not change valid
   scan evidence.
4. Require canonical USDC quote orientation and verify pool address, base mint,
   symbols, decimals, current price, fee metadata, warning state, and
   `tick_spacing` against fresh detail before planning.
5. Use Orca tick spacing and the deterministic Orca planner to align bounds and
   validate the venue's actual range feasibility. Do not apply Meteora bin-step,
   bin-width, or strategy-type rules to a Whirlpool.
6. Use tick-liquidity or tick-array evidence when available to judge where
   liquidity is concentrated, but never estimate a technically valid range in
   prose when the planner can verify it.
7. Hand the normalized scan to `lp_pool_review`, then hand venue constraints to
   `lp_range_and_inventory`. Keep final pool and range choices with the LLM.

Read `orca-contract.md` when interpreting scan fields, price orientation, or
Orca-versus-bin-based range mechanics.

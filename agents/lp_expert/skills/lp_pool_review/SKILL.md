---
name: lp_pool_review
description: Build an ordered LP candidate set using MCDA, raw yield and activity evidence, categorical context, rankings, portfolio fit, and dynamic risk posture without delegating selection to a score.
when_to_use: Read only when normalized scan evidence is incomplete, contradictory, technically unfamiliar, or needs deeper portfolio comparison than the Strategy fast path; an ordinary complete scan needs no skill read.
source: agent:lp_expert
---

# LP Pool Review

1. Require the active venue's complete source contract and technical eligibility.
   Incomplete coverage, stale identity, or a non-deployable result means HOLD.
2. Compare the full returned candidate set using neutral MCDA score and
   components, active-posture judgment, raw fees and volume, hourly fee yield,
   sustainable yield, acceleration, turnover, price movement, categories,
   confidence, and current portfolio overlap.
3. Treat MCDA as the core comparison framework, not an automatic selector. A
   lower-ranked pool may be preferable when raw evidence supports a stronger
   current opportunity, more sustainable activity, a better feasible range, or
   useful diversification.
4. Build an ordered candidate set large enough to fill every clean portfolio
   slot, plus alternates. Prefer distinct pools and useful diversification.
5. Fresh-validate candidates in order. Reject only the candidate whose evidence
   fails; continue to the next candidate. Do not turn one declining, stale, or
   unsuitable pool into a portfolio-wide HOLD.
6. If a recently rejected pool has no materially improved evidence, move on
   instead of selecting it again. Explain candidate rejection and every
   neutral-rank override using specific returned evidence.
7. Select up to clean capacity or HOLD only the unfilled slots after the
   remaining candidate universe or a portfolio-wide gate is exhausted. Leave
   allocation, range, inventory preparation, and execution to their own
   decisions.

Venue categories describe the scan universe; they are not token-security
certification. Risk posture changes evidence emphasis, never technical gates or
capital limits. Read the active venue-intelligence skill for source meanings and
venue-specific constraints.

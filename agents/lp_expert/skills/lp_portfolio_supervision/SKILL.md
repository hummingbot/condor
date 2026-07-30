---
name: lp_portfolio_supervision
description: Supervise multiple standardized LP executors as one bounded portfolio, preserving exact ownership, independent capacity, dynamic risk posture, and serialized portfolio actions.
when_to_use: Read only for a genuine ownership, lifecycle, capacity, terminal, or multi-executor ambiguity after exact executor evidence; a healthy active LP follows the Strategy fast path without a skill read.
source: agent:lp_expert
---

# LP Portfolio Supervision

1. Reconcile any uncertain current-session mutation before making a new
   decision. A journaled intent alone is not a mutation. For an interrupted
   Gateway operation, use its exact current-session receipt to distinguish
   absent/rejected, confirmed/pending, and uncertain submission states.
2. Use exact current-controller executor detail and its embedded on-chain LP
   position as active-LP authority. Preserve one executor's identity
   independently from every other executor and keep foreign resources read-only.
   `positions_summary` contains executor-held residual positions, not active LPs;
   a running LP plus an empty held summary is expected and not contradictory.
3. For an active portfolio, derive clean slots from nonterminal
   current-controller executors, configured capacity, remaining capital, and
   unresolved mutation state. After CLOSE, classify capacity as reusable only
   after terminal executor, absent embedded LP position, and required residual
   restoration evidence agree. Opening, closing, swapping,
   failed-but-unreconciled, or ambiguous resources still occupy capacity.
4. Use the Strategy's exact limit check every tick as the authority for both
   session and per-executor age, stop-loss, and take-profit triggers.
   `session.stop_latched=true` requires portfolio-wide wind-down and remains
   authoritative after the portfolio is flat, so it cannot redeploy. A triggered
   executor is a mandatory close target when its lifecycle is stoppable; if it
   is already closing, swapping, or failed, reconcile without another stop. Do
   not replace or clear a configured threshold with discretionary judgment.
5. For a non-latched session, assess the portfolio using remaining capital,
   same-pool overlap, diversification, correlated exposure, replacement
   opportunity, and the active dynamic risk posture. Assess non-triggered
   executors using lifecycle state, in-range status, range distance, fees and net
   yield, inventory drift, pool quality change, age, loss, and technical
   warnings.
6. Choose exactly one portfolio action allowed by the Strategy: deploy into
   clean slots, close selected executors, or HOLD. A DEPLOY or CLOSE action may
   operate on multiple distinct executors up to current configured capacity.
7. Execute each executor's transition chain serially. Continue to the next
   selected pool or executor only after the previous chain confirms and
   refreshed state remains clean. Stop the batch immediately when confirmation
   is missing.

Use configured limits as hard gates. Use market and portfolio evidence for
judgment; do not invent fixed close thresholds merely to make the decision
mechanical.

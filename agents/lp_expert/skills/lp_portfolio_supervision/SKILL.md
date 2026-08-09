---
name: lp_portfolio_supervision
description: Interpret a bounded multi-executor LP portfolio while preserving independent ownership, clean capacity, and serialized actions.
when_to_use: Read only when valid lp_snapshot evidence leaves a genuine multi-executor ownership, lifecycle, capacity, diversification, or supervision ambiguity.
references_routine: lp_snapshot
source: agent:lp_expert
---

# LP Portfolio Supervision

1. Treat every current-controller executor, pool, position, preparation receipt,
   stop, and cleanup as an independent chain. Foreign resources stay read-only.
2. Use `lp_snapshot` as the authority for lifecycle, close triggers, stop latch,
   occupied pools, capital, clean slots, and quarantined capacity. Do not rebuild
   those values from broad tool reads.
3. A slot is clean only when no opening, active, closing, cleanup-pending,
   uncertain, ambiguous, or failed-but-unreconciled resource still owns it.
   If snapshot supplies an unresolved operation's `reconcile.config`, run it
   unchanged before scanning or considering another mutation; do not rebuild it
   from a journal or report. If it supplies `continue_create.config` for a
   confirmed preparation with no create attempt, continue that LP admission
   first; the routine restores its frozen selection from the preparation
   receipt. If it supplies `restore.config` after a create was definitely
   rejected, restore that exact output before scanning; never include
   pre-existing wallet base. For a legacy admitted create without a recorded
   executor ID, accept a snapshot recovery capsule only when one and only one
   current-controller executor matches the complete frozen request; zero or
   multiple matches remain quarantined.
4. The portfolio may hold up to its configured limit and may prepare/create no
   more than the current configured deployment limit per tick. Use the
   second value in snapshot `selection_constraints.deployments`
   (`[configured, available_this_tick]`), not shipped defaults.
5. Apply per-executor age, take-profit, and stop-loss triggers independently.
   The session stop latch is portfolio-wide and cannot be cleared by judgment.
6. For non-triggered executors, compare range condition, range distance, fee and
   net yield, inventory drift, pool-quality change, age, replacement opportunity,
   correlated exposure, and diversification.
7. Keep at most one active or reconciling executor per pool. Executors sharing a
   base mint still own separate attributed inventory.
8. Choose one portfolio action: deploy an ordered bounded set of independent
   chains, close an ordered set of exact targets, or HOLD. Within DEPLOY, finish
   each two-phase routine admission → native executor create → exact routine
   reconciliation chain before starting the next. Stop the affected scope when
   a native identity or confirmation is missing.
9. A closed slot remains quarantined until a following tick proves no material
   residual or confirms exact cleanup. Never release or reuse it from a broad
   balance change.

Use configured limits as hard gates. This playbook supplies portfolio judgment,
not alternate arithmetic or mutation authority.

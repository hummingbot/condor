---
name: lp_close_and_recovery
description: Order exact LP closes and handle following-tick residual verification without racing native close-out or cross-selling wallet inventory.
when_to_use: Read when closing an executor, reconciling a prior close, handling an uncertain stop or swap, or deciding an exact residual-cleanup versus manual-quarantine path.
source: agent:lp_expert
---

# LP Close and Recovery

## Stop tick

1. Select and order exact current-controller close targets from `lp_snapshot`.
   Keep executor, LP position, pool, base mint, and every other executor's
   inventory identity independent.
2. Record one loop-mode CLOSE intent with `keep_position=false`, exact targets,
   and the fact that post-close verification is due on the following tick.
3. Submit one native stop per target and reconcile only that exact executor.
   Never repeat a stop whose submission may have occurred.
4. Do not submit a residual swap on the stop tick. Native close-out owns this
   tick and must not be raced by a fallback.

## Following tick

5. Begin with `lp_snapshot` and prioritize the exact prior close before
   deployment. Require authoritative terminal executor, absent LP position, and
   native close-out evidence tied to that executor.
6. If close-out is pending, continue read-only reconciliation. If evidence is
   conflicting, missing, or matches several owners, quarantine it for manual
   review.
7. If no material attributable residual remains, or only configured economic
   dust remains, record that fact. Capacity can be released only when the
   snapshot proves the release condition.
8. If one material base residual is exactly attributable after native
   restoration failed, was omitted, or incorrectly reported success, use
   `lp_swap` once with a new cleanup operation ID to sell exactly that amount to
   canonical USDC.
9. Confirm the cleanup receipt and refreshed scoped evidence before releasing
   capacity. An uncertain cleanup never permits a retry.
10. Never use total wallet balance, a broad balance delta, or same-mint inventory
    owned by another executor as the cleanup amount.

Run-once cannot assume this following tick. A run-once stop whose native
restoration is not proven clean requires manual post-close verification and may
not perform same-tick cleanup.

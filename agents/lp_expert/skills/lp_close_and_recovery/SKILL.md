---
name: lp_close_and_recovery
description: Close one exact standardized LP executor, reconcile its position and native close-out, and restore only a provably attributable residual base amount.
when_to_use: Read when closing an LP executor, supervising a terminal executor, or reconciling an uncertain stop, close-out swap, or fallback restoration.
source: agent:lp_expert
---

# LP Close and Recovery

1. Select one exact current-controller executor and its exact LP position.
2. Record or snapshot one close intent according to execution mode, including
   executor identity, `keep_position` choice, and the conditional restoration.
3. Submit one stop request and reconcile it. Never repeat a stop whose submission
   may have occurred.
4. Require terminal executor evidence and prove the exact LP position absent.
   Until then, keep its capacity occupied.
   If the native stop/close result timed out or is unknown and exact executor
   recovery remains inconclusive, use the read-only finalized Solana fallback
   once with the exact wallet signer, active venue program, pool, known position
   address, operation window, and bounded expected asset changes. One exact
   on-chain match prevents another stop; it does not replace terminal
   executor/position evidence.
5. Inspect the executor's native close-out swap and receipt. If it succeeded,
   perform no fallback.
6. If it provably failed and exact executor evidence identifies a material
   unswapped base remainder, quote and execute one fallback sale of that
   attributable amount to the active Strategy's quote token.
7. Preserve account, network, wallet, provider, pair, side, operation, reserve,
   and receipt identity through transaction-status reconciliation and portfolio
   refresh.
8. Preserve each Gateway status/execute `report_id` or `report_error` in the
   close trace for later inspection.
9. Leave zero-match, multiple-match, broad-balance-only, or contradictory
   inventory evidence for manual review. Never sell total wallet base.

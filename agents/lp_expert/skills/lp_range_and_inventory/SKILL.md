---
name: lp_range_and_inventory
description: Interpret strategic Orca range and inventory choices while exact feasibility, attribution, and execution remain deterministic.
when_to_use: Read only when valid lp_snapshot range alternatives, exact inventory attribution, or a post-swap replan needs strategic interpretation beyond the ordinary Strategy path.
source: agent:lp_expert
---

# LP Range and Inventory

Use this playbook to interpret valid routine evidence, not to reconstruct range
math, balances, or swap receipts.

1. Choose strategic range intent from current volatility, liquidity
   distribution, fee opportunity, expected holding horizon, active risk posture,
   and the candidate's role in the existing portfolio.
2. Compare only range alternatives already bounded and tick-aligned by
   `lp_snapshot`. Do not invent price bounds, ticks, token decimals, or
   feasibility when routine evidence is incomplete.
3. Treat the selected allocation as a maximum commitment. Hard capital,
   slippage, reserve, and range limits remain authoritative and cannot be widened
   by qualitative judgment.
4. Ignore unrelated wallet inventory. A base amount is usable only when one
   exact current-session preparation receipt attributes it to the selected
   candidate.
5. Use `lp_swap` only for the exact preparation or restoration transition
   already selected by the Strategy. Never infer an amount from total wallet
   balance.
6. After a confirmed preparation swap, let `lp_create` refresh price, align the
   final range, validate amounts and schema, and decide technical admission.
   Interpret a changed valid range; do not bypass a deterministic rejection.
7. If a plan is no longer strategically suitable before create, leave the exact
   attributed inventory quarantined for its declared restoration path. If any
   mutation is uncertain, stop and reconcile rather than selecting another use
   for it.

Return the strategic judgment and evidence used. Reports and routine results
remain diagnostic and execution authority respectively.

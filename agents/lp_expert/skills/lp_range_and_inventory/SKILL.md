---
name: lp_range_and_inventory
description: Interpret strategic Orca range and inventory choices while exact feasibility, attribution, and execution remain deterministic.
when_to_use: Read only when valid lp_pool_scan candidates and lp_snapshot range bounds, exact inventory attribution, or a post-swap replan need strategic interpretation beyond the ordinary Strategy path.
source: agent:lp_expert
---

# LP Range and Inventory

Use this playbook to interpret valid routine evidence, not to reconstruct range
math, balances, or swap receipts.

1. Choose strategic range intent from current volatility, liquidity
   distribution, fee opportunity, expected holding horizon, active risk posture,
   and the candidate's role in the existing portfolio.
2. Select a range half-width only inside `lp_snapshot`'s returned bounds.
   `selection_constraints.range_half_width_pct` is ordered
   `[minimum, maximum]`; `allocation_quote` is
   `[minimum, maximum, remaining_portfolio_budget]`.
   `lp_order_request` constructs the initial tick-aligned plan and
   `lp_executor_request`
   refreshes it. Do not invent price bounds, ticks, token decimals, or
   feasibility.
3. Treat the selected allocation as a maximum commitment. Hard capital,
   slippage, reserve, and range limits remain authoritative and cannot be widened
   by qualitative judgment.
4. Treat wallet base as usable only when frozen
   `use_existing_base_inventory=true` and `lp_order_request` authorizes an exact
   amount for this candidate. It caps that amount at the LP requirement. For
   SOL, it first subtracts `min_sol_reserve`; never allocate the reserve.
   Unattributed wallet inventory remains unavailable.
5. For preparation, pass only the unchanged candidate plus selected allocation
   and range half-width. Let `lp_order_request` derive the exact token identity,
   base amount, quote cap, slippage, plan, and exact `order_executor` request.
   This routine cannot submit or transfer funds. Submit that request unchanged
   through native `manage_executors`, then reconcile the returned identity using
   only controller, operation ID, and `swap_executor_id`. Never infer an amount
   from total wallet balance or trust the order executor's requested amount as
   actual output; `lp_order_request` derives it from the finalized Solana
   transaction's exact bound-wallet and token-mint deltas.
6. After a confirmed preparation transaction or confirmed no-swap wallet
   allocation, use its returned `deployment_input` unchanged and let
   `lp_executor_request` refresh price, align the final range, validate amounts and
   schema, and decide technical admission. Existing attributed base and exact
   finalized swap output may be combined; requested order amount or broad
   wallet deltas may not substitute for either source.
   Submit only its unchanged `lp_executor` request, then reconcile the returned
   identity using only controller, operation ID, and `lp_executor_id`.
   Admission freezes the exact final request; reconciliation restores that
   request from its current-session receipt and must not refresh or replan.
   When executor capacity defers create to the following tick, run snapshot
   `continue_create.config` unchanged; it identifies the same preparation and
   lets the routine load the frozen deployment input without repeating it in the
   model response.
   Interpret a changed valid range before admission; do not bypass a
   deterministic rejection.
7. If a plan is no longer strategically suitable before create, leave the exact
   acquired swap output quarantined for its declared restoration path.
   Pre-existing wallet base requires no restoration. If any mutation is
   uncertain, stop and reconcile rather than selecting another use for it.

Return the strategic judgment and evidence used. Reports and routine results
remain diagnostic and execution authority respectively.

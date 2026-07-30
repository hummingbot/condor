---
name: lp_range_and_inventory
description: Choose an LP range from market evidence, validate it through the active venue planner, and prepare only the exact attributable inventory for one bounded executor allocation.
when_to_use: Read only when planner evidence is missing, contradictory, technically unfamiliar, or requires recovery/attribution analysis; the ordinary Strategy-declared plan and preparation path needs no skill read.
source: agent:lp_expert
---

# LP Range and Inventory

1. Refresh exact pool price, token identities, decimals, fee structure, and the
   active venue's range granularity and feasibility evidence.
2. Choose a strategic range from volatility, liquidity distribution, expected
   holding horizon, fee opportunity, risk posture, and portfolio role. Do not
   replace judgment with a rigid profile table.
3. Pass the active slot allocation and configured range/slippage bounds to the
   active venue's deterministic planner. Require valid aligned bounds,
   double-sided amounts, configured rebalance-threshold limit prices, and a
   stable plan digest. Treat the allocation as the maximum committed capital:
   use the planner's exact preparation-spend cap, which reserves headroom from
   configured slippage instead of consuming the entire allocation at the
   indicative price.
4. Resolve the exact account/network/default-wallet scope and confirm capital and
   native-token reserve. Ignore unrelated existing base inventory.
5. Prepare only the planner's attributable base shortfall through the active
   Strategy's swap provider. Pass its `max_usdc_for_preparation_swap` as the BUY
   ceiling. Enforce the Strategy's allocation cap; shared guidance never
   hardcodes an amount.
6. Preserve the exact swap receipt, refresh price and balances, and replan before
   create. The refreshed plan and receipt must agree with the guarded create.
7. Preserve the planner, Gateway, and create-guard `report_id` or `report_error`
   values so the observed range, inventory, quote, receipt, and admission
   outcome remain inspectable.
8. If create was definitely not submitted and the plan becomes invalid, restore
   only the acquired base. If any transition is uncertain, stop and reconcile.

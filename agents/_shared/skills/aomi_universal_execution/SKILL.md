---
name: aomi_universal_execution
description: Discover, preview and confirm Jupiter Lend, Kamino Earn and PumpSwap actions through Aomi Executor in attended chat.
---

# Aomi universal execution

Use this workflow directly in the attended conversation when the user asks about
Jupiter Lend, Kamino Earn or PumpSwap. These are supported Aomi recipe families;
new protocols do not become supported merely by supplying an address. Jupiter
Lend supplies lending liquidity, Kamino Earn deposits into a vault, and PumpSwap
adds/removes pool liquidity. Do not describe them all as swaps or trading profit.

1. Call `list_onchain_venues`, then `inspect_onchain_market` for the chosen venue
   and exact market. Show the connected wallet/network, asset, fees and relevant
   warnings in readable language. A mainnet-beta cluster label does not establish
   whether this is a local mirror: only the inspected local_mirror field does.
   If absent, report the environment as unverified; never infer live-network execution.
   Example markets are discovery aids, not investment
   recommendations. Disclose `local_mirror` when true. Never invent APYs or balances.
2. Obtain the user's amount and ceilings for token debits, SOL debit (including
   account funding) and network fee. PumpSwap deposits require both pool assets.
   Preserve those limits throughout this action; never raise them to make it pass.
3. Call `prepare_onchain_action`. Amounts are exact raw integer strings using the
   inspected decimals. Deposit amount is in market asset units (PumpSwap quote).
   For an exit use `withdraw_all=True` if requested. Preparation signs nothing.
4. Call `create_onchain_executor` with chain=svm, chain_id=1,
   cluster from preparation, mode=instructions, the returned prepared_action_id,
   commit=False, max_svm_network_fee_lamports and svm_spending_policy. Build the
   policy from the returned wallet, market.address, market.program_id, unique
   program IDs from result.allowed_programs and the user's max_debits_raw. Include native
   for SOL. Deposit limits include market.asset and any base_asset; withdrawal
   permits market.share_mint up to position.wallet_shares_raw (or shares_raw if
   wallet_shares_raw is absent). Never use fractional farm shares as raw tokens.
5. Poll `get_executor` until terminal. A failed simulation, incomplete fee/balance
   evidence or refused spending limit ends this preview: explain it and submit
   nothing. Otherwise show expected asset movements, network fee, account-funding
   costs and the action in a short review. A simulated result is not a receipt.
6. Ask the user to confirm this reviewed action. Following confirmation call the
   same `create_onchain_executor` with commit=True and reviewed_svm_plan_hash from
   the preview. Preserve instructions, account/controller, all ceilings and policy.
   The prepared-action reference preserves the exact bytes and assembly; never
   reconstruct or copy instruction arrays yourself. The normal chat permission
   gate also applies. If a plan expires or changes,
   reprepare and preview it and ask the user to review again. Do not bypass an
   approval through code, routines, delegation, direct signing or another tool.
7. Poll `get_executor` and report actual confirmed receipts, then
   `get_onchain_position`. A completed dry run or creation response is not execution.
   If submission is ambiguous, report that uncertainty and reconcile that executor;
   do not resubmit. Entry/exit amounts and fees may differ; do not call the difference
   profit or yield. Report exact receipt amounts and distinguish wallet holdings
   from contributions attributable to this action.

Stay in this chat for the review and confirmation. Unattended actions on these
venues are not granted by this workflow. Human approval and the executor's fresh
simulation/plan binding remain required. No production signing claim follows from
local test-wallet evidence.


## Keep the chat readable

For discovery, give one short bullet per venue and the common preview/confirm
workflow. For a preview, show the action, expected asset movements, total SOL debit
and fee component, and whether the user's limits pass. Keep the normal response
under about 150 words. Use short wallet/market identifiers unless the user asks for
full addresses; full plan hashes, program IDs and raw units belong in tool details.
Never label the full native balance decrease as rent: it already includes network
fees and account funding. Do not add simulation_fees to it again.

For a result, lead with confirmed, refused or unresolved. A commit attempt that
fails without a hash is unresolved until independently reconciled; committed=false
alone cannot prove no broadcast or unchanged balances. Read current position and
report the backend's actual confirmation status. Do not invent a root cause from a
generic error. Keep local-test labels clear, without repeating a long disclaimer
on every turn.

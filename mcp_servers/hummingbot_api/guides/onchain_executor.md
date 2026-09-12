# Aomi on-chain executors

Use the typed create_lending_executor for exact Aave V3 supply/withdraw plans and
create_onchain_executor for catalog operations, raw EVM calls or Solana instruction batches. Both return an
onchain_executor owned by the Hummingbot API. Read get_executor for its result,
list_executors for history and stop_executor to stop an action before submission.

## Lending workflow

1. Read the wallet, market and available liquidity through the Aomi read routines.
2. Call create_lending_executor with chain_id, wallet, pool, asset, amount (raw integer
   string), action (supply/withdraw), controller_id and commit=False to preview.
3. Inspect simulation, exact approval, asset movements and estimated gas.
4. For an automatic commit, use the operator-granted account/controller and identical
   plan, commit=True, require_lending_policy=True and an explicit max_gas_quote.
   Condor checks durable contributions and real USDC/USDT valuation. The API checks
   exact authority and reserves capacity under a database transaction lock.
5. Inspect actual receipt evidence and durable contributions after confirmation.

A policy is configured by the operator with AOMI_LENDING_POLICY_FILE on the API.
It bounds recorded contributions and pending supplies, not externally deposited funds,
interest or the whole portfolio's market value. Only confirmed withdrawals free
capacity. Wallet-wide receipt-token balances are shown separately from controller
contributions. Do not clear unknown history to free a limit.

## Catalog and raw calls

create_onchain_executor requires an explicit commit decision. Its mode is calls,
instructions or operation. Unattended use permits only commit=False; granted lending has its own tool.

Raw calls have to, value (decimal wei string), data (signature, args, raw), optional
chain_id and description. A plain transfer has empty signature/args/raw. Catalog mode
uses operation, arguments and optionally app/skills as discovered by aomi_catalog.
Use aomi_skill to read protocol instructions and aomi_read to inspect chain state.
For Solana reads, set aomi_read chain=svm. Start with context to verify the connected
cluster and wallet. Use program with an address or name query to inspect available
program metadata and interfaces; account reads accept any Solana address. The
token-holdings operation accepts an optional mint filter, or include_token_2022=true
to include Token-2022 positions such as PumpSwap LP tokens. These reads use the same
Aomi client as EVM reads; chain_id does not select a Solana cluster.
The catalog excludes chat plumbing and direct stage/commit primitives.

For Solana, use chain=svm, chain_id=1 and mode=instructions. The instructions
argument is a list of svm_stage_ix batches. Each batch has a description and a
non-empty instructions list; each instruction names program_id and accounts with
pubkey/is_signer/is_writable, plus either data_base64 or an IDL encode object.
Batch options include version and address_lookup_tables. Preserve the instruction
order and exact account permissions returned by the protocol builder. Signing
authority and the actual network come from the Aomi wallet/provider configuration;
the executor's cluster field labels the record and does not switch the backend RPC.
Use protocol discovery and position reads to identify the selected market and check
the result. This shared transport alone does not establish support for a new venue.

The backend stages/builds, fork-simulates, checks risk, commits and records evidence.
max_gas_quote is an estimated execution-gas ceiling in USDT, independent of display
pair. Missing pricing refuses a budgeted commit. Rollup data fees and provider
surcharges are excluded; it is not a final signing fee cap. timeout_sec bounds the run.

Creation is persisted before on-chain execution starts. Unknown outcomes remain
reserved for reconciliation. Multiple wallet transactions are not atomic. A dry-run
COMPLETED status does not prove signing. Local fork/test-wallet evidence does not
certify production Para signing, service availability or realized investment returns.

For Solana, `max_svm_network_fee_lamports` sets a non-negative integer ceiling
on the complete simulated network fee (one SOL is 1,000,000,000 lamports). The
executor requires the backend's `svm_network_fees` completeness guard and checks
the payer, cluster, native asset and raw fee units before summing all steps.
An older backend without this evidence refuses a budgeted submission. This ceiling
excludes account rent, protocol fees and signing-provider charges, and does not
guarantee the final network fee. Read `estimated_svm_network_fee_lamports` in
custom_info; an unavailable estimate is never represented as zero.

To bind an attended confirmation to the reviewed Solana instructions, first create
a dry run, then retain its `svm_plan_hash`. Pass that value as
`reviewed_svm_plan_hash` with the same prepared instructions and `commit=True`.
The executor refuses changed wallet, cluster, program, ordered accounts and
permissions, instruction bytes or assembly options after fresh staging/simulation.
Queue IDs, expiry and display descriptions may change. This is per-invocation
review binding, not an unattended grant or a venue/spending policy. Asset spending
limits and the operator selection workflow still need their separate checks.


## Attended Solana chat

`list_onchain_venues`, `inspect_onchain_market`, `get_onchain_position` and
`prepare_onchain_action` expose the same registered Aomi preparation service as
Condor's dashboard. They do not stage, sign or submit. Supported recipes currently
cover Jupiter Lend, Kamino Earn and PumpSwap; inspect a market before preparing it.
Pass the returned `prepared_action_id` to `create_onchain_executor` in svm
instructions mode. The reference preserves exact instruction bytes and assembly;
do not reconstruct them. Expired references require a new preparation and review. Include `svm_spending_policy` with the reviewed wallet, market,
protocol program, returned allowed top-level programs and raw debit ceilings (including
`native` for SOL fees/account funding). The API independently verifies the policy.
`get_executor` returns allowlisted simulation, policy and receipt identity evidence.
Confirm in the attended chat using the preview's `reviewed_svm_plan_hash` and the
same instructions and ceilings. For the full procedure read the shared
`aomi_universal_execution` skill when the Condor skills library is available.

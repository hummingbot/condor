# Aomi on-chain executors

Use the typed create_lending_executor for exact Aave V3 supply/withdraw plans and
create_onchain_executor for catalog operations or raw EVM calls. Both return an
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

create_onchain_executor requires an explicit commit decision. Its mode is calls or
operation. Unattended use permits only commit=False; granted lending has its own tool.

Raw calls have to, value (decimal wei string), data (signature, args, raw), optional
chain_id and description. A plain transfer has empty signature/args/raw. Catalog mode
uses operation, arguments and optionally app/skills as discovered by aomi_catalog.
Use aomi_skill to read protocol instructions and aomi_read to inspect chain state.
The catalog excludes chat plumbing and direct stage/commit primitives.

The backend stages/builds, fork-simulates, checks risk, commits and records evidence.
max_gas_quote is an estimated execution-gas ceiling in USDT, independent of display
pair. Missing pricing refuses a budgeted commit. Rollup data fees and provider
surcharges are excluded; it is not a final signing fee cap. timeout_sec bounds the run.

Creation is persisted before on-chain execution starts. Unknown outcomes remain
reserved for reconciliation. Multiple wallet transactions are not atomic. A dry-run
COMPLETED status does not prove signing. Local fork/test-wallet evidence does not
certify production Para signing, service availability or realized investment returns.

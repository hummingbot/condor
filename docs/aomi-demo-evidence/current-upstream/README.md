# Current upstream integration evidence — 11 September 2026

The current Condor UI and typed MCP tool completed real Aave V3 supply and withdrawal transactions on a local Base fork. These are test funds and local receipts, not public-chain transactions or a profitability demonstration.

## Versions and boundaries

- Stock Condor baseline: `1890c0871694c8b1c7c93ecdadb6350cd20eab6a`.
- Condor execution code: `312567b725cec720ad0dffe4ed2d7c019d421ca2`.
- Hummingbot API execution code: `f5bc4b191b019b9e7834d227cae086c3c4c4f969`, including upstream `7e86651b69b13b39e0cb3e1afbf080eedd461e8d`.
- Public aomi-python v0.1.2: `b108c2d5399ac1d3a31a98f4b6668b3b20233d8e`.
- Aomi backend code: `e1e89ad0fb397cf353f6ece46e5ec10e261c5af1`; subsequent CI-only fixes at `04b600b6ad4ca427d8af1502d391a0a85c780ea6`, all CI jobs green.
- Base chain 8453 forked at block 51,161,414. Aave pool and USDC are deployed contracts; initial wallet funding is synthetic.
- Condor → authenticated Hummingbot API → actual Aomi Pipeline. Only the signing handoff is replaced by a bounded loopback Anvil test-wallet adapter. No production Para signing is claimed.
- Gas conversion uses a fresh public Kraken ETH/USDT demo adapter. Grant valuation uses the real Hummingbot Binance USDC/USDT endpoint. Execution-gas estimates exclude Base data fees and provider surcharges.

The final follow-up separates estimated gas from incurred executor fees and corrects history navigation to upstream's `groupBy=ctrlType`. The recorded supply/withdraw executions predate this accounting-only correction; their receipt and balance evidence remains tied to the versions above. Receipt-derived quote fees are unavailable and are not invented from simulation estimates.

## Verified results

All amounts below are USDC; aUSDC is the wallet-wide receipt-token balance, including earlier test activity.

| Path | Action | Wallet before → after | aUSDC after | Receipts |
| --- | --- | --- | --- | --- |
| Typed granted tool | Supply 5 | 999.999997 → 994.999997 | 5.000012 | 2 |
| Typed granted tool | Withdraw 4.999999 | 994.999997 → 999.999996 | 0.000012 | 1 |
| Recorded UI | Supply 2 | 999.999996 → 997.999996 | 2.000011 | 2 |
| Recorded UI | Withdraw 1.999999 | 997.999996 → 999.999995 | 0.000012 | 1 |

Every receipt in [receipts.json](receipts.json) was independently fetched again from the live fork: status `0x1`, identical block hash. Remaining USDC allowance to the pool is zero after each action. The two small round trips leave rounding residuals; they are not realized trading profits.

The typed check calls the real Condor risk callback and `create_lending_executor` with explicit policy-required authorization, then the authenticated API and database admission. It uses deterministic inputs, with no model inference. A 10.000001 USDC action is refused under a 10 USDC action cap. PostgreSQL race tests separately prove serialized admission across service instances and fresh reads under both READ COMMITTED and REPEATABLE READ defaults.

The recorded negative case previews 1 USDC with a 0.000001 USDT gas budget. Simulation passes, the executor reports `gas_over_budget`, and confirmation is disabled. No additional wallet submission occurs. The isolated signer has eight confirmed historical action files and twelve receipts after this run, unchanged by the refusal.

Final contribution ledger: `grant-demo` 0.000002 USDC; `main` 0.000003 USDC; shared wallet receipt balance 0.000012. These separate concepts deliberately do not sum: wallet balances can include other activity, interest and rounding. Pending supplies reserve contribution capacity; unknown outcomes remain reserved, and withdrawals release it only after confirmation.

## Recording and reproduction

The local 148-second walkthrough combines current stock Condor's routine catalog/lending search, supplied 2 USDC, withdrew 1.999999 USDC, gas refusal and ledger. Idle review time is removed; execution states are not fabricated. The baseline search shows no built-in lending routine, not an inability to build a custom integration. Videos are local deliverables, not hosted in this repository. [Narration script](narration.md).

Use the repository's [operator guide](../../../mcp_servers/hummingbot_api/guides/onchain_executor.md) and API README for installation, exact market, signer ownership and policy format. Configure isolated databases, local backend/Pipeline endpoints, a funded fork wallet, operator grants and a test signing adapter before reproducing. Never point this harness at production. Public evidence intentionally excludes credentials and private Pipeline request metadata.

## Validation

- API full suite before final accounting correction: 927 passed, eight integration cases deselected. Focused lifecycle/duplicate/admission checks and real PostgreSQL concurrency tests pass.
- Condor full Python suite: 5,231 passed, three live skips. Frontend: 1,852 passed; build and upstream lint gate passed on Node 22.22.2 before final display/navigation correction.
- Public client: 139 passed, one live skip; wheel and source distribution built.
- Para transaction-envelope regressions: eight passed, including empty calldata and missing/zero fee rejection before HTTP. Live provider signing remains a separate deployment check.
- Final API suite: 928 passed, eight integration cases deselected. All 43 lifecycle/gas regressions pass. Final frontend suite: 1,853 passed; production build and lint gate pass. A fresh UI preview on the accounting correction was stopped with a positive estimate, zero incurred fees and no commit attempt; see [final-refusal.json](final-refusal.json). History navigation was checked against the current fleet browser.

## Automated review follow-up

Two final review findings were corrected after the recording: an empty recent-executor search no longer bypasses the durable lending ledger, and the lending form offers **Start another action** after a confirmed result (or a terminal result that never attempted commit). An uncertain accepted request remains locked to prevent duplicate submission. New provider/risk and browser-component regressions cover these boundaries. The video remains accurate for the recorded executions; it does not demonstrate the later reset control.

Final review validation: 5,233 Python tests passed (three live skips), 1,855 frontend tests passed, production build and upstream lint gate passed. Backend CI run 34591678160 passed at 2439db38 after its current-upstream merge.

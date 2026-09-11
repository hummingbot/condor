# Condor lending UI: local fork evidence

Recorded 11 September 2026 against Base fork block 51,161,414. These receipts belong to a local Anvil fork, not a public explorer. No mainnet funds were used.

## What ran

Condor UI → Hummingbot API on-chain executor → Aomi Pipeline stage, simulate and commit → local test-wallet adapter → real deployed Aave V3 contracts on the fork. The adapter verifies exact calldata and caps each action at 100 USDC. It submits actual transactions, verifies successful receipts and reads actual balances. It substitutes for the production signer; this run does not verify Para signing. A separate fresh public ETH/USDT quote supplies gas conversion in this isolated demo.

The baseline recording uses upstream Condor `0e310e62773081b6ffe25cb137f5931369e090e2`. It shows the existing routine catalog, market-making tools, and no matching built-in lending routine. It does not claim existing swaps are missing or custom integrations are impossible. Both UIs use the same isolated API for server status; the baseline is a UI comparison, not an executed trading benchmark.

The after recording uses Condor `0ec2a9842020159ba0d51e31c7d60b74c05b3dba`, Hummingbot API `ba45860c76f8f45fe05ed920536707f3c5d59fb7`, client `b108c2d5399ac1d3a31a98f4b6668b3b20233d8e` (v0.1.2), and backend `e1e89ad0fb397cf353f6ece46e5ec10e261c5af1`.

## Observed outcomes

| Action | Wallet USDC before | Wallet USDC after | Receipt-token balance after | Remaining allowance |
| --- | ---: | ---: | ---: | ---: |
| Supply 100 USDC | 1,000 | 900 | 99.999999 | 0 |
| Withdraw 99.999998 USDC | 900 | 999.999998 | 0.000011 | 0 |

Amounts have six decimals. The 0.000002 USDC net contribution differs from the 0.000011 wallet-wide receipt balance because of rounding and elapsed fork time. Neither is claimed as realized trading profit or a fully closed position.

- [Supply receipts and balances](supply.json): approval and supply both succeeded.
- [Withdrawal receipt and balances](withdraw.json): withdrawal succeeded.
- Negative case: a 1 USDC supply preview with a 0.000001 USDT estimated-gas budget simulated successfully but stopped at the risk check. Confirmation was disabled; there were still exactly two wallet execution records and three successful receipts in total. No third action was submitted.

All three receipts were independently re-read from the fork and matched their recorded block hashes and successful statuses. The video is edited to remove idle review time. Execution-gas estimates exclude Base data fees and provider surcharges.

## Boundaries still requiring evidence

Production signing and service deployment, automatic agent exposure admission across restarts/concurrent creates, and full upstream compatibility are separate readiness gates. This demonstration proves the explicitly confirmed operator workflow with a local test signer. It does not prove future yield or unattended execution readiness.

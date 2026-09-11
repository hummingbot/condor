# Automatic lending grant: local fork evidence

Verified 11 September 2026 against API `471d7e04`, Condor `e9ac42f6`, aomi-python `v0.1.2`, and backend code `e1e89ad0` (workflow head `04b600b6`).

A named `grant-demo` controller used the real Condor risk callback and `manage_executors` MCP implementation, the authenticated Hummingbot API, its PostgreSQL-serialized policy admission, and the Aomi Pipeline. No model inference was involved in this deterministic integration check.

The operator file granted 20 USDC to this controller, capped each action at 10 USDC, and capped gas at 1 USDT. A 10.000001 USDC supply was refused before submission. A 10 USDC supply and 9.999999 USDC withdrawal were approved and completed. The live Binance USDC/USDT quote came through Hummingbot's actual market-data endpoint; local macOS proxy configuration was explicitly passed to its network session.

Supply executor: `GQkKJTZw6bJvtgLYkM4cwfAwHHfXLPVEGjsMBijntT6b`.
Withdrawal executor: `Dz1t5boyPPXnVrfqDf8Wi7BSHyqUvVDqpFtt1TofTW1F`.

All three transaction receipts were independently re-read from the live Base fork and matched their recorded block hashes and successful status. The wallet balance changed from 999.999998 to 989.999998 and then 999.999997 USDC. The final wallet-wide aUSDC balance was 0.000012; this controller's net contribution is 0.000001 USDC. Residual balance includes the earlier manual demo and is not attributed to this controller. Final allowance was zero.

The signer is an explicitly bounded loopback Anvil test wallet, not Para. Gas valuation uses the previously documented fresh public Kraken ETH/USDT demo adapter. These receipts prove the local fork path, not production signing or realized investment profit. This automatic check was not recorded as a video; the separately delivered UI videos show the manual supply/withdrawal/refusal flow.

# Narration — Hummingbot × Aomi (2:28)

## 0:00–0:17 — Existing Condor
Condor already helps operators run trading agents and market-making routines. This is current upstream. Searching for lending finds no built-in routine. The opportunity is to bring another capital operation into the same workflow.

## 0:17–1:02 — Supply
With Aomi, an operator can supply a bounded reserve to Aave on Base. Here we use just two test USDC. The preview shows the exact asset movement, approval authority and estimated execution gas. The operator reviews and confirms. Hummingbot owns the executor lifecycle; Aomi constructs and simulates the protocol calls. These are real transactions on a local fork, signed by an explicitly substituted test wallet. The receipts were independently checked against the fork.

## 1:02–1:48 — Recover liquidity
Now withdraw 1.999999 USDC to the same wallet. Review the new preview and confirm. The wallet balance increases and the transaction receipt is retained. A small residual remains from rounding and earlier tests. This demonstrates capital recovery, not trading profit or future yield.

## 1:48–2:18 — Enforce the budget
A successful simulation does not authorize every action. Here the estimated execution gas exceeds the entered budget, so confirmation is disabled and no transaction is submitted. Automatic lending also requires an operator-owned grant, with exact market and controller checks and durable capacity reservations. We separately tested an over-limit refusal and competing database admissions.

## 2:18–2:28 — Account for the position
Completed deposits remain in the ledger. Controller contributions and the shared wallet balance are distinct. The opportunity is more productive reserves with fewer manual steps, when expected income exceeds costs.

## Recording notes

No audio is embedded; this is the supplied voiceover script. Footage is actual UI capture with idle time removed. Local Base fork and test signer labels remain visible. Supply/withdraw footage uses API f5bc4b19 and Condor 312567b7. A subsequent correction separates simulated gas estimates from incurred executor fees without changing transaction execution. These tiny test amounts are not an economically profitable allocation example. Gas estimates exclude Base data fees and signing-provider surcharges. Production Para signing is not demonstrated.

---
name: gateway_dex_operations
description: Operate Hummingbot Gateway across chains and DEXs with explicit account, network, wallet, provider, token, quote, execution, and transaction-status semantics.
when_to_use: Read only for an unfamiliar or ambiguous Gateway result, status/recovery problem, or unclear chain, network, wallet, provider, token, side, or amount semantics; ordinary Strategy-declared swaps need no skill read.
source: agent:lp_expert
---

# Gateway DEX Operations

1. Treat Gateway as the chain/DEX transaction layer, not the owner of Condor
   controller or executor state.
2. Resolve the active account, canonical network, default wallet, connector or
   provider, token identities, balances, and native-token reserve before a
   mutation. Never substitute an account name for a wallet.
3. Quote before execute. Preserve connector, network, wallet, pair, side, amount,
   slippage/cap, reserve, and operation identity across quote, execute, recovery,
   and status.
4. Confirm what `amount` denotes for the chosen side and endpoint. Do not assume
   BUY and SELL use the same input/output denomination.
5. Submit once. In loop mode the shared routine writes a current-session
   operation receipt before submission and records its outcome. After an
   interrupted tick, recover that exact operation: `absent` or `rejected` proves
   no Gateway submission; `submitting`, `uncertain`, or `manual_review` blocks a
   retry; a recorded hash is reconciled through status. When no hash survived,
   `recover` searches only the bounded current-operation Gateway window using the
   original connector, wallet, pair, side, amount, slippage, and attribution
   bounds. Continue only from one exact match. If that native history remains
   uncertain after a timeout or unknown result, use the Strategy-declared
   finalized Solana reconciliation fallback once with exact signer, time window,
   programs, accounts, and bounded wallet asset changes. Chain absence or
   ambiguity never authorizes a retry.
6. A uniquely recovered confirmed preparation BUY is attributable inventory, not
   an unrelated wallet balance. Continue the intended same-session LP chain from
   its exact input/output receipt, or use a new operation ID to sell only that
   exact output back to the Strategy quote asset when the candidate is no longer
   valid.
7. Prefer the LP executor for normal position open/close lifecycle. Use a direct
   swap or CLMM fallback only when the active Strategy explicitly authorizes it.
8. Preserve every `gateway_swap` `report_id` or `report_error` with the operation
   trace. A diagnostic report failure does not change the quote, submission,
   recovery, or reconciliation outcome.
9. An active Strategy may authorize one narrow token-registry mutation: ensure
   the selected token's exact address, symbol, and decimals, then re-read and
   verify it. Treat an exact existing entry as idempotent success; reject
   metadata collisions; stop on uncertainty without a blind retry. Never
   bulk-register scan candidates.
10. Never modify wallets, RPC endpoints, providers, connectors, or any other
    Gateway configuration during a trading tick. Do not restart Gateway as part
    of token registration.

Read `gateway-contract.md` when translating identifiers, interpreting swap
amounts or receipts, or distinguishing router and CLMM operations.

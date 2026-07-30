# Gateway Contract Reference

## Identity model

Keep these identifiers distinct:

| Concept | Example | Scope |
|---|---|---|
| Chain | `solana` | Blockchain family |
| Gateway network | `mainnet-beta` | Chain-local network |
| Hummingbot connector network | `solana-mainnet-beta` | Hummingbot/Gateway namespace |
| LP provider | `orca/clmm` | DEX plus trading type |
| Swap provider | `jupiter/router` | Router used for swaps |
| MCP connector shorthand | `jupiter` | Surface-specific connector input |
| Account | configured `account_name` | Hummingbot API account namespace |
| Wallet | returned Solana address | Gateway chain identity |

Do not derive one mechanically unless the current tool/routine contract performs
and verifies the translation. Use returned canonical values.

Gateway resolves a default wallet at chain scope and may resolve a default swap
provider from network configuration. Verify both; do not mutate either.

## Token registry

Gateway may not know every token returned by venue discovery. When the active
Strategy explicitly authorizes selected-token registration:

1. Use only the current complete scan's exact token address, symbol, and
   decimals. Do not infer metadata from a trading-pair string.
2. Read the network token registry first. An exact matching entry is ready
   without mutation.
3. Reject duplicate addresses, an address metadata mismatch, or the same symbol
   assigned to a different address. Never overwrite or bulk-register.
4. Add only a missing selected token, then re-read the registry and require the
   exact entry before quoting or executing.
5. A timeout, cancellation, transport failure, or failed verification after the
   add call is uncertain. Do not blindly add again; a later tick must first
   re-read the exact token entry.

Current Gateway releases read token configuration without requiring an automatic
restart after programmatic registration. If a verified entry is still unusable,
reject or hold that candidate and report the evidence; do not restart Gateway
from the trading agent.

## Swap contract

A robust first-attempt swap sequence is:

```text
scope -> quote -> validate -> execute once -> status when pending -> balances
```

The quote is evidence, not a price lock. Before execute, enforce the active
Strategy's maximum spend, slippage, wallet, network, token pair, and native-token
reserve.

For LP Expert's guarded swap routine:

- `BUY` means acquire the base token and `amount` is the requested base output;
  enforce the maximum quote input separately.
- `SELL` means dispose of an exact base amount and `amount` is base input.
- A confirmed `BUY` may receive less than its requested base output only down to
  `amount * (1 - slippage_pct / 100)` and must remain below its explicit maximum
  quote input. Replan from the exact confirmed amounts.
- Preserve a returned transaction hash before parsing optional amount fields. If
  execute omits amounts, reconcile the hash through status before continuing.
- Preserve the exact confirmed input/output amounts. Those values, not a broad
  balance delta, establish inventory attribution.

Other Gateway surfaces may define amount differently; inspect their current
schema before use.

## Router versus CLMM

- A router provider, such as `jupiter/router`, quotes and executes token swaps.
- A CLMM provider, such as `orca/clmm`, manages concentrated-liquidity position
  operations and venue-specific range constraints.
- The LP executor coordinates the normal CLMM lifecycle and may call the swap
  provider during close when `keep_position=false`.
- A separate fallback swap is appropriate only after exact executor evidence
  proves the native close-out failed and identifies the residual.

## Transaction outcomes

- Confirmed transaction plus matching receipt: continue.
- Deterministic validation rejection before submission: failed without a
  transaction.
- Hash returned but finality unknown: query status using the same identity.
- Timeout, cancellation, or transport failure after possible submission and no
  hash: uncertain; do not resubmit. In loop mode, recover may search the narrow
  current-operation Gateway history window. Zero matches remains uncertain, one
  exact match is reconciled through status, and multiple matches require manual
  review.
- Interrupted loop with no visible result: recover the exact current-session
  operation receipt first. `absent` or `rejected` means the guarded routine did
  not submit; `submitting` or `uncertain` requires manual review.
- Contradictory status, receipt, or wallet evidence: HOLD/manual review.

Never print or persist private keys, API keys, auth headers, RPC credentials, or
full sensitive configuration.

## Upstream references

- [Hummingbot Gateway HTTP client](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/core/gateway/gateway_http_client.py)
- [Hummingbot LP executor implementation](https://github.com/hummingbot/hummingbot/tree/master/hummingbot/strategy_v2/executors/lp_executor)
- [Gateway Orca CLMM connector](https://github.com/hummingbot/gateway/tree/main/src/connectors/orca)
- [Gateway Jupiter router](https://github.com/hummingbot/gateway/tree/main/src/connectors/jupiter)
- [Gateway Solana transaction status route](https://github.com/hummingbot/gateway/blob/main/src/chains/solana/routes/status.ts)
- [Hummingbot 2.12.0 Gateway token-cache removal](https://hummingbot.org/release-notes/2.12.0/)

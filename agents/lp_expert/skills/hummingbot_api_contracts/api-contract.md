# Hummingbot API Contract Reference

The installed Hummingbot API and Hummingbot client source are the runtime
authority. This reference records the stable concepts an LP strategy needs.

## Identity boundaries

- **Account**: Hummingbot API credential/account namespace, such as the active
  Strategy's `account_name`.
- **Controller**: Condor/Hummingbot owner of a group of executors. LP Expert must
  use the exact current `lp_expert.<strategy>_<session>` identity.
- **Executor**: one strategy-v2 execution object with its own ID, config,
  lifecycle, PnL, and logs.
- **LP position**: the venue position managed by an LP executor. Its on-chain or
  provider identity is not interchangeable with the executor ID.
- **Wallet**: chain address resolved by Gateway for the active chain/account
  scope. An account name is not a wallet address.

Require explicit current evidence when joining these objects.

## LP executor config

Retrieve the live schema before every live create path. The current Hummingbot
LP executor contract commonly includes:

- `type`
- `connector_name`
- `lp_provider`
- `pool_address`
- `trading_pair`
- `base_amount` and `quote_amount`
- `lower_price`, `upper_price`
- `lower_limit_price`, `upper_limit_price`
- `swap_provider`
- `keep_position`

Condor creation also needs the exact controller identity. Field placement and
accepted shapes are defined by the current MCP/API schema, so do not reconstruct
them from this list.

The current LP executor contract does not include native `time_limit`,
`stop_loss`, `take_profit`, or `triple_barrier_config` fields. LP Expert keeps
those thresholds in its frozen Strategy config, checks each exact executor's
creation timestamp and `net_pnl_pct`, and uses the normal exact stop lifecycle
when a limit triggers. Never inject those Strategy fields into the executor
payload unless a future live schema explicitly adds them.

## Lifecycle

Current Hummingbot LP execution distinguishes states such as:

```text
NOT_ACTIVE -> OPENING -> IN_RANGE or OUT_OF_RANGE
-> CLOSING -> SWAPPING -> COMPLETE
```

`FAILED` may occur at multiple stages. State labels alone do not prove whether a
position exists or whether residual inventory remains; inspect position and
receipt evidence.

With `keep_position=false`, the executor normally closes liquidity and attempts
its configured close-out swap before completion. With `keep_position=true`, it
may preserve resulting inventory/position-hold semantics instead. LP Expert's
Orca Strategy requires `keep_position=false`.

## Read and mutation semantics

- Use schema/guide discovery inside the Strategy's create guard before create.
- Use exact executor search, performance report, and logs for active
  supervision and reconciliation.
- `/executors/positions/summary` reports positions held outside active
  executors. Use it only for post-close residual inventory; it cannot prove that
  a running LP's embedded on-chain position exists or is absent.
- A create response is not enough: reconcile the returned executor under the
  exact current controller and expected config identity.
- A stop request is not terminal evidence: reconcile the executor and LP
  position until close semantics are known.
- Stop one executor once. A timeout or lost response after possible submission
  is uncertainty, not permission to stop again.
- Treat empty, truncated, stale, or contradictory reads from the same
  authoritative object as incomplete evidence. A running LP executor and an
  empty held-position summary are different objects and are not contradictory.

## Portfolio reads

Portfolio output is account- and network-scoped balance/position evidence. It
can prove availability and reserve but cannot attribute broad wallet inventory
to one executor. Use exact preparation-swap and close receipts for attribution.

## Upstream references

- [Hummingbot LP executor implementation](https://github.com/hummingbot/hummingbot/tree/master/hummingbot/strategy_v2/executors/lp_executor)
- [Hummingbot API executor router](https://github.com/hummingbot/hummingbot-api/blob/main/routers/executors.py)
- [Hummingbot API portfolio router](https://github.com/hummingbot/hummingbot-api/blob/main/routers/portfolio.py)
- [Condor Hummingbot MCP executor tool](https://github.com/hummingbot/condor/blob/main/mcp_servers/hummingbot_api/tools/executors.py)

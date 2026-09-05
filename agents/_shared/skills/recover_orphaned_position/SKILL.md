---
name: recover_orphaned_position
description: The cross-server procedure for a live on-chain LP position whose executor has
  terminated — how to recognise one, why stopping the executor cannot help, closing it by
  address through Gateway, and the bookkeeping that ends it.
when_to_use: An LP executor terminated with a position still on-chain, a close exhausted its
  retries, an orphan warning appeared, or before opening a new LP position on funds a
  previous one may still hold. Also whenever an LP position and its executor disagree.
source: builtin
---

# Recover an orphaned position

An **orphan** is a live on-chain CLMM position with no automated owner: the executor
that opened it has terminated, so nothing is monitoring the range, nothing will close it
at a limit price, and nothing will collect its fees. The capital is real and it is
unattended.

No single tool docstring can own this procedure, because it crosses two servers: the
Hummingbot API knows *which* executors are orphaned, Gateway is the only thing that can
*close* the position, and the API has to be told afterwards. That sequence is what this
playbook is.

**Do not open a new LP position on the same funds until the orphan is resolved.** A
fresh `create_lp_executor` cannot adopt an existing position — it always mints a new
one, stacking a second funded position on top of the first.

## How a position becomes an orphan

Three ways, and they need slightly different handling:

- **A close that exhausted its retries.** The executor terminates as an involuntary
  `POSITION_HOLD` carrying a `hold_reason`, and its record is flagged
  `orphaned_position`. The position address is known. This is the common case.
- **A legacy `FAILED` executor** whose final state still carries a `position_address`.
  Also directly recoverable.
- **An API restart reaping the executor** (`SYSTEM_CLEANUP`). The position address is
  **not** recorded, so it has to be found on-chain before anything can close it.

## 1. Find the orphans

```
list_orphaned_positions()
```

Read-only, and worth running proactively — before opening a new LP position, and after
any unexpected LP termination.

Each entry gives you what the close needs. Note the field names, because two of them are
easy to mix up:

- `position_address` — the position itself. This is what actually gets closed.
- `lp_provider` — the **DEX**, e.g. `orca/clmm`.
- `connector_name` — the **NETWORK**, e.g. `solana-mainnet-beta`. Despite the name, this
  is not the DEX.
- `pool_address` — the pool the position sits in.

The close call inverts the first two relative to how they are named here, which is the
single most common way this procedure goes wrong. Map them deliberately:

```
lp_provider     →  connector
connector_name  →  network
```

## 2. If the position address is unknown, reconcile on-chain first

An entry flagged as needing reconciliation (the `SYSTEM_CLEANUP` case) has no
`position_address`, and you cannot close what you cannot name. Find it:

```
get_portfolio_overview(include_lp_positions=True)
```

Match the on-chain positions against the executor's pair and pool. A position present
on-chain with no corresponding live executor is the orphan. Do not guess between two
candidates — if it is ambiguous, say so and stop rather than closing a position that
belongs to a running strategy.

## 3. Do not try to stop the executor

`stop_executor` on a terminated executor is **a no-op, not an error**. It returns
`status="already_terminated"` with the final `close_type`, `position_address` and the
`orphaned_position` flag — it does not return a 404, and it does not close anything.
That response is useful for confirming what you are dealing with, and useless as a
recovery step.

A 404 from `stop_executor` means something different: the executor id is unknown to the
API's database — it never existed, or the database was unavailable at that moment.

The reason stopping cannot work is structural: the executor is already gone, so there is
nothing running to receive the instruction. The position has to be closed by address,
directly against Gateway.

## 4. Close the position through Gateway

```
manage_clmm(action="close",
            connector=<lp_provider>,       # the DEX, e.g. "orca/clmm"
            network=<connector_name>,      # the network, e.g. "solana-mainnet-beta"
            position_address=<position_address>,
            pool_address=<pool_address>)
```

Points that matter:

- **Pass `pool_address`.** A position opened by an LP executor is opened by the bot
  straight against Gateway, so the API database has no row to read the pool from and the
  close can fail with a 400 without it. `list_orphaned_positions` reports the pool for
  exactly this reason. It is harmless where the API can resolve the pool itself, so
  there is no case where omitting it is the better choice.
- **Use `close`, not `remove_liquidity`.** `remove_liquidity` at 100% withdraws the
  liquidity but leaves an **empty position still open**. Only `close` withdraws
  everything, collects the pending fees and closes the position account. An orphan
  "recovered" with `remove_liquidity` is still an orphan, now with nothing in it.
- The `'<dex>/clmm'` form of `lp_provider` passes through unchanged, so the orphan
  record's value can be used as-is.

Read the result and confirm the transaction hash and the amounts returned. If the close
fails, do not proceed to step 5 — the position is still live and the bookkeeping must
keep reflecting that.

## 5. Mark it recovered

```
resolve_orphaned_position(executor_id="<executor_id>")
```

**Only after the position is actually closed on-chain.** This closes nothing itself; it
updates the API database so the orphan stops appearing in listings and warnings.
Calling it on a position that is still open hides a live, unattended position from every
future check — the one genuinely harmful mistake available in this procedure.

## 6. Restart the controller if one was managing it

If an `lp_rebalancer` controller owned the executor, restart the controller or its bot
after resolving. Its orphan halt is held **in memory** and only clears on restart;
`resolve_orphaned_position` updates the database, not the running controller. Skip this
and the controller stays halted while every check says the orphan is resolved.

```
manage_bots(...)   # restart the bot or its controller
```

## 7. Verify

```
list_orphaned_positions()
get_portfolio_overview(include_lp_positions=True)
```

The orphan should be gone from the listing, and the position gone from the on-chain
view. Both, not either — the listing going quiet while the position persists is exactly
the failure mode step 5 warns about.

## The sequence, in one place

```
list_orphaned_positions()                       # 1. identify
get_portfolio_overview(include_lp_positions=True)  # 2. only if address unknown
manage_clmm(action="close", ...)                # 4. close on-chain (Gateway)
resolve_orphaned_position(executor_id=...)      # 5. bookkeeping (API)
manage_bots(...)                                # 6. restart controller if any
list_orphaned_positions()                       # 7. verify
```

Steps 4 and 5 are two different servers and neither implies the other. Doing 5 without 4
loses the position; doing 4 without 5 leaves a warning that will halt future work.

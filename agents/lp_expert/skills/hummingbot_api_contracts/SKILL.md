---
name: hummingbot_api_contracts
description: Interpret Hummingbot API portfolio and executor objects, schemas, lifecycle states, and ownership without confusing accounts, controllers, executors, positions, or wallets.
when_to_use: Read only for an unfamiliar schema or lifecycle result, a genuine exact-evidence contradiction, or close/recovery troubleshooting; ordinary Strategy-declared create and supervision paths need no skill read.
source: agent:lp_expert
---

# Hummingbot API Contracts

1. Treat account, controller, executor, LP position, and wallet as separate
   identities. Require explicit evidence for every mapping used by a mutation.
2. Read the live `lp_executor` schema before create. The installed API schema,
   not remembered fields, is authoritative.
   Current LP executors do not expose native time-limit, stop-loss, take-profit,
   or triple-barrier config; use only the active Strategy's read-only
   per-executor limit check and exact stop flow for those policies.
3. Use preloaded current-controller executors for initial capacity. Search one
   exact executor when lifecycle, range, config, or on-chain position detail is
   needed. Foreign-controller resources are observation-only.
4. Create only through the active Strategy's declared guard. Stop only one exact
   executor, once, with the Strategy's chosen `keep_position` behavior.
5. Reconcile create or stop using returned executor identity plus exact executor
   lifecycle and its embedded on-chain LP position state. Do not infer completion
   from a request response alone.
6. Preserve terminal, failed, closing, swapping, or uncertain resources as
   capacity-consuming until the Strategy's release contract is satisfied.
7. `positions_summary` contains executor-held residual positions, not active LP
   positions. A running LP with an embedded position address and an empty held
   summary is normal and does not consume an additional slot or block capacity.

Read `api-contract.md` when exact object boundaries, LP config fields, state
meaning, or close behavior matters.

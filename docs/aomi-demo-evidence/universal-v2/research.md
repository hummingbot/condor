# Narration V2 research — 2026-09-11

Research-stage notes below are preserved as written. Update, 12 September 2026: all six selected entry/exit flows now have [local UI and receipt evidence](README.md), using explicit Aomi-side official-SDK recipes and a substituted local test wallet. [Fresh upstream inventory](connector-recheck.json) confirms the same connector baseline and request states. Recording and hosted activation remain outstanding.

## Recommendation

Use Jupiter Lend Earn, Kamino Earn vaults, and direct PumpSwap liquidity provision. Lead with the connector backlog, show two protocols for lending/vaults, then broaden to AMM liquidity. This demonstrates the intended benefit of one Aomi Executor integration serving several venues. These are proposed demo targets, not verified end-to-end Aomi integrations.

## Current upstream baseline

Gateway main was checked at `f090f4ab7a8159b85fca2f3d4467970a5231cf5f`. Its connector directory contains 0x, dflow, jupiter, meteora, okx, orca, pancakeswap-sol, pancakeswap, raydium, titan, and uniswap. No dedicated jupiter-lend, kamino, or pumpswap connector was present.

Source: https://github.com/hummingbot/gateway/tree/f090f4ab7a8159b85fca2f3d4467970a5231cf5f/src/connectors

This establishes absence from the inspected upstream inventory, not absence from every fork, custom script, unmerged PR, or aggregator route. A swap aggregator reaching a pool does not establish direct LP management support.

## Candidate evidence

### Jupiter Lend Earn — strongest direct demand

Gateway issue 672 is open, authored by fengtality, and dated August 5, 2026. It explicitly describes scoping with no code yet. The proposal adds a lending trading type, dedicated connector, routes, configuration, position accounting, and tests. It covers supply/withdraw plus more complex borrowing and looping. Use only Earn supply/withdraw in this demo; do not imply coverage of the entire proposal.

- Request: https://github.com/hummingbot/gateway/issues/672
- Official operations and position model: https://developers.jup.ag/docs/lend/earn

Jupiter Swap integration is already available, but lending is separate. Official Earn documentation exposes instruction construction and position data, making it a plausible fit for Aomi's generic Solana execution paths. End-to-end compatibility remains to be demonstrated.

### Kamino Earn vaults — realistic Vault X

Issue 672 explicitly defers Kamino to a later connector, citing the SDK stack mismatch. This is demand for Kamino lending generally; choosing curated Earn vaults is our proposed demonstration, not an explicit request for that exact product subtype.

- Demand: https://github.com/hummingbot/gateway/issues/672
- Official Earn integration: https://kamino.com/docs/build/developers/earn
- Vault structure: https://kamino.com/docs/curators/vaults/concepts/how-vaults-work

An operator could allocate reserves to a selected curated vault and later recover them. Vault configuration includes allocations and fees. Deposits issue shares; where a farm is attached, shares can be staked automatically. Withdrawals require correct share conversion and available liquidity. A new vault under the same program demonstrates instance reuse, not arbitrary new-program support.

### PumpSwap direct liquidity — a different operation family

Gateway issue 570 is open, authored by fengtality, and dated December 11, 2025. It requests swaps, pool reads, liquidity addition/removal, position reads, configuration, and tests. PR 575 attempted a connector but was closed unmerged. The current upstream directory remains the decisive checked inventory.

- Request: https://github.com/hummingbot/gateway/issues/570
- Attempted connector: https://github.com/hummingbot/gateway/pull/575
- Official program operations: https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_SWAP_README.md

Demonstrate direct pool liquidity addition/removal, not a token swap potentially reachable through existing aggregators. The program exposes bounded two-asset deposits and LP-token-based withdrawals. Verify both underlying assets, LP tokens, and minimum outputs. This is a liquidity-management use case, not a claim of profitable market making.

## Alternatives considered

- Aerodrome: https://github.com/hummingbot/gateway/issues/502 documents a connector request with a 400K HBOT bounty in comments, but is closed. Useful EVM alternative; weaker clean current-demand story than the open requests above. Do not call the bounty active.
- Morpho: a strong conceptual ERC-4626 Vault X example, but the Hummingbot issue search found no direct request. Existing local Aomi SDK Morpho tools are read-only and do not prove execution. Better as a later EVM extension than evidence of explicit Hummingbot demand.
- Curve: historical connectors and a later refactor bounty complicate an unsupported-venue claim.

## Architecture and claim boundary

Local Aomi code provides shared Solana instruction and transaction execution mechanisms:

- `aomi/crates/tools/src/solana/tx/commit_tx.rs`: assembles staged instructions into a versioned transaction, including lookup-table inputs, or executes staged transaction blobs under signing policy.
- `aomi/crates/tools/src/solana/tx/ix.rs`: instruction encoding support.
- `aomi/crates/tools/src/solana/get_program/mod.rs`: program interface resolution.

These are building blocks, not proof that the Hummingbot integration can currently perform all selected operations. The next implementation phase must establish discovery, protocol instruction construction, simulation, policy, signing, lifecycle, and position reconciliation for each scene.

The defensible offering is no dedicated Hummingbot connector for each demonstrated venue. Protocol knowledge, account construction, metadata, and sometimes Aomi-side recipes or SDK integrations remain necessary. If those become substantial custom adapters, disclose that work rather than claiming zero integration effort. Simulation is useful but is not unique to Aomi and does not establish protocol safety or future profitability.

## Required evidence before recording

1. Pin the Hummingbot and Aomi builds and record unchanged executor code across venue switches.
2. Record how each target is discovered; show no demo-only hardcoded target substituted for discovery.
3. Verify entry and exit on each venue using independent receipts and position/balance reads.
4. Demonstrate policy refusal before submission with no transaction emitted.
5. Show exact supported operation scope, actual environment, and signer; do not imply all protocol operations or mainnet production signing from a narrower test.
6. Recheck upstream connector availability immediately before publishing the video.

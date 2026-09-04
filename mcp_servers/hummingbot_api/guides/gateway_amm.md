# manage_amm — direct AMM operations & pool creation

A stateless, chain- & DEX-agnostic tool for **AMM** liquidity and **pool creation**. You hold
position state in your journal (there is no AMM executor). Swaps that just need best execution go
through `create_order_executor`; CLMM LP goes through `create_lp_executor`.
`manage_amm` is **AMM-only**.

## Connectors & networks
- **meteora** — Solana DAMM v2 (constant-product AMM with **NFT positions**). Network: `solana-mainnet-beta`.
- **raydium** — Solana CPMM (fungible LP). Network: `solana-mainnet-beta`.
- **uniswap** — EVM V2 (fungible LP). Networks: `ethereum-mainnet`, `base-mainnet`, `arbitrum-mainnet`, …

`connector` and `network` are required for every action (no defaults). `wallet_address` is optional
(uses the default wallet).

## Actions
| action | type | required params |
|---|---|---|
| `pool_info` | read | `pool_address` |
| `position_info` | read | `pool_address` (returns aggregate + `positions[]`) |
| `positions_owned` | read | — (**meteora only**; fungible-LP → error) |
| `quote_liquidity` | read | `pool_address`, `base_token_amount`, `quote_token_amount` |
| `add_liquidity` | write | `pool_address`, `base_token_amount`, `quote_token_amount` (+ optional `position_address`) |
| `remove_liquidity` | write | `pool_address`, `percentage_to_remove` (+ **`position_address` required for meteora**) |
| `create_pool` | write | `base_token`, `quote_token`, `base_token_amount` (+ connector extra) |

Swaps are not part of this tool: the unified swap route handles every connector type
(pass connector as `name/type`, e.g. `raydium/amm`) — via the order executor for trading.

## Meteora DAMM v2 position model (important)
DAMM v2 positions are **NFTs**: a wallet may hold **several positions in one pool**, each an
independent asset differing in size, lock state, and fees — they are *not* interchangeable shares.
So this tool is **position-addressed**:
- `position_info` returns the pool **aggregate** plus a `positions[]` breakdown — each entry has a
  `position_address`. This is your discovery path. `positions_owned` lists **all** your positions
  across pools.
- `remove_liquidity` **requires** `position_address`; the percentage applies to *that* position, so
  "remove 100%" is a true exit of the named position (no silent partial-exit when several exist).
- `add_liquidity` takes an **optional** `position_address` — provide it to add to that position, or
  **omit it to open a NEW position**.
- **Journal the `position_address`** of each position you open; it is the source of truth for which
  position to act on later.

Fungible-LP AMMs (raydium, uniswap) have a single position per wallet: `position_address` is ignored
and `positions_owned` is not supported — use `position_info` with a known `pool_address`.

## create_pool — market-seeded, anti-snipe
Seed price priority: `initial_price` (quote per base) → `quote_token_amount` ratio → **live market
price** (fetched from the swap router so the pool opens on-market and bots can't arb your seed).
Only `base_token_amount` is required.

Per-connector `create_pool` extras ride `extra_params` under Gateway's own names (only the
owning connector consumes them; unknown keys are rejected):
- **meteora**: `extra_params={"configAddress": ...}` (**required**) — the DAMM v2 config account
  that fixes the fee schedule. Many configs are token-launch configs whose base fee starts near
  **99%** and decays; pick a static-fee config you actually want. Token order: `base_token` →
  token A, `quote_token` → token B.
- **raydium**: `ammConfigIndex` (optional; defaults to the first available config).
- **uniswap** (EVM): no extra params (fee is fixed at 0.30%).

## Examples
- Load this guide: `manage_amm()`
- Read a pool: `manage_amm(action="pool_info", connector="meteora", network="solana-mainnet-beta", pool_address="…")`
- Discover your positions: `manage_amm(action="positions_owned", connector="meteora", network="solana-mainnet-beta")`
- Open a new Meteora position: `manage_amm(action="add_liquidity", connector="meteora", network="solana-mainnet-beta", pool_address="…", base_token_amount="1", quote_token_amount="2")` (omit `position_address`)
- Exit a specific position: `manage_amm(action="remove_liquidity", connector="meteora", network="solana-mainnet-beta", pool_address="…", position_address="…", percentage_to_remove="100")`
- Create a Raydium CPMM pool (market-seeded): `manage_amm(action="create_pool", connector="raydium", network="solana-mainnet-beta", base_token="SOL", quote_token="USDC", base_token_amount="1")`
- Create a Meteora DAMM v2 pool: `manage_amm(action="create_pool", connector="meteora", network="solana-mainnet-beta", base_token="…", quote_token="USDC", base_token_amount="1", extra_params={"configAddress": "…"})`

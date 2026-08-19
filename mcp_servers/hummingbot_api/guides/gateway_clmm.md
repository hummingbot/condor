# manage_clmm — direct concentrated-liquidity positions

Direct, chain- and DEX-agnostic CLMM position control. Stateless: you hold position state in your
journal.

**This is the unmanaged path.** For normal LP work use `manage_executors` with `lp_executor` — it
owns range monitoring, rebalancing, and bounded close retries. Reach for `manage_clmm` when there is
no executor to do that for you:

- **Recovering an orphaned position** — the main reason this tool exists. Once an executor has
  terminated it cannot be told to close anything; `manage_executors(action="stop")` on it correctly
  returns a no-op. The position has to be closed by address.
- Inspecting a position's live amounts and uncollected fees.
- Collecting fees without disturbing the position.

## Connectors

| Connector | Chain | Pools |
|---|---|---|
| `meteora` | Solana | DLMM |
| `raydium` | Solana | CLMM |
| `orca` | Solana | Whirlpools |
| `pancakeswap-sol` | Solana | CLMM |
| `uniswap` | EVM | V3 |
| `pancakeswap` | EVM | V3 |

A `<name>/clmm` form is accepted too, so an orphan record's `lp_provider` (e.g. `orca/clmm`) can be
passed straight through.

## Actions

| Action | Required | Notes |
|---|---|---|
| `position_info` | — | Lists ALL the wallet's positions on the connector (each row names its pool) with amounts, range, and uncollected fees |
| `open` | `pool_address`, `lower_price`, `upper_price`, and at least one amount | Creates a position no executor tracks |
| `add_liquidity` | `position_address`, at least one amount | Keeps the existing range |
| `remove_liquidity` | `position_address`, `percentage_to_remove` | Partial withdrawal; the position account survives even at 100 |
| `close` | `position_address` | Withdraws everything, collects fees, closes the account |
| `collect_fees` | `position_address` | Fees only; position untouched |
| `create_pool` | `base_token`, `quote_token` | Creates a new EMPTY pool; `initial_price` optional (market price fetched when omitted) |

`remove_liquidity` at 100% and `close` are not the same thing: the former leaves an empty position
open, the latter closes the account. To recover an orphan, use `close`.

## create_pool

Creates a new (empty) CLMM pool — liquidity is added afterwards by opening positions. `initial_price`
is quote per base and optional: when omitted, the API fetches the live market price. Connector-specific
params ride `extra_params` under Gateway's own names (unknown keys are rejected with a 400):

- **meteora / orca**: `binStep`
- **meteora / uniswap / pancakeswap**: `feeBps`
- **raydium / pancakeswap-sol**: `ammConfigIndex`

Example: `manage_clmm(action="create_pool", connector="meteora", network="solana-mainnet-beta", base_token="SOL", quote_token="USDC", extra_params={"binStep": 20, "feeBps": 20})`

## pool_address on close and collect_fees

`pool_address` is optional and informational on both. Gateway needs only `position_address` to
close or collect, and the API's pre-close fee snapshot (positions-owned, which takes no pool
filter) does not use it either — so a position the API never recorded, such as every
`lp_executor` position, closes fine without one.

## Recovering an orphaned position

1. `manage_executors(action="orphaned")` — each entry carries `lp_provider`, `pool_address`,
   `position_address`, and `connector_name` (which for an LP executor holds the *network*, e.g.
   `solana-mainnet-beta`, not the DEX).
2. Confirm it is really still on-chain (lists every position the wallet owns; match by
   `position_address` / `pool_address`):
   `manage_clmm(action="position_info", connector=<lp_provider>, network=<connector_name>)`
3. Close it:
   `manage_clmm(action="close", connector=<lp_provider>, network=<connector_name>, position_address=<position_address>)`
4. `manage_executors(action="resolve_orphan", executor_id="...")` so it stops being reported.

If an entry has `needs_onchain_reconciliation: true` its position address was never persisted (an
API restart). Find it with `get_portfolio_overview(include_lp_positions=True)` or `position_info`
before closing anything.

Do not open a replacement position until the orphan is closed — a fresh `lp_executor` always mints a
new position and cannot adopt an existing one, so you would end up funding two.

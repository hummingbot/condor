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
| `uniswap` | EVM | V3 |
| `pancakeswap` | EVM | V3 |

A `<name>/clmm` form is accepted too, so an orphan record's `lp_provider` (e.g. `orca/clmm`) can be
passed straight through.

## Actions

| Action | Required | Notes |
|---|---|---|
| `position_info` | `pool_address` | Lists your positions in that pool with amounts, range, and uncollected fees |
| `open` | `pool_address`, `lower_price`, `upper_price`, and at least one amount | Creates a position no executor tracks |
| `add_liquidity` | `position_address`, at least one amount | Keeps the existing range |
| `remove_liquidity` | `position_address`, `percentage_to_remove` | Partial withdrawal; the position account survives even at 100 |
| `close` | `position_address` | Withdraws everything, collects fees, closes the account |
| `collect_fees` | `position_address` | Fees only; position untouched |

`remove_liquidity` at 100% and `close` are not the same thing: the former leaves an empty position
open, the latter closes the account. To recover an orphan, use `close`.

## pool_address on close and collect_fees

The API reads a position's pool from its own database. Positions opened by an `lp_executor` are
never in it — the bot opens those straight against Gateway — so for those you must pass
`pool_address` explicitly or the call fails with a 400 naming exactly this. The orphan listing
reports the pool for each orphan, so pass it through.

Gateway itself needs only `position_address` to close; `pool_address` is used to snapshot pending
fees before the close so they can be reported.

## Recovering an orphaned position

1. `manage_executors(action="orphaned")` — each entry carries `lp_provider`, `pool_address`,
   `position_address`, and `connector_name` (which for an LP executor holds the *network*, e.g.
   `solana-mainnet-beta`, not the DEX).
2. Confirm it is really still on-chain:
   `manage_clmm(action="position_info", connector=<lp_provider>, network=<connector_name>, pool_address=<pool_address>)`
3. Close it:
   `manage_clmm(action="close", connector=<lp_provider>, network=<connector_name>, position_address=<position_address>, pool_address=<pool_address>)`
4. `manage_executors(action="resolve_orphan", executor_id="...")` so it stops being reported.

If an entry has `needs_onchain_reconciliation: true` its position address was never persisted (an
API restart). Find it with `get_portfolio_overview(include_lp_positions=True)` or `position_info`
before closing anything.

Do not open a replacement position until the orphan is closed — a fresh `lp_executor` always mints a
new position and cannot adopt an existing one, so you would end up funding two.

### LP Executor
**This is the standard way to manage LP positions on CLMM DEXs.**

Manages liquidity provider positions on CLMM DEXs.
Opens positions within price bounds, monitors range status, tracks fees,
and auto-closes when price crosses configurable limit prices.

**Supported DEXs:**
- **Solana:** Meteora (DLMM), Raydium (CLMM), Orca (Whirlpools)
- **EVM:** Uniswap V3 (Ethereum, Arbitrum, Base, etc.), PancakeSwap V3 (BSC, Ethereum)

**Use when:**
- Providing liquidity on Solana or EVM DEXs
- Want automated position monitoring and fee tracking
- Earning trading fees from LP positions

**Avoid when:**
- Trading on CEX (use other executors)
- Want directional exposure only
- Not familiar with impermanent loss risks

#### Setup Workflow

**If user provides pool_address:** Skip to step 3 (get pool info directly)

1. **Find pools** (skip if pool_address provided):
   - Use `explore_dex_pools` with `action="list_pools"` and a DEX `connector` (`meteora`, `raydium`, `orca`, `uniswap`, `pancakeswap`)
   - Filter by `search_term` (e.g., "SOL", "ETH", "USDC") to find relevant pools
   - Sort by `volume` or `tvl` to find active pools

2. **Select pool**: User picks from list or provides address directly

3. **Get pool info**:
   - Use `explore_dex_pools` with `action="get_pool_info"`, DEX `connector`, `network`, and `pool_address`
   - **Networks:** `solana-mainnet-beta` (Solana), `ethereum-mainnet`, `arbitrum-one`, `base-mainnet`, `binance-smart-chain` (EVM)
   - Get current price, bin_step, trading_pair for position setup

4. **Determine position parameters**:
   - Ask user for amount(s) and range preference
   - Calculate `lower_price` / `upper_price` based on current price and user preference
   - Set `side` based on which tokens user is providing (1=BUY/quote-only, 2=SELL/base-only, 3=RANGE/both)

5. **Create executor**:
   - Use `manage_executors` with `action="create"`, `executor_type="lp_executor"`
   - Required params: `connector_name` (network), `lp_provider` (DEX), `trading_pair`, `pool_address`, `lower_price`, `upper_price`, `side`, plus at least one of `base_amount` / `quote_amount`

#### State Machine

```
NOT_ACTIVE → OPENING → IN_RANGE ↔ OUT_OF_RANGE → CLOSING → COMPLETE
                                                       ↘ SWAPPING → COMPLETE
```

Any state may transition to `FAILED` if max retries are exhausted with nothing left on-chain (open / swap) or if `early_stop` is called while still `OPENING`. An exhausted **close** instead terminates as an involuntary `POSITION_HOLD` (`hold_reason: "close_retries_exhausted"`) because the position is still live on-chain.

- **NOT_ACTIVE**: Initial state, no position yet
- **OPENING**: `add_liquidity` submitted, waiting for confirmation
- **IN_RANGE**: Position active, current price within bounds
- **OUT_OF_RANGE**: Position active but price outside bounds (no fees earned)
- **CLOSING**: `remove_liquidity` submitted, waiting for confirmation
- **SWAPPING**: Close-out swap in progress (only when `keep_position=False`, to return to the original quote asset)
- **COMPLETE**: Position closed permanently
- **FAILED**: Max retries reached with nothing left on-chain, or invalid config. A failed close retries in `CLOSING` with fresh on-chain state (bounded by `max_retries`); if the retries run out with the position still live, the executor terminates as an involuntary `POSITION_HOLD` with `hold_reason: "close_retries_exhausted"` and the record is flagged `orphaned_position` in `custom_info` — recover it (see below) before opening new positions

#### Key Parameters

**Required:**
- `connector_name`: **Chain-network identifier** — e.g., `"solana-mainnet-beta"`, `"ethereum-mainnet"`, `"arbitrum-one"`, `"base-mainnet"`, `"binance-smart-chain"`
  - **IMPORTANT:** This is the network, NOT the DEX. Do not pass `meteora/clmm` here — the API rejects it with `"Invalid network format"`.
- `lp_provider`: DEX + trading type in `dex/trading_type` format — used for pool ops, add/remove liquidity
  - **Solana:** `meteora/clmm`, `raydium/clmm`, `orca/clmm`
  - **EVM:** `uniswap/clmm`, `pancakeswap/clmm`
- `trading_pair`: Token pair (e.g., `SOL-USDC`)
- `pool_address`: Pool contract address
- `lower_price` / `upper_price`: Price range bounds
- `side`: Position side as a `TradeType` enum value — `1`=BUY (quote-only), `2`=SELL (base-only), `3`=RANGE (both/double-sided)

**Optional:**
- `swap_provider`: Swap provider for close-out swaps when `keep_position=False` (e.g., `jupiter/router`). If not provided, the network default is auto-resolved.
- `base_amount`: Amount of base token to provide (default: `0`)
- `quote_amount`: Amount of quote token to provide (default: `0`)
- `extra_params`: Connector-specific params, e.g., `{"strategyType": 0}` for Meteora
- `keep_position`: Default `True` — keep the net token change as a held spot position when closed. Set `False` to swap back to the original quote asset on close.

**Limit Prices (auto-close triggers, grid-executor style):**
- `upper_limit_price`: Close when current price ≥ this value (default `None` = no upper limit)
- `lower_limit_price`: Close when current price ≤ this value (default `None` = no lower limit)
- Both checks fire only when the position is `OUT_OF_RANGE`. When triggered, the executor closes with `CloseType.POSITION_HOLD` if `keep_position=True`, otherwise `CloseType.EARLY_STOP` (followed by a close-out swap).

#### Single-Sided vs Double-Sided Positions

**Single-sided (one asset only):**
- **Base token only** (e.g., 0.2 SOL when SOL is the base): `side=2` (SELL) with range ABOVE current price
  - Position starts out-of-range, enters range when price rises
  - Base converts to quote as price moves up through the range
- **Quote token only** (e.g., 50 USDC, or 1 SOL when SOL is the quote): `side=1` (BUY) with range BELOW current price
  - Position starts out-of-range, enters range when price falls
  - Quote converts to base as price moves down through the range

**Double-sided (both assets):**
- When user specifies both `base_amount` and `quote_amount`, ask:
  1. **Centered range** around current price? (±X% above/below current price)
  2. **Custom range**? (user specifies exact lower/upper bounds)
- Set `side=3` (RANGE) for double-sided positions

#### Limit Price Orders (Auto-Close Feature)

Use `upper_limit_price` and `lower_limit_price` to create limit-order-style LP positions that automatically close when price moves beyond your target range.

**SELL Limit (take profit on long, single-sided base):**
```
side=2, base_amount=X, quote_amount=0
lower_price > current_price                      # range above current price
upper_limit_price = upper_price * (1 + buffer)   # close trigger above range top
```
- Position starts OUT_OF_RANGE (price below range)
- When price rises into range: base → quote conversion, fees earned
- When price ≥ `upper_limit_price`: position auto-closes (mostly quote tokens)

**BUY Limit (accumulate on dip, single-sided quote):**
```
side=1, base_amount=0, quote_amount=X
upper_price < current_price                      # range below current price
lower_limit_price = lower_price * (1 - buffer)   # close trigger below range bottom
```
- Position starts OUT_OF_RANGE (price above range)
- When price falls into range: quote → base conversion, fees earned
- When price ≤ `lower_limit_price`: position auto-closes (mostly base tokens)

**Always set both limits when you want a closed strategy**, even on single-sided positions — otherwise the position will sit out-of-range indefinitely on the unprotected side.

**Key Benefits:**
- Earn LP fees while price moves through your target range
- Automatic execution without monitoring
- Better fills than traditional limit orders (continuous conversion vs single fill)

#### Meteora Strategy Types (extra_params.strategyType)

- `0`: **Spot** — Uniform liquidity across range
- `1`: **Curve** — Concentrated around current price
- `2`: **Bid-Ask** — Liquidity at range edges

#### Example: Single-sided SOL into a memecoin/SOL pool

```python
manage_executors(
    action="create",
    executor_type="lp_executor",
    executor_config={
        "connector_name": "solana-mainnet-beta",   # network, NOT the DEX
        "lp_provider": "meteora/clmm",             # DEX + trading type
        "trading_pair": "BONK-SOL",
        "pool_address": "<pool_address>",
        "lower_price": price * 0.80,
        "upper_price": price,                      # range entirely below current price
        "upper_limit_price": price * 1.10,         # close if price rallies 10% above range top
        "lower_limit_price": price * 0.80 * 0.90,  # close if price drops 10% below range bottom
        "side": 1,                                 # BUY = single-sided quote-only (SOL is quote)
        "base_amount": 0,
        "quote_amount": 1.0,
        "keep_position": False,                    # swap memecoin → SOL on close
    },
)
```

#### Important: Managing Positions

**Always use the executor tool (`manage_executors`) to open and close LP positions.**

- Use `manage_executors` with `action="stop"` to properly close positions and update executor status
- `manage_executors(action="stop")` accepts its own `keep_position` flag (separate from the config field) — MCP default is `False`, which closes the on-chain position and swaps back to the original quote asset. Pass `keep_position=True` to close the LP position but retain the net token change as a held spot position.
- If a position is closed externally (via DEX UI), manually mark the executor as `TERMINATED` in the database

**Verifying position status:**
- If uncertain about position status, use `get_portfolio_overview` with `include_lp_positions=True` to check on-chain state
- Compare on-chain positions with executor `custom_info.position_address`
- If position is closed on-chain but executor still shows `RUNNING`, manually update executor status in database to `TERMINATED`
- If position is open on-chain but executor still shows `OPENING`, the executor should eventually sync — if stuck, check API logs for errors

**Orphaned positions (executor terminated with a live on-chain position):**
- `manage_executors(action="orphaned")` lists terminated executors that may still own an on-chain position: involuntary holds (`POSITION_HOLD` with `hold_reason` set — a close that exhausted its retries), legacy `FAILED` executors whose final state carries a `position_address`, and `SYSTEM_CLEANUP` LP executors from an API restart (position address unknown — reconcile against `get_portfolio_overview(include_lp_positions=True)` or the gateway positions-owned endpoints)
- Each entry reports the `lp_provider` (the DEX, e.g. `orca/clmm`) and `pool_address` needed to close it. Note `connector_name` holds the **network** (e.g. `solana-mainnet-beta`), not the DEX
- Recover with `manage_clmm(action="close", connector=<lp_provider>, network=<connector_name>, position_address=..., pool_address=...)`. `pool_address` is REQUIRED here: LP-executor positions are opened by the bot straight against Gateway, so the API database has no row to read the pool from and the close returns 400 without it
- Stopping the executor will NOT close the position — it has already terminated, so `manage_executors(action="stop")` is correctly a no-op. A fresh `lp_executor` CANNOT adopt an existing position either; it always mints a new one, which would stack a second funded position on top of the orphan
- After the position is closed on-chain, mark it recovered with `manage_executors(action="resolve_orphan", executor_id="...")` so it stops appearing in orphan listings and warnings
- If an `lp_rebalancer` controller was managing the executor, restart the controller (or its bot) after resolving: the controller's orphan halt is held in memory and only clears on restart. `resolve_orphan` updates the API database, not the running controller
- Do NOT open new positions on the same funds until the orphan is recovered

**Stopping an already-terminated executor is a no-op, not an error:**
- `manage_executors(action="stop")` on a terminated executor returns `status="already_terminated"` with its final `close_type`, `position_address`, and `orphaned_position` flag instead of a 404
- A 404 means the executor ID is unknown to the API's database (never existed, or the database was unavailable at that moment)

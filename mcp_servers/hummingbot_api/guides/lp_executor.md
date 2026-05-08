### LP Executor
**This is the standard way to manage LP positions on CLMM DEXs.**

Manages liquidity provider positions on CLMM DEXs.
Opens positions within price bounds, monitors range status, tracks fees.

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
   - Use `explore_dex_pools` with `action="list_pools"` and `connector`
   - Solana connectors: `meteora`, `raydium`, `orca`
   - EVM connectors: `uniswap`, `pancakeswap`
   - Filter by `search_term` (e.g., "SOL", "ETH", "USDC") to find relevant pools
   - Sort by `volume` or `tvl` to find active pools

2. **Select pool**: User picks from list or provides address directly

3. **Get pool info**:
   - Use `explore_dex_pools` with `action="get_pool_info"`, `connector`, `network`, and `pool_address`
   - **Networks:** `solana-mainnet-beta` (Solana), `ethereum-mainnet`, `arbitrum-one`, `base-mainnet`, `binance-smart-chain` (EVM)
   - Get current price, bin_step, trading_pair for position setup

4. **Determine position parameters**:
   - Ask user for amount(s) and range preference
   - Calculate `lower_price` / `upper_price` based on current price and user preference
   - Set `side` based on which tokens user is providing (0=both, 1=quote-only, 2=base-only)

5. **Create executor**:
   - Use `manage_executors` with `action="create"`, `executor_type="lp_executor"`
   - Include all required params: `connector_name`, `trading_pair`, `pool_address`, `lower_price`, `upper_price`, amounts

#### State Machine

```
NOT_ACTIVE → OPENING → IN_RANGE ↔ OUT_OF_RANGE → CLOSING → COMPLETE
```

- **NOT_ACTIVE**: Initial state, no position yet
- **OPENING**: Transaction submitted to open position
- **IN_RANGE**: Position active, current price within bounds
- **OUT_OF_RANGE**: Position active but price outside bounds (no fees earned)
- **CLOSING**: Transaction submitted to close position
- **COMPLETE**: Position closed, executor finished

#### Key Parameters

**Required:**
- `connector_name`: CLMM connector in `connector/clmm` format
  - **Solana:** `meteora/clmm`, `raydium/clmm`, `orca/clmm`
  - **EVM:** `uniswap/clmm`, `pancakeswap/clmm`
  - **IMPORTANT:** Must include the `/clmm` suffix — using just `meteora` will fail
- `trading_pair`: Token pair (e.g., `SOL-USDC`)
- `pool_address`: Pool contract address
- `lower_price` / `upper_price`: Price range bounds

**Liquidity:**
- `base_amount`: Amount of base token to provide (default: 0)
- `quote_amount`: Amount of quote token to provide (default: 0)
- `side`: Position side (0=BOTH, 1=BUY/quote-only, 2=SELL/base-only)

**Auto-Close (Limit Range Orders):**
- `auto_close_above_range_seconds`: Close when price >= upper_price for this many seconds
- `auto_close_below_range_seconds`: Close when price <= lower_price for this many seconds
- Set to `null` (default) to disable auto-close

#### Single-Sided vs Double-Sided Positions

**Single-sided (one asset only):**
- **Base token only** (e.g., 0.2 SOL): Creates a SELL position (`side=2`) with range ABOVE current price
  - Position starts out-of-range, enters range when price rises
  - SOL converts to USDC as price moves up through the range
- **Quote token only** (e.g., 50 USDC): Creates a BUY position (`side=1`) with range BELOW current price
  - Position starts out-of-range, enters range when price falls
  - USDC converts to SOL as price moves down through the range

**Double-sided (both assets):**
- When user specifies both `base_amount` and `quote_amount`, ask:
  1. **Centered range** around current price? (±50% of position width above/below current price)
  2. **Custom range**? (user specifies exact lower/upper bounds)
- Set `side=0` (BOTH) for double-sided positions

**Position Management:**
- `keep_position=false` (default): Close LP position when executor stops
- `keep_position=true`: Leave position open on-chain, stop monitoring only
- `position_offset_pct`: Offset from current price for single-sided positions (default: 0.01%)

#### Limit Range Orders (Auto-Close Feature)

Use `auto_close_above_range_seconds` and `auto_close_below_range_seconds` to create limit-order-style LP positions that automatically close when price moves through the range.

**SELL Limit (Take Profit on Long):**
```
side=2, base_amount=X, quote_amount=0
lower_price > current_price (range above current price)
auto_close_above_range_seconds=60
```
- Position starts OUT_OF_RANGE (price below range)
- When price rises into range: base → quote conversion, fees earned
- When price rises above range for 60s: position auto-closes with quote tokens

**BUY Limit (Accumulate on Dip):**
```
side=1, base_amount=0, quote_amount=X
upper_price < current_price (range below current price)
auto_close_below_range_seconds=60
```
- Position starts OUT_OF_RANGE (price above range)
- When price falls into range: quote → base conversion, fees earned
- When price falls below range for 60s: position auto-closes with base tokens

**Key Benefits:**
- Earn LP fees while price moves through your target range
- Automatic execution without monitoring
- Better fills than traditional limit orders (continuous conversion vs single fill)

#### Meteora Strategy Types (extra_params.strategyType)

- `0`: **Spot** — Uniform liquidity across range
- `1`: **Curve** — Concentrated around current price
- `2`: **Bid-Ask** — Liquidity at range edges

#### Important: Managing Positions

**Always use the executor tool (`manage_executors`) to open and close LP positions.**

- Use `manage_executors` with `action="stop"` to properly close positions and update executor status
- If a position is closed externally (via DEX UI), manually mark the executor as `TERMINATED` in the database

**Verifying position status:**
- If uncertain about position status, use `get_portfolio_overview` with `include_lp_positions=True` to check on-chain state
- Compare on-chain positions with executor `custom_info.position_address`
- If position is closed on-chain but executor still shows `RUNNING`, manually update executor status in database to `TERMINATED`
- If position is open on-chain but executor still shows `OPENING`, the executor should eventually sync — if stuck, check API logs for errors

**Exception: Executor not found in API (404 error):**
- If API was restarted, executors may no longer exist in memory but positions remain on-chain
- In this case, close the on-chain position directly via the DEX UI or gateway API
- Then manually update the executor status in the database to `TERMINATED`

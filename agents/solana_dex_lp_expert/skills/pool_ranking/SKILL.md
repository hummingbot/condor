---
name: pool_ranking
description: Scan and rank Solana CLMM memecoin pools by fee yield (fees/TVL) to choose
  which to LP into.
when_to_use: When you need to find and rank Solana memecoin CLMM pools by fee yield
  (fees/TVL) to decide which pools to LP into. Use at the start of filling any free
  slot.
created: '2026-07-20T23:26:46Z'
source: agent:solana_dex_lp_expert
---

# Pool Ranking — fees/TVL yield scan

Goal: produce a ranked shortlist of Solana CLMM pools to LP into, quoted in `quote_asset`, on an allowed `venue`.

## 1. Source candidates (GeckoTerminal)
- `explore_geckoterminal(action="trending_pools", network="solana")` — momentum memecoins.
- `explore_geckoterminal(action="top_pools", network="solana", dex_id="<venue>")` — top by volume per venue (loop `venues`).
- Optionally `action="new_pools"` for fresh launches (higher risk/higher fee).

## 2. Filter
- Keep only pools whose **quote == `quote_asset`** (SOL or USDC).
- Keep only pools on an allowed **venue** (meteora / orca / raydium).
- Drop pools already held by an open slot.
- Drop ultra-thin TVL (`reserve_usd`) — thin pools gap out of range immediately and IL dominates. Rule of thumb: skip TVL < ~$25k unless volume is exceptional.

## 3. Score — fee yield
For each survivor: **fee_yield = fees(`ranking_window`) / reserve_usd**.
- GeckoTerminal pool fields give volume + reserve; when a direct fee figure isn't present, estimate `fees ≈ volume(window) × pool_fee_pct`.
- Higher fee_yield = more fee income per dollar of liquidity = better. This is the primary sort key.

## 4. Sanity-check the top few
- `explore_dex_pools(action="get_pool_info", connector=<venue>, network="solana-mainnet-beta", pool_address=...)` → live price, `bin_step`/`tick_spacing`, liquidity distribution.
- `explore_geckoterminal(action="ohlcv", network="solana", pool_address=..., timeframe="1h")` → volatility (for range width) and trend. **Reject** pools in a steep one-directional dump (fees won't cover IL / you'll be single-sided into a falling knife).

## 5. Output
Ranked list: `pool | venue | pair | TVL | vol(window) | fee_pct | fee_yield | bin_step/tick | price | trend`. Lead with the single best for the next free slot.

## Notes
- Raydium pool-info comes from the Raydium API (not Gateway); Meteora/Orca via Gateway.
- Re-rank every tick that has free slots — trending sets rotate fast.

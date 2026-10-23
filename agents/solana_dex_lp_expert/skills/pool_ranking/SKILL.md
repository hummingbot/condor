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

## 1. Run the routine — do NOT hand-scan

```
manage_routines(action="run", name="lp_scanner", agent="solana_dex_lp_expert",
  config={"quote_asset": <quote>, "venues": <venues>, "ranking_window": <window>,
          "top_n": 5, "exclude_pools": [<held pool_addresses>],
          "exclude_mints": [<held base mints>]})
```

One call. It sources from GeckoTerminal, filters to the quote asset and the
allowed venues, drops what you already hold, enriches the shortlist with live
pool-info and ranks by fee yield — returning `Pool`, `lp_provider`, `MintPair`,
`BaseMint`, `Bin/Tick`, `Price`, `FeeYield` per row. Take the top one.

> This skill used to spell out the scan by hand: several `explore_geckoterminal`
> calls, then `explore_dex_pools` and an OHLCV read per candidate. That is a
> dozen tool calls against a loop that budgets **eight for the whole tick**, and
> it reimplemented a routine that already exists — which the loop forbids in as
> many words ("do NOT hand-scan GeckoTerminal") and Condor's own routing rule
> forbids generally (LP-9). What remains below is the judgement the routine
> cannot make for you.

## 2. What the ranking means
- **fee_yield = fees(window) / TVL** is the sort key: fee income per dollar of
  liquidity. It is already computed for you.
- **Thin TVL gaps out of range immediately** and IL dominates. The routine floors
  this at `min_tvl_usd`; raise it if you keep getting gapped.
- **Reject a steep one-directional dump.** The routine ranks on yield and cannot
  see that you would be single-sided into a falling knife. If a candidate looks
  like one, take the next row rather than spending a second OHLCV call — the loop
  allows **one OHLCV check per tick**, and an exit decision has first claim on it.

## 3. Choosing between rows
- Never a second slot on a pool **or token** you already hold — pass your holdings
  as `exclude_pools` / `exclude_mints` and the routine enforces it.
- Prefer the row whose `Bin/Tick` leaves room for the width you want: a small
  `tick_spacing` caps the range hard (see `lp_range_config`).
- Do not re-enter a pool you just stopped out of unless it clearly re-ranks on
  top; churn pays fees twice for the same exposure.

## Notes
- `venues` are **GeckoTerminal dex ids**: `meteora`, `orca`, `raydium-clmm`.
  Plain `raydium` is the constant-product AMM and holds no CLMM positions (LP-4).
- Raydium pool-info comes from the Raydium API (not Gateway); Meteora/Orca via Gateway.
- Re-rank only on a tick with a free slot — trending sets rotate fast, but a
  ranking you cannot act on costs a call for nothing.

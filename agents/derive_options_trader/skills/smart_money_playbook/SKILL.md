---
name: smart_money_playbook
description: How to read the Smart-Money Flow composite and translate it into a bounded directional perp decision on any venue (Derive, Hyperliquid, Backpack, Pacifica, …). Use whenever interpreting onchain_flow output or deciding LONG/SHORT/HOLD for the Derive Options Trader agent's smart_money_flow strategy.
when_to_use: When the Derive Options Trader agent's smart_money_flow strategy needs to interpret the onchain_flow routine output, confirm it against options_flow positioning, decide a directional entry on perps, or manage an open flow-based position.
source: agent:derive_options_trader
---

# Smart-Money Playbook (Directional Perps, any venue)

The agent's edge is **capital-flow positioning**, not price patterns. This playbook
turns the `onchain_flow` routine output into a trade decision. Execution is
**perpetual futures on any venue** — Derive (`derive_perpetual`), Hyperliquid
(`hyperliquid`), Backpack (`backpack_perpetual`), Pacifica (`pacifica_perpetual`),
or others. (Orca spot was dropped: Whirlpools are CLMM spot and cannot express the
directional/short side this composite needs.)

## The composite (from `onchain_flow`)

| Signal | Source | What it tells you |
|---|---|---|
| Risk regime | CoinGecko `/global` (mcap 24h, BTC dominance) | RISK-ON / RISK-OFF / NEUTRAL |
| Per-asset flow score | `/coins/markets` volume-to-mcap + 24h change | How hard capital moves in/out of an asset |
| Trending momentum | `/search/trending` | What is heating up across the market |
| **Solana on-chain pulse** | GeckoTerminal SOL top pools | Crypto-native DeFi flow (vol, momentum, TVL) — the default signal. Solana carries materially deeper liquidity than XRPL. |
| XRPL pulse (optional) | XRPL JSON-RPC AMM/wallets | Legacy cross-check, off by default |
| **Options confirmation** | `options_flow` (Derive options API) | 25D risk reversal, put/call OI, term structure, GEX — confirms or fades the flow read |

**Flow score scale:** normalized −1 (strong outflow/down) … +1 (strong inflow/up).
**Entry threshold (DEMO MODE):** `|flow_score| >= 0.05`, ANY regime — direction is
the sign of the flow. If no asset clears 0.05, open the largest-|flow| asset anyway
(unless all |flow| < 0.02).
**Options confirmation:** always cross-check the `options_flow` composite before
sizing — full size when options agree with the flow direction, half size when they
strongly disagree (|composite| ≥ 0.40 against the flow), and use the options
direction as tie-breaker when the flow read is ambiguous.

## Decision matrix (Derive perps)

| Regime | Flow score | Action |
|---|---|---|
| any | asset ≥ +0.05 | **LONG** that asset (top flow first) |
| any | asset ≤ −0.05 | **SHORT** that asset |
| any | no asset clears \|flow\| ≥ 0.05 | open the largest-\|flow\| asset (sign of flow); HOLD only if all \|flow\| < 0.02 |

## Why this lane is open
Botcamp (110 strategies) is saturated with MM, funding arb, trend-following, and
pairs trading. **None trade capital-flow as the primary signal.** This agent owns
that lane — a discretionary flow reader reasoning over on-chain + cross-market data,
which is exactly what an LLM does better than hand-coded strategy. It also does not
overlap the server's other entries (Agora = news/sentiment; TFS/Sats = trend;
condor-simple = mean-reversion). Using **Solana on-chain flow** (vs thin XRPL)
makes the signal deeper and more credible.

## Risk rules (hard)
- Max 2 concurrent positions. Max leverage 3x (5x only at flow conviction ≥ 0.7).
- Respect `max_drawdown_pct` — Risk Engine enforces it.
- No forced trades on ambiguous reads. Macro-print windows (≤30 min): halve size.

## Journaling
Always record the *flow thesis*, not just the fill:
> "RISK-ON; SOL flow +0.52; Solana pulse +0.44 → LONG SOL-USDC."

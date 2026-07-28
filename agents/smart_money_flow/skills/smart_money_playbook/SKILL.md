---
name: smart_money_playbook
description: How to read the Smart-Money Flow composite and translate it into a bounded directional perp decision on any venue (Derive, Hyperliquid, Backpack, Pacifica, …). Use whenever interpreting onchain_flow output or deciding LONG/SHORT/HOLD for the Smart-Money Flow agent.
when_to_use: When the Smart-Money Flow agent needs to interpret the onchain_flow routine output, decide a directional entry on perps, or manage an open flow-based position.
source: agent:smart_money_flow
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

**Flow score scale:** normalized −1 (strong outflow/down) … +1 (strong inflow/up).
**Entry threshold:** `|flow_score| >= 0.4` AND regime-aligned.

## Decision matrix (Derive perps)

| Regime | Flow score | Action |
|---|---|---|
| RISK-ON | asset ≥ +0.4 | **LONG** that asset (top flow first) |
| RISK-OFF | asset ≤ −0.4 | **SHORT** that asset |
| RISK-ON | asset ≤ −0.4 | conflict — do not trade that asset |
| RISK-OFF | asset ≥ +0.4 | conflict — do not trade that asset |
| any | \|score\| < 0.4 | **HOLD** — stand aside |
| any | NEUTRAL regime | **HOLD** |

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
> "RISK-ON; ETH flow +0.52; Solana pulse +0.44 → LONG ETH 500."

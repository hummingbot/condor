---
name: Memecoin Market Intelligence
description: Four-hour skeptical Solana, Ethereum, and Robinhood Chain memecoin discovery and risk radar.
agent_key: null
skills: []
default_config:
  execution_mode: dry_run
  frequency_sec: 14400
  report_profile: memecoin_radar
  chains:
    - solana
    - ethereum
    - robinhood
  news_lookback_hours: 24
  market_history_days: 30
  nowcast_hours: 24
  near_term_days: 3
  established_extended_weeks: 2
  min_pair_age_hours: 6
  min_liquidity_usd: 50000
  min_chain_eligible_pairs: 5
  max_discovery_candidates: 100
  max_detailed_candidates: 40
  max_report_candidates: 3
  max_news_items: 40
  max_social_items: 60
  max_event_items: 20
  source_collection_budget_sec: 60
  report_language: en
  report_timezone: UTC
  risk_limits:
    max_position_size_quote: 0
    max_open_executors: 0
    max_drawdown_pct: -1
    shutdown_drawdown_pct: -1
default_trading_context: ''
created_by: 0
created_at: '2026-07-31T00:00:00+00:00'
---

# Memecoin Market Intelligence

Produce a fast, skeptical radar for speculative attention that has enough
observable liquidity and identity quality to justify further research. Cover
Solana, Ethereum, and Robinhood Chain. Base is not supported.

Horizons are 1–24 hours and 1–3 days. A 1–2 week view is allowed only for an
established address-curated token.

This report is a **Memecoin meta-and-chain radar**. Do not use a generic market
regime as the main frame. Memecoin research has
`research_posture="extreme_risk_research"` by definition; the useful changing
state is speculation temperature, meta momentum, liquidity quality, and chain
dispersion—not a generic “risk appetite” label.

## Source sequence

Call `gather_data` once with `strategy_key="memecoin_market_intelligence"`,
`scope="memecoin"`, and the arguments defined in `AGENT.md`. It concurrently
gathers news, social, BTC/ETH market backdrop, exact token discovery, and
events. It never gathers issuer fundamentals.

## Analysis sequence

1. Build the **Meta Landscape and Momentum** from CoinGecko's provider-maintained
   category summaries and bounded constituent lists. Preserve category IDs and
   names and use only the controlled display themes `dog`, `cat`, `frog`,
   `political`, `ai`, and `celebrity`. An unclassified token is not a market
   theme: keep it out of theme leadership rather than calling it “emerging”.
   Never sum overlapping provider categories.
2. For each leading meta, explain momentum, durability, representative tokens,
   attention/news context, risk, and invalidation. Never substitute FDV for
   missing market cap.
3. Establish BTC/ETH only as a compact background condition.
4. Separate the established memecoin basket from discovery tokens.
5. Require exact chain, token, deepest eligible pair, approved quote, pair age,
   liquidity, volume, transactions, and discovery origin.
6. Compare attention with liquidity, turnover, transaction balance, pair age,
   paid promotion, and identity warnings.
7. Keep GeckoTerminal organic-oriented Solana/Ethereum discovery separate from
   DEX Screener paid-attention feeds.
8. Keep Robinhood Chain in a separate new-chain cohort. Its
   promotion-biased coverage is not directly comparable with Solana or
   Ethereum and must never be rendered as observed zero.
9. Exclude every canonical Robinhood Stock Token or ETF by address whether it
   appears as base, quote, promoted token, or session focus.
10. Surface a balanced eligible-pair highlight set: established tokens first,
   then organic-oriented discovery, then clearly labeled paid-visible
   discovery. Quote assets and obvious infrastructure tokens are not memecoin
   highlights.
11. Build one all-theme landscape heatmap from every retained CoinGecko
    category summary. Tile size is provider category market cap and color is
    24-hour market-cap direction. Use the bounded categorized-coin sample,
    CoinGecko Solana/Robinhood membership, and its Ethereum platform mapping
    only for the separate chain-footprint table. Categories can overlap, so
    each tile is independent and must not be summed. Keep the much smaller
    exact-pair screen separate for liquidity, age, turnover, and tradability
    checks.

For cross-source attention, treat a token named in a controlled meta's
`representative_symbols` as present in the current catalog and carry the meta
evidence ID into its attention row. Never turn a missing theme-chain row into
an observed zero; it means the theme was not present in the expanded
constituent sample. Numerical superlatives such as “strongest” or “largest”
must be recomputed from the same retained all-theme rows used by the landscape
heatmap.

Missing or stale Robinhood Stock Token/ETF exclusions block ranked Robinhood
candidates. Promotion-biased Robinhood discovery cannot support a
high-confidence chain conclusion. A newly discovered token cannot exceed
`moderate` confidence. Missing BTC/ETH backdrop leaves token facts reportable
but makes directional candidate state unavailable.

Rank at most three tokens as `priority_research`,
`conditional_watch`, `risk_watch`, or `avoid_for_now`. Never imply audit,
contract safety, rug resistance, or a buy/sell instruction.
When eligible pairs and cross-bundle support exist, analyze those pairs before
filling the ranking with excluded `avoid_for_now` examples. Excluded pairs
remain in the audit table and may be ranked only when their exclusion itself is
decision-useful.

In run once or loop, build one report with `market_view`, `movers_view`,
optional `event_outlook`, up to five exact cached official `event_impacts`, and
up to three `research_highlights`. Do not supply generic `drivers`; the
deterministic meta and chain views are the useful market map.

The reader report has three sections only: memecoin snapshot and leaders;
official calendar; analyst view and token research highlights. Use
deterministic meta and chain charts, not a generic regime, narrative, or risk
appetite chart. Paid visibility is a secondary breakdown by meta or chain, not
an executive warning by itself. Lead with a conclusion and then the backing
visual; exact-pair details belong in the technical audit.

Every meta claim must cite the matching controlled provider-category
observation; representative-token evidence must belong to that same category.
A ranked token thesis must also cite its exact pair and supporting news or
social evidence that names that token or its meta. Contrary evidence is
additive and never satisfies this support requirement. Cross-source attention
should distinguish current catalog, DEX/chain discovery, news, and social
presence; unavailable sources stay blank instead of becoming zero. Link news
evidence to the exact retained article, not a generic publication market,
news, or tag page.

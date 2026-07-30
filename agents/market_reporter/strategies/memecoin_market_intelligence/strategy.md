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
  max_report_candidates: 15
  max_news_items: 40
  max_social_items: 60
  max_event_items: 20
  source_collection_budget_sec: 120
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

## Source sequence

Call each at most once with this Strategy key and current-config bounds:

1. `news_source`
2. `social_source`
3. `market_signal_source` for BTC/ETH and broad-liquidity backdrop
4. `token_discovery_source`
5. `event_source`

Never call `fundamentals_source`.

## Analysis sequence

1. Establish current BTC/ETH and broad crypto risk backdrop.
2. Separate the established memecoin basket from discovery tokens.
3. Require exact chain, token, deepest eligible pair, approved quote, pair age,
   liquidity, volume, transactions, and discovery origin.
4. Compare attention with liquidity, turnover, transaction balance, pair age,
   paid promotion, and identity warnings.
5. Keep GeckoTerminal organic-oriented Solana/Ethereum discovery separate from
   DEX Screener paid-attention feeds.
6. Keep Robinhood Chain in a separate emerging-chain cohort.
7. Exclude every canonical Robinhood Stock Token or ETF by address whether it
   appears as base, quote, promoted token, or session focus.

Missing or stale Robinhood Stock Token/ETF exclusions block ranked Robinhood
candidates. Promotion-biased Robinhood discovery cannot support a
high-confidence chain conclusion. A newly discovered token cannot exceed
`moderate` confidence. Missing BTC/ETH backdrop leaves token facts reportable
but makes directional candidate state unavailable.

Rank at most `max_report_candidates` as `priority_research`,
`conditional_watch`, `risk_watch`, or `avoid_for_now`. Never imply audit,
contract safety, rug resistance, or a buy/sell instruction.

In run once or loop, build one report with the Memecoin payload blocks:
`broad_backdrop`, `established_basket`, `chain_cohorts`, `discovery_funnel`,
`candidate_quality`, and `exclusions`.

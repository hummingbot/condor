---
name: Crypto Market Intelligence
description: Daily liquid-crypto regime, breadth, derivatives, narrative, risk, and research-candidate report.
agent_key: null
skills: []
default_config:
  execution_mode: dry_run
  frequency_sec: 86400
  coverage_mode: primary
  report_profile: daily_dual_horizon
  news_lookback_hours: 72
  market_history_days: 90
  near_term_days: 7
  medium_term_weeks: 6
  max_news_items: 60
  max_social_items: 60
  max_event_items: 30
  max_research_candidates: 8
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

# Crypto Market Intelligence

Analyze whether liquid crypto risk is expanding, contracting, or rotating and
which liquid assets deserve further research. The horizons are 1–7 days and
2–6 weeks.

## Source sequence

Call each at most once with this Strategy key and current-config bounds:

1. `news_source`
2. `social_source`
3. `market_signal_source`
4. `event_source`
5. `fundamentals_source` only when `coverage_mode` is `both`

Never call `token_discovery_source`. Newly discovered DEX tokens are outside
this playbook.

## Analysis sequence

1. Establish BTC and ETH trend, volatility, and liquidity regime.
2. Test whether at least 70% of the configured liquid universe has valid
   history and whether breadth confirms the benchmark move.
3. Compare spot activity with funding and open-interest changes.
4. Evaluate stablecoin, DeFi, rates, dollar, credit, and event evidence when
   available.
5. Cluster news and social evidence into narratives, then test them against
   price, volume, breadth, and positioning.
6. Rank at most `max_research_candidates` liquid assets with contrary evidence
   and invalidation.

A positioning conclusion cannot be `high` without current BTC and ETH
derivatives evidence. One-venue derivatives coverage carries a venue-bias
warning. Below the BTC/ETH plus 70% breadth gate, produce observations only and
no ranked candidates.

For `coverage_mode: both`, add full TradFi backdrop and candidate sections after
the Crypto-first sections. Do not call an unsupported data host or treat missing
TradFi evidence as Crypto evidence.

In run once or loop, build one report with the Crypto payload blocks:
`benchmark_regime`, `breadth`, `liquidity`, `derivatives_positioning`, and
`narrative_rotation`.

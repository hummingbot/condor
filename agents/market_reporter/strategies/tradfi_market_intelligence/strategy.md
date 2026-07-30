---
name: TradFi Market Intelligence
description: U.S.-first equity and cross-asset macro regime, sector, fundamentals, catalyst, and research-candidate report.
agent_key: null
skills: []
default_config:
  execution_mode: dry_run
  frequency_sec: 86400
  coverage_mode: primary
  report_profile: us_daily_dual_horizon
  news_lookback_hours: 72
  market_history_days: 180
  near_term_sessions: 5
  medium_term_weeks: 6
  max_news_items: 60
  max_social_items: 40
  max_event_items: 40
  max_research_candidates: 8
  source_collection_budget_sec: 120
  report_language: en
  report_timezone: America/New_York
  risk_limits:
    max_position_size_quote: 0
    max_open_executors: 0
    max_drawdown_pct: -1
    shutdown_drawdown_pct: -1
default_trading_context: ''
created_by: 0
created_at: '2026-07-31T00:00:00+00:00'
---

# TradFi Market Intelligence

Analyze which macro regime and cross-asset signals are driving U.S. equities and
where leadership, fragility, and verified event risk are concentrated. The
horizons are 1–5 completed trading sessions and 2–6 weeks.

## Source sequence

Call each at most once with this Strategy key and current-config bounds:

1. `news_source`
2. `social_source`
3. `market_signal_source`
4. `fundamentals_source`
5. `event_source`

Never call `token_discovery_source`.

## Analysis sequence

1. Read the Treasury curve, dollar, volatility, and high-yield credit before
   interpreting equity direction.
2. Require last-completed-session data for SPY, QQQ, and at least eight of the
   eleven sector ETFs for a directional equity-regime view.
3. Require the Treasury curve and two of volatility, high-yield credit, and the
   dollar for a sufficient cross-asset regime.
4. Measure index, sector, and large-cap breadth and relative strength.
5. Add lagged CFTC positioning with report and release dates.
6. Use SEC fundamentals and filings only with exact ticker-to-CIK identity.
7. Rank an equity only with asset and benchmark history plus compatible SEC
   evidence or a second non-social primary/news source.

Do not invent a TradFi Fear & Greed number. Show the underlying risk-appetite
components separately. Do not infer earnings dates, consensus, future
fundamentals, or real-time CFTC positioning. When market-session status is
unverified, label it `unknown` and reason from the last completed observation.

For `coverage_mode: both`, append full Crypto backdrop and candidates after the
TradFi-first sections.

In run once or loop, build one report with the TradFi payload blocks:
`macro_regime`, `rates_credit_dollar`, `equity_breadth`, `sector_rotation`, and
`cftc_positioning`.

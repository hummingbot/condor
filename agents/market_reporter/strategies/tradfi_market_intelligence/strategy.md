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
  max_research_candidates: 3
  source_collection_budget_sec: 60
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

This report is a **TradFi macro-to-asset brief**, not a collection of market
tables. Use `research_posture="conservative"` by default and separate that
discipline from the observed cross-asset risk state.

## Source sequence

Call `gather_data` once with `strategy_key="tradfi_market_intelligence"` and
the arguments defined in `AGENT.md`. For primary coverage use `scope="tradfi"`;
use `scope="both"` only when current config says `coverage_mode: both`. When
the current session supplies no focus assets, pass `focus_assets=[]`; the
private market and fundamentals collectors provide bounded built-in universes.
The routine concurrently gathers news, social, market, fundamentals, and
events. It never performs token discovery.

## Analysis sequence

1. Read the Treasury curve, dollar, volatility, high-yield credit, oil, and
   gold before interpreting equity direction.
2. Explain at most five evidence-linked macro drivers in plain English. Give
   each driver a distinctive one-to-three-word `short_label` that summarizes
   its concept for the chart, such as `Credit Stress` or `Policy Risk`. Then
   explain what changed, why it matters, which sectors or assets are affected,
   and what would weaken the view.
3. Use SPY as the broad-market anchor and require last-completed-session data
   for at least eight of the twelve representative S&P 500 stocks before
   making a directional equity-regime view. Treat sector ETFs as supporting
   rotation evidence, not as the report's featured assets.
4. Require the Treasury curve and two of volatility, high-yield credit, and the
   dollar for a sufficient cross-asset regime.
5. Connect official releases and verified calendars to transmission paths for
   rates, sectors, ETFs, and stocks. Group timing into immediate, next, and
   later windows; never fabricate an earnings date.
6. Measure SPY, representative S&P 500 stock breadth, and stock relative
   strength. Use sector breadth only to explain where participation is
   concentrated.
7. Add lagged CFTC positioning with report and release dates.
8. Use SEC fundamentals and filings only with exact ticker-to-CIK identity.
9. Always identify observed S&P 500 stock leaders and laggards when valid price
   history exists. Show company names with tickers. Keep sector ETFs and macro
   proxies in supporting analysis rather than presenting them as stock ideas.
   Show SEC comparable-period context when available and label its absence;
   these highlights are not ranked picks.
10. Rank at most three equities for further research, and only with asset and
    benchmark history plus compatible SEC evidence or a second non-social
    primary/news source. Use each asset's exact `asset_evidence_id`.

Every claim about how many tracked S&P 500 stocks are rising must use
`analysis_context.strategy_features.sp500_stock_breadth`, the returned
`tradfi_sp500_sample_breadth` aggregate evidence item, and agree with its
positive, negative, and observed counts. Preserve its derivation and
underlying evidence IDs. Sector breadth remains available only as supporting
rotation evidence. Do not describe the sample as the full S&P 500. Use only
completed trading-session observations in performance visuals.

Do not invent a TradFi Fear & Greed number. Show the underlying risk-appetite
components separately. Do not infer earnings dates, consensus, future
fundamentals, or real-time CFTC positioning. When market-session status is
unverified, label it `unknown` and reason from the last completed observation.

For `coverage_mode: both`, cover the Crypto backdrop and any supported Crypto
highlight inside the same three reader sections after the TradFi-first view.
Do not add a fourth section or compare unmatched sessions as if timestamps were
identical.

In run once or loop, build one report with `market_view`, `movers_view`,
optional `event_outlook`, up to five `drivers`, up to five exact cached
official `event_impacts`, and up to three `research_highlights`. Keep the
default posture conservative.

The reader report has three sections only: market snapshot and leaders;
official calendar; analyst view and research highlights. Lead with the
conclusion, then use a small readable visual or compact table as evidence.
Full source details belong in the technical audit.

Coverage is not complete merely because every requested endpoint returned.
Complete coverage needs SPY, at least eight representative S&P 500 stocks, a
Treasury curve, volatility, credit, dollar, current official news, at least two
verified dated events, and comparable fundamentals for the highlighted stocks. Use
`sufficient` or lower when any block is absent, and never use high executive
confidence outside complete coverage. Mark stock highlights as
`momentum-only` unless fundamentals and a catalyst support promotion into a
ranked thesis. Link a scheduled event or policy claim to its exact official
release or calendar item rather than a generic agency landing page.

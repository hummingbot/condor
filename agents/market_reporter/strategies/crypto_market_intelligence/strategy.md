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
  max_research_candidates: 3
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

# Crypto Market Intelligence

Analyze whether liquid crypto risk is expanding, contracting, or rotating and
which liquid assets deserve further research. The horizons are 1–7 days and
2–6 weeks.

This report is a **Crypto concern-and-rotation brief**, not a generic market
dashboard. Use `research_posture="conservative"` by default. Separate that
discipline from the observed risk-appetite state.

## Source sequence

Call `gather_data` once with `strategy_key="crypto_market_intelligence"` and
the arguments defined in `AGENT.md`. For primary coverage use `scope="crypto"`;
use `scope="both"` only when current config says `coverage_mode: both`. The
routine concurrently gathers news, social, market, and events, plus
fundamentals for `both`. It never gathers newly discovered DEX tokens.

## Analysis sequence

1. Start with the current provider-ranked universe in `analysis_context`;
   exclude stablecoins, wrappers, bridged duplicates, and liquid-staking
   representations from the research universe. BTC and ETH remain anchors.
   Treat the static universe only as an explicitly aged emergency fallback.
2. Establish BTC and ETH trend, volatility, global market cap, dominance,
   breadth, liquidity, and fear/greed regime.
3. Test whether at least 70% of the configured liquid universe has valid
   history and whether breadth confirms the benchmark move.
4. Compare spot activity with funding and open-interest changes.
5. Evaluate stablecoin, DeFi, rates, dollar, credit, and event evidence when
   available.
6. Identify observed seven-day leaders and laggards from the valid liquid
   universe even when ranking gates fail; label these observations, not picks.
7. Explain at most five evidence-linked drivers in plain English. Give each
   driver a distinctive one-to-three-word `short_label` that summarizes its
   concept for the chart, such as `Narrow Participation` or `Soft Liquidity`.
   Focus on what is moving the market, why it matters, who is affected, and
   what would weaken the view.
8. Rank at most three liquid assets for further research. Use the exact
   `asset_evidence_id`, contrary evidence, and invalidation.

A breadth statement must use
`analysis_context.strategy_features.breadth.aggregate_observation`, the
returned `liquid_crypto_breadth` aggregate evidence item, not a hand-picked
subset of assets. Preserve its configured count, positive count, derivation,
and underlying evidence IDs. The builder attaches its exact evidence ID as a
backstop. When a technical-history return and the current provider catalog have
opposing signs for the same asset, state both windows and both evidence IDs, do
not average them, and cap the affected executive stance or candidate at
`moderate` until they converge.

A positioning conclusion cannot be `high` without current BTC and ETH
derivatives evidence. One-venue derivatives coverage carries a venue-bias
warning. Missing derivatives alone does not block a moderate-confidence
candidate when BTC/ETH, the 70% breadth gate, and the cross-bundle evidence gate
are satisfied. Below the BTC/ETH plus 70% breadth gate, produce observations
only and no ranked candidates.

For `coverage_mode: both`, cover the TradFi backdrop and any supported TradFi
highlight inside the same three reader sections after the Crypto-first view.
Do not add a fourth section, call an unsupported data host, or treat missing
TradFi evidence as Crypto evidence.

In run once or loop, build one compact report package with
`market_view`, `movers_view`, optional `event_outlook`, up to five `drivers`,
up to five exact cached official `event_impacts`, and up to three
`research_highlights`. Keep the default posture conservative.

The reader report has three sections only: market snapshot and leaders;
official calendar; analyst view and research highlights. Lead with the
conclusion, then show only the deterministic visual or compact table that
supports it. Raw evidence belongs in the technical audit.

Do not reuse one generic market/news evidence pair for every driver. Missing
breadth, sentiment, social, liquidity, or positioning is a limitation, not
zero. A view that names funding, open interest, or positioning must cite that
derivatives evidence. Use direct article or official-release URLs when a
specific item supports a claim. Suppress a visual when its retained history or
asset count does not support its title.

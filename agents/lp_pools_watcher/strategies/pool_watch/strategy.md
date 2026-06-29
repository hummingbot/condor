---
name: pool_watch
description: Scheduled analysis strategy for ranking Orca and Meteora pools by 24h
  fees, liquidity, trend quality, and warning status.
agent_key: null
skills:
- routine_builder
default_config:
  execution_mode: run_once
  frequency_sec: 3600
  protocols:
  - orca
  - meteora
  max_results: 5
  max_pools_per_protocol: 20
  discovery_routine: discover_lp_pools
  scoring_routine: optimal_pool_scorer
  risk_limits:
    allow_trading: false
    allow_swaps: false
    allow_lp_changes: false
    allow_executor_management: false
default_trading_context: ''
created_by: 1684711897
created_at: '2026-06-29T11:02:54.917366+00:00'
---

# Pool Watch Strategy

Goal:
Rank Orca and Meteora pools and return the best candidates based on the user's criteria.

Scope:
- Protocols: Orca and Meteora.
- Market: Solana DEX pools only.
- Actions: analysis only. Do not trade, swap, add/remove LP, manage executors, change leverage, or start/stop bots.

Dedicated routines:
- First run `discover_lp_pools` to collect candidate pools and normalize them into scorer-ready objects.
- Then run `optimal_pool_scorer` with `config.pools` set to the `pools` returned by `discover_lp_pools`.
- Discovery returns raw pool fields for chaining and saves a Condor `ReportBuilder` discovery report.
- Scoring returns readable text and saves a Condor `ReportBuilder` scoring report.

Each run should:
1. Run `discover_lp_pools` with protocols `orca` and `meteora`.
2. Use `max_pools_per_protocol` to limit discovery size; `max_results` is only for final reporting.
3. Review `sources`, `api_errors`, and `data_gaps` from discovery.
4. Pass discovered `pools` into `optimal_pool_scorer`.
5. Return the scorer text plus discovery sources and data gaps.

Reporting:
- `discover_lp_pools` creates a report titled `LP Pool Discovery` with Input Parameters, Input Endpoints, Candidate Pools, Sources, Data Gaps, API Errors, and Discovery Config sections.
- `optimal_pool_scorer` creates a report titled `LP Pool Watcher` with Input Parameters, Input Pools, Best Pools, Watchlist / Flagged, Excluded, Data Gaps, and Scoring Thresholds sections.
- The routine runner may still show `table_data: null`; that is normal for report-builder routines because saved reports are produced by `ReportBuilder.save()`.

Journaling:
- If a valid runtime `agent_id` has a journal, write one short action entry summarizing the run.
- If journal writing returns `no journal available`, do not retry with the strategy id. Include the action summary in the final response instead.

Default scorer thresholds:
- Minimum liquidity: 100,000 USD.
- Preferred liquidity: 1,000,000 USD.
- Target fee APR proxy: 30% annualized from 24h fees.
- Strong downtrend: below -3% over 24h.
- Stale data: older than 60 minutes.

Output format:
- Best pools: ranked bullets, max 5.
- Watchlist: promising but incomplete/flagged pools, max 5.
- Excluded: only the most important exclusions, max 5.
- Data gaps: what could not be verified.
- Sources: APIs used for Orca and Meteora.

Default stance:
If confidence is low, say so and explain which data is missing.

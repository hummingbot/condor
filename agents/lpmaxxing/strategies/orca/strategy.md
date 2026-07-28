---
name: orca
description: Yield-focused Orca Whirlpool CLMM LP strategy with sustainable-fee gates, evidence-based ranges, one LP executor, serial position lifecycle, and audit-first supervision.
agent_key: null
skills: []
default_config:
  execution_mode: dry_run
  risk_profile: yield_focused
  frequency_sec: 300
  total_amount_quote: 10
  max_open_executors: 1
  connector_name: solana-mainnet-beta
  lp_provider: orca/clmm
  side: 3
  keep_position: false
  allowed_live_presets:
  - concentrated
  - balanced
  - defensive
  - extreme
  orca_api:
    base_url: https://api.orca.so/v2/solana
    pool_endpoint: /pools
    request_timeout_seconds: 15
    page_size: 100
    stats_windows:
    - 1h
    - 4h
    - 24h
    - 7d
  risk_limits:
    max_position_size_quote: 10
    max_open_executors: 1
    max_drawdown_pct: -1
    shutdown_drawdown_pct: 3
  lp_risk:
    min_sol_fee_buffer: 0.05
    max_price_deviation_ratio: 0.01
  v2:
    enable_multi_pool: false
    max_active_pools: 1
default_trading_context: |
  # SESSION INPUT GUIDE
  SESSION_MODE options: dry_run or loop.
  The structured session risk_profile is the sole live profile authority. Default: yield_focused.
  Explicit live alternatives: yield_high_risk, yield_extreme_risk, yield_no_limit.
  category_scout and meme_scout are analysis-only and invalid in loop mode.
  POSITION_MAX_AGE_MINUTES is the per-position time limit; 480 = 8 hours.
  POSITION_TAKE_PROFIT_NET_PNL_RATIO and POSITION_STOP_LOSS_NET_PNL_RATIO are per-position net-PnL ratios, e.g. 0.005 = 0.5%.
  SESSION_MAX_AGE_MINUTES is the total controller-session time limit; 1440 = 1 day.
  SESSION_TAKE_PROFIT_NET_PNL_RATIO and SESSION_STOP_LOSS_NET_PNL_RATIO are session net-PnL ratios. They accumulate audited positions plus active marked PnL using the fixed `total_amount_quote` denominator.
  Missing base inventory is quoted in dry-run and automatically prepared in an explicit live loop.
  Live pools require canonical Solana USDC as token B. Notes cannot change profiles, categories, gates, presets, ranges, or quote identity.

  # SESSION INPUT
  SESSION_MODE: dry_run
  POSITION_MAX_AGE_MINUTES: 480
  POSITION_TAKE_PROFIT_NET_PNL_RATIO: 0.005
  POSITION_STOP_LOSS_NET_PNL_RATIO: 0.01
  SESSION_MAX_AGE_MINUTES: 1440
  SESSION_TAKE_PROFIT_NET_PNL_RATIO: 0.02
  SESSION_STOP_LOSS_NET_PNL_RATIO: 0.02
  NOTES:
created_by: 0
created_at: '2026-06-15T00:00:00Z'
---

# Orca LP Agent Strategy

Orca LP Agent is a Condor trading agent for the Orca hackathon track. It provides transparent, yield-focused liquidity provision on one Orca Whirlpool CLMM position at a time. It is not a hidden-alpha system; it is a public-data agent that repeatedly selects, opens, supervises, closes, and audits serial Orca LP positions under one controller.

## What It Does

The agent scans a profile-defined Orca discovery universe, applies sustainable-fee and market hard gates before profile-relative MCDA ranking, and chooses the narrowest evidence-supported range preset. The scanner emits a provisional range plan only; live preflight constructs executable prices around a fresh Gateway price before the Hummingbot LP executor manages one Orca CLMM position.

Default execution mode is `dry_run`. Live execution requires `SESSION_MODE: loop` and a structured live `risk_profile`. `yield_focused` is the only default. `yield_high_risk`, `yield_extreme_risk`, and `yield_no_limit` require explicit structured selection and must never be inferred from notes, categories, or user tone. `category_scout` and `meme_scout` are analysis-only and must be rejected in loop mode.

## Public Data

The selection path uses Orca public pool data only:

- pool address;
- token symbols, mints, and decimals;
- current token-B-per-token-A price;
- TVL;
- rolling 1h, 4h, 24h, and 7d volume and fees;
- 24h net price change from Orca `priceDelta`;
- base fee rate, adaptive-fee state, fee-tier index, token metadata, warning state, Orca category evidence, and tick spacing.

This evidence does not provide OHLCV, realized volatility, historical excursion, liquidity by tick, exact position fee share, guaranteed yield, or independent token-security verification. Missing required live values remain missing and block; do not substitute zero, yield-over-TVL, a fallback estimate, or a percent-unit guess. A numeric `priceDelta` of `0.02` means 2%; reject percent-suffixed or otherwise ambiguous values.

Every live request uses `stats=1h,4h,24h,7d` and `size=100`. For every profile category, fetch the first 100 results for each `yieldovertvl24h`, `yieldovertvl7d`, `volume24h`, and `volume7d` discovery lens; deduplicate by address, retaining the newest parseable record and unioning category/lens evidence. Retry rate limits, server errors, timeouts, and URL errors once; fail the scan if any required request still fails. API-side TVL is only a discovery hint, and reports must describe this fetched universe rather than claim to rank every Orca pool. Manual pool inclusion is dry-run-only; explicit exclusion is allowed in both modes.

Orca categories are qualified pool classification from Orca's API. They are not independent token-security verification.

## Hard Gates

Hard gates run before scoring. Every live pool must have complete finite evidence, no Orca warning, valid token metadata and tick spacing, and canonical USDC as token B:

```text
token_b.mint == EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
token_b.symbol.upper() == USDC
token_b.decimals == 6
token_a.mint != token_b.mint
price_orientation == token_b_per_token_a
```

USDC-as-token-A pools are out of scope and must not be inverted. Profile policy is fixed:

| Profile | Categories | Min TVL | Min 24h / 7d volume | Max abs 24h net change | Min 24h and dailyized 7d fee productivity |
| --- | --- | ---: | ---: | ---: | ---: |
| `yield_focused` | stablecoin, liquid_staking_token, utility, governance | $300k | $200k / $1m | 3% | 2 bp/day |
| `yield_high_risk` | stablecoin, liquid_staking_token, utility, governance, memecoin | $100k | $150k / $500k | 5% | 2 bp/day |
| `yield_extreme_risk` | stablecoin, liquid_staking_token, utility, governance, memecoin | $100k | $150k / $500k | 50% | 2 bp/day |
| `yield_no_limit` | stablecoin, liquid_staking_token, utility, governance, memecoin | $100k | $150k / $500k | none | 2 bp/day |

The live profiles form a monotonic eligibility ladder: every pool eligible for a lower-risk profile remains eligible for each higher-risk profile before ranking. `yield_high_risk` adds memecoin discovery and relaxes liquidity, volume, and volatility gates. Extreme and no-limit preserve that broad universe while progressively relaxing volatility and range constraints. All retain the common 2 bp/day sustainable-fee floor.

Calculate `fee_tvl_24h = fees_24h / current_tvl`, `fee_tvl_7d_daily = fees_7d / 7 / current_tvl`, and `sustained_fee_productivity = min(fee_tvl_24h, fee_tvl_7d_daily)`. Both windows must independently pass the profile minimum. Short-window productivity and momentum are diagnostics only. A weighted score can never rescue a failed gate, and there is no score cutoff after all gates pass.

## MCDA Scoring

Score gate-passing pools from 0 to 5 using 40% sustained fee productivity, 25% recent activity, 15% 24h net-price-change stability, 10% liquidity depth, and 10% execution simplicity. For fee productivity, volume, and TVL, score profile-relative multiples by linearly interpolating `1x=1`, `2x=2`, `5x=3`, `10x=4`, and `20x=5`, clamped to 0 through 5.

Recent activity is `0.6 * 24h volume score + 0.4 * 7d volume score`. Stability is `5 * (1 - min(abs(net change), reference) / reference)`, with references 3%, 5%, 50%, and 50% for the four live profiles in order. Fixed-fee execution simplicity is 5; adaptive-fee is 4. Rank by weighted score descending, sustained fee productivity descending, recent activity descending, absolute 24h net price change ascending, TVL descending, then pool address ascending. If a ranked pool has no feasible range, record why and continue to the next eligible pool.

## Consult-Mode Pool Discovery

When consulted for Orca pool discovery, run `orca_pool_scan` in analysis-only posture. Do not consult another pool-watcher agent for Orca discovery.

Use routine config that matches the user's intent:

- `execution_mode: dry_run`;
- `risk_profile: meme_scout` for meme-pool screening;
- `risk_profile: category_scout` for broad category exploration;
- `risk_profile: yield_focused` for production-policy fee screening without memecoins;
- `risk_profile: yield_high_risk`, `yield_extreme_risk`, or `yield_no_limit` only when the user explicitly requests that named policy in dry-run analysis;
- `stats_windows: "1h,4h,24h,7d,30d"` only with an analysis-only scout when the user asks for monthly data; monthly means rolling 30d, not a calendar month;
- `include_pool_addresses` only for explicit dry-run inspection and `exclude_pool_addresses` only for explicit exclusions.

Do not pass category, quote, profile-threshold, scoring-weight, range-bound, or preset overrides. Named profiles own those policies.

Preferred consult-mode profile mapping:

- meme pools, meme coins, or highest meme fees -> `risk_profile: meme_scout`;
- stablecoin, liquid-staking, or fee-focused non-meme pools -> `risk_profile: yield_focused`;
- broad utility, governance, liquid-staking-token, or security exploration -> `risk_profile: category_scout`;
- explicitly requested broader risk tolerance -> the matching named live profile in dry-run mode.

Consult-mode discovery stops at routine-backed analysis. It must not create executors, perform swaps, open LP positions, or imply that a candidate is live-actionable without Gateway, balance, executor, and risk-limit preflight.

Report only fields present in routine output, such as:

- pool/pair;
- pool address;
- TVL and raw rolling volume and fees;
- 24h and dailyized 7d fee productivity plus sustained fee productivity;
- 1h/4h dailyized productivity and momentum when available, clearly labeled diagnostic;
- 24h net price change, fee rate, adaptive-fee state, fee-tier index, and tick spacing;
- source categories and discovery lenses returned as evidence;
- hard gates, warnings, rejection reasons, criteria scores, and weighted score;
- provisional range plan and preset suggestion, never executable bounds;
- reason for inclusion or exclusion.

## Named Presets And Range Policy

Calculate `tick_spacing_floor = 1.0001 ** (tick_spacing * 2) - 1`, `change_based_half_width = abs(net_price_change_24h) * 1.5`, and `required_half_width` as their maximum. The floor is a two-tick-interval-per-side heuristic, not tick snapping; Gateway performs authoritative price-to-initializable-tick conversion.

- `concentrated`: required width at most 1.5% and sustained fees at least 5 bp/day; clamp to 0.5%-1.5%.
- `balanced`: required width at most 3%; clamp to 1%-3%.
- `defensive`: required width at most 8% and sustained fees at least 4 bp/day; clamp to 2%-8%.
- `extreme`: only `yield_extreme_risk` and `yield_no_limit`; clamp to 8%-95%. Extreme-risk rejects requirements above 95%. No-limit records the uncapped requirement and sets `width_capped: true` when clamping above 95%.
- `no-trade`: mandatory when gates, range feasibility, preflight, or risk checks fail.

Choose the narrowest valid preset. Do not invent a custom range, override policy bounds, or widen the selected half-width. The scanner candidate contains a provisional `range_plan` with status, safety factor, tick-spacing floor, change-derived half-width, uncapped required half-width, maximum executable half-width, `width_capped`, preset, and provisional half-width. It must contain no `lower_price`, `upper_price`, `lower_limit_price`, or `upper_limit_price`.

## LP Executor Rails

The executor is the hands of the strategy. The agent selects policy-level knobs; the LP executor opens, monitors, and closes the on-chain CLMM position.

The strategy uses one `lp_executor` with:

- `connector_name: solana-mainnet-beta` unless runtime config overrides;
- `lp_provider: orca/clmm`;
- `side: 3` for double-sided range LP;
- `pool_address` from the selected candidate;
- `lower_price` and `upper_price` recentered by preflight on the fresh Gateway price;
- `lower_limit_price` and `upper_limit_price` outside the LP range;
- `keep_position: false` so post-close handling targets clean quote inventory;
- `controller_id` supplied as Condor requires.

If a centered range lacks base inventory but has enough quote inventory and SOL reserve, `pre_lp_rebalance` prepares only the base shortfall. It is quote-only in `dry_run` and automatically submits one Jupiter swap in explicit `loop` mode. Live preflight registers and verifies the exact selected pool tokens before checking inventory, so refreshed balances include newly acquired assets. Rebalance persists submission intent before execution, never retries an uncertain submission, and polls confirmed settlement for refreshed balances before preflight can pass.

Scanner evidence is fresh only from 30 seconds in the future through 600 seconds old. Preflight revalidates candidate identity, structured profile, source evidence and arithmetic, then checks fresh Gateway pool identity, canonical token orientation, scanner-to-Gateway deviation, executor state, wallet state, and risk limits. It constructs `lower_price = gateway_price * (1 - provisional_half_width)` and `upper_price = gateway_price * (1 + provisional_half_width)`. It then uses `limit_buffer = min(1.5%, max(0.3%, provisional_half_width * 0.5))`, `lower_limit_price = lower_price * (1 - limit_buffer)`, and `upper_limit_price = upper_price * (1 + limit_buffer)`. After a confirmed swap, refresh Gateway price and wallet balances and rerun preflight while the original candidate remains fresh. If freshness, deviation, or inventory fails, stop for manual review; never submit a second swap, select a second market, or create from stale evidence.

The active session's `total_amount_quote` is the canonical LP budget. Its default is 10 quote units, but a user-selected value replaces that default throughout scanning, rebalance sizing, preflight, and executor creation. The agent must also preserve the SOL fee buffer and respect the one-executor limit.

## Exits And Session Limits

The active LP position is supervised by `lp_position_report`.
The routine fetches executor and LP position facts from the Hummingbot API; its inputs should be limited to exit-policy knobs such as max age, take-profit, stop-loss, out-of-range grace, and missing-position grace.
`POSITION_MAX_AGE_MINUTES`, `POSITION_TAKE_PROFIT_NET_PNL_RATIO`, and `POSITION_STOP_LOSS_NET_PNL_RATIO` from the current session input are canonical for each position and reset when that position is audited and archived.

`SESSION_MAX_AGE_MINUTES`, `SESSION_TAKE_PROFIT_NET_PNL_RATIO`, and `SESSION_STOP_LOSS_NET_PNL_RATIO` apply to the controller session. Session PnL is the cumulative net PnL of audited positions plus active marked net PnL, always divided by the fixed session `total_amount_quote`; do not compound, resize the denominator, or maintain a separate local session-drawdown calculation.

Exit or escalation conditions include:

- take-profit after costs;
- stop-loss after costs;
- max position age;
- hard limit price crossing;
- soft out-of-range grace exceeded;
- failed executor state;
- repeated missing position info;
- operator stop or manual-review trigger.

`risk_limits.max_position_size_quote: 10` and `max_open_executors: 1` are hard rails. Generic `max_drawdown_pct` is disabled at `-1`. The generic hard drawdown backstop is delayed through `shutdown_drawdown_pct: 3`: it is measured in percentage points, bypasses normal audit work, and requests session shutdown rather than a position-level exit. It is not a local session-drawdown limit.

## Tick Lifecycle

Every tick begins with `lp_position_report`, using the dynamic controller ID from `[TICK INFO]` and explicit `execution_mode` from the current session. Dispatch only its or a subsequently invoked routine's versioned `outcome` command; do not infer follow-up work from prose or other payload fields.

| Outcome command | Immediate dispatch |
| --- | --- |
| `continue`, `wait-next-cycle`, `no-action` | End this tick. |
| `close` | In loop mode, call `manage_executors(action="stop", executor_id=<exact outcome id>, keep_position=false)` once. |
| `write-audit` | Run `lp_close_audit` with the emitted final executor evidence. |
| `resume-rebalance` / `run-rebalance` | Run `pre_lp_rebalance` with the exact persisted/emitted plan. |
| `resume-preflight` / `run-preflight` | Refresh Gateway pool evidence and run `orca_live_preflight` for the exact candidate. |
| `run-pool-scan` | Run `orca_pool_scan` only when flat with no lifecycle state. |
| `create-executor` | In loop mode, create exactly the emitted executor tool-call plan. |
| `manual-review` | End this tick without scan, swap, or create. |
| `stop-agent` | Call `manage_trading_agent(action="stop_agent", agent_id=<dynamic controller ID>)` and do nothing else. |

Critical invariants:

- Use the exact emitted plan only. `controller_id` and `total_amount_quote` stay nested in `executor_config`.
- V1 permits one executor. V2 still dispatches only one immediate command per tick so future executor opens can be staggered.
- Never call a swap tool directly; `pre_lp_rebalance` alone may submit its persisted single swap in loop mode.
- Never duplicate executor create or stop calls. Dry-run supervision never stops an executor.
- A terminal executor is audited before any later-tick re-entry. A complete audit may only return `wait-next-cycle`; an audit with `stop_pending` returns `no-action`, and the following position-report tick dispatches `stop-agent`.
- A terminal failed executor may advance only when the close audit proves it failed before open from explicit null position identity, zero base and quote fills, and inactive/non-trading state. Missing or contradictory evidence remains manual review.
- Same-tick post-rebalance preflight and exact-plan create remain allowed. No same-tick re-entry follows an audit.

Every routine writes a standard Condor report containing sanitized input, decision evidence, warnings/errors, and the full debug JSON payload.

Legacy sessions created under former profile, preset, candidate, range, or lifecycle contracts are historical evidence only. Do not resume them or add compatibility aliases, migration, version dispatch, or legacy-state handling. New looping behavior starts with a newly created session.

Pass normalized preflight evidence in this shape:

```yaml
execution_mode: dry_run | loop
controller_id: <dynamic id from TICK INFO>
selected_candidate: <exact orca_pool_scan selected_candidate>
gateway_pool_info: {pool_address, base_mint, quote_mint, current_price}
executor_lookup_succeeded: true | false
active_executors: [{controller_id, status}]
fetch_wallet_balances: true
wallet_account_name: master_account
wallet_connector_name: solana-mainnet-beta
api_errors: []
total_amount_quote: <session budget>
min_sol_fee_buffer: <required SOL reserve>
```

For rebalance, pass `execution_mode`, `controller_id`, `selected_candidate`, `gateway_pool_info`, `rebalance_plan`, `total_amount_quote`, `wallet_account_name`, and `wallet_connector_name`. `total_amount_quote` must exactly match `[CURRENT CONFIG]`. In loop resume states, the routine loads the persisted candidate and transaction identity and rejects mismatches.

For the audit, pass `controller_id`, `execution_mode: loop`, and the final executor API response as `final_executor`; the routine loads the exact persisted plan, preset, and policy close reason from the agent-local lifecycle file.

## Evidence Status

This implementation starts with dry-run readiness. No live executor should be created by the files alone. Explicit session mode, structured risk profile, Gateway health, wallet balances, and user intent are required for live tests.

## Outside Scope

The strategy does not author raw Solana transactions, depend on paid APIs, manage private credentials, optimize a secret portfolio model, or scan new pools while a position is active or closing.

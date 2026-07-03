---
name: orca
description: Cautious Orca Whirlpool CLMM LP strategy with public-data scanning, hard gates, one LP executor, and audit-first supervision.
agent_key: null
skills: []
default_config:
  execution_mode: dry_run
  risk_profile: default_cautious
  frequency_sec: 300
  total_amount_quote: 10
  agent_id: lpmaxxing_orca
  controller_id: lpmaxxing_orca
  max_open_executors: 1
  connector_name: solana-mainnet-beta
  lp_provider: orca/clmm
  side: 3
  keep_position: false
  allowed_live_presets:
  - conservative
  - balanced
  - wide
  orca_api:
    base_url: https://api.orca.so/v2/solana
    pool_endpoint: /pools
    request_timeout_seconds: 15
    page_size: 25
    stats_windows:
    - 24h
    - 7d
    - 30d
    categories: []
  gates:
    min_tvl_usd: 500000
    min_volume_24h_usd: 100000
    min_volume_7d_usd: 500000
    max_abs_price_delta_24h: 0.20
    allowed_quote_symbols:
    - USDC
    - SOL
    - mSOL
    - JitoSOL
    preferred_quote_symbols:
    - USDC
    - SOL
    reject_has_warning: true
    require_token_metadata: true
    require_gateway_pool_info: true
  risk_limits:
    max_total_exposure_quote: 10
    max_position_size_quote: 10
    max_drawdown_pct: 2
    max_open_executors: 1
  lp_risk:
    max_capital_allocation_quote: 10
    max_capital_pct_of_account: 0.25
    max_pool_tvl_share: 0.001
    max_pool_24h_volume_share: 0.001
    take_profit_pct_after_costs: 0.005
    stop_loss_pct_after_costs: 0.01
    max_tx_fee_quote: 0.25
    min_sol_fee_buffer: 0.05
    max_consecutive_api_failures: 2
    max_consecutive_gateway_failures: 1
  v2:
    enable_multi_pool: false
    max_active_pools: 1
default_trading_context: |
  # SESSION INPUT GUIDE
  SESSION_MODE options: dry_run, run_once, loop.
  SCAN_PROFILE options: safe_conservative, default_cautious, balanced_fee_capture, risk_on_volatile, category_scout, meme_scout, meme_tiny_live.
  OPTIONAL_CATEGORIES options: blank, memecoin, utility, governance, liquid_staking_token, security, stablecoin.
  MAX_POSITION_AGE_MINUTES is the time limit; 1440 = 1 day.
  TAKE_PROFIT_PCT_AFTER_COSTS and STOP_LOSS_PCT_AFTER_COSTS are percentages, e.g. 5 = 5%.
  ALLOW_PRE_LP_REBALANCE allows a Gateway swap to fund centered LP ranges.
  NOTES examples: only USDC quote pools; avoid SOL quote; avoid wide preset; inspect pool <address>.
  Use exact option names. Do not put numeric limits in NOTES.

  # SESSION INPUT
  SESSION_MODE: dry_run
  SCAN_PROFILE: safe_conservative
  OPTIONAL_CATEGORIES:
  MAX_POSITION_AGE_MINUTES: 480
  TAKE_PROFIT_PCT_AFTER_COSTS: 0.5
  STOP_LOSS_PCT_AFTER_COSTS: 1
  ALLOW_PRE_LP_REBALANCE: true
  NOTES:
created_by: 0
created_at: '2026-06-15T00:00:00Z'
---

# Orca LP Agent Strategy

Orca LP Agent is a Condor trading agent for the Orca hackathon track. V1 demonstrates transparent, cautious liquidity provision on a single Orca Whirlpool CLMM pool. It is not a hidden-alpha system; it is a public-data agent that shows how Condor can select, open, supervise, close, and audit an Orca LP position.

## What It Does

The agent scans public Orca pool data using an explicit session scan profile and optional Orca category filters, rejects unsuitable pools with hard gates, scores the remaining pools with a simple MCDA model, chooses one named preset, and then uses the Hummingbot LP executor path to manage one Orca CLMM position.

Default execution mode is `dry_run`. Live execution requires the session context to explicitly set `SESSION_MODE: run_once` or `SESSION_MODE: loop` and a valid `SCAN_PROFILE`.

## Public Data

The selection path uses Orca public pool data only:

- pool address;
- token symbols, mints, and decimals;
- current price;
- TVL;
- 24h, 7d, and rolling 30d volume;
- 24h, 7d, and rolling 30d fees;
- yield-over-TVL where available;
- 24h price delta;
- warning flags, fee tier, and tick spacing when exposed.

The live path must additionally verify the selected pool through Gateway and portfolio/balance checks before opening.

Scan profiles can narrow discovery by Orca category and adjust TVL, volume, volatility, and scoring gates. The chosen scan profile is the TVL hard floor; do not apply a second live TVL floor from default config or model judgment after a scan succeeds. `safe_conservative` avoids memecoin focus, `balanced_fee_capture` targets non-meme fee pools, `risk_on_volatile` allows memecoin exposure, `category_scout` is a looser consult/dry-run discovery profile for utility, governance, liquid-staking-token, and security categories, and `meme_scout` / `meme_tiny_live` default to memecoin category scans. Manual `include_pool_addresses` should be used only for debug or forced inspection of known pools.

## Hard Gates

Hard gates run before scoring. A weighted score can never rescue a pool that fails a gate.

V1 always rejects malformed records, missing pool addresses, missing token metadata, Orca API warning pools, profile-low TVL, low 24h or 7d volume, disallowed quote assets, missing required price data, extreme 24h price movement, explicit exclude-list pools, and any pool that cannot pass Gateway preflight before live execution. Routine notes like missing Gateway preflight or high fee/TVL productivity are cautions unless a selected-pool gate or live preflight fails.

Allowed quote symbols are configurable and are not limited to SOL-USDC. The default major quote set is `USDC`, `SOL`, `mSOL`, and `JitoSOL`, with `USDC` and `SOL` preferred for simpler accounting.

## MCDA Scoring

Surviving pools are scored from 0 to 5 across six criteria:

- liquidity depth;
- recent activity;
- fee productivity;
- range stability;
- execution simplicity;
- Orca sponsor fit.

The default weights sum to 1.0: 25% liquidity depth, 20% recent activity, 20% fee productivity, 15% range stability, 10% execution simplicity, and 10% sponsor fit.

The scoring model is intentionally simple and reviewable. It is designed to produce an auditable choice, not to claim proprietary prediction power.

## Consult-Mode Pool Discovery

When consulted for Orca pool discovery, run `orca_pool_scan` in analysis-only posture. Do not consult another pool-watcher agent for Orca discovery.

Use routine config that matches the user's intent:

- `execution_mode: dry_run`;
- `risk_profile: meme_scout` for meme-pool screening;
- `risk_profile: safe_conservative` or `default_cautious` for conservative broad screening;
- `stats_windows: "24h,7d,30d"` when the user asks for monthly data; monthly means rolling 30d, not a calendar month;
- `scan_sort_fields` should use Orca-style names such as `fees30d`, `volume30d`, `fees7d`, `volume7d`, `fees24h`, and `volume24h`;
- explicit category, quote, or exclude filters only when the user asks for them; category filters must use exact valid category values.

Valid Orca category values are exact strings only:

- `memecoin`;
- `utility`;
- `governance`;
- `liquid_staking_token`;
- `security`;
- `stablecoin`.

Do not invent aliases. Use `memecoin`, not `meme`; use `liquid_staking_token`, not `lst`.

Preferred consult-mode profile mapping:

- meme pools, meme coins, or highest meme fees -> `risk_profile: meme_scout`; do not also pass `categories` unless the user explicitly asks for a category override;
- tiny-live meme dry analysis -> `risk_profile: meme_tiny_live`;
- stablecoin or liquid-staking conservative pools -> `risk_profile: safe_conservative`;
- broad conservative Orca screening -> `risk_profile: default_cautious`;
- fee-focused non-meme pools -> `risk_profile: balanced_fee_capture`;
- early category discovery for utility, governance, liquid-staking-token, or security pools -> `risk_profile: category_scout`;
- higher-volatility fee capture across utility, governance, and memecoin pools -> `risk_profile: risk_on_volatile`.

Consult-mode discovery stops at routine-backed analysis. It must not create executors, perform swaps, open LP positions, or imply that a candidate is live-actionable without Gateway, balance, executor, and risk-limit preflight.

Report only fields present in routine output, such as:

- pool/pair;
- pool address;
- TVL/liquidity;
- 24h volume;
- 7d and rolling 30d volume if returned;
- 24h fees or fee proxy if returned;
- 7d and rolling 30d fees if returned;
- fee APR/APY proxy if returned;
- warnings, hard gates, and rejection reasons;
- preset suggestion;
- reason for inclusion or exclusion.

## Named Presets

The routine may recommend only four outcomes: `no-trade`, `conservative`, `balanced`, or `wide`.

- `conservative` uses wider safety posture for lower-confidence candidates.
- `balanced` is used when liquidity, activity, fees, and volatility are all acceptable.
- `wide` is available in live mode within risk caps when volatility is high but still allowed and fee/activity evidence is strong.
- `no-trade` is mandatory when gates, confidence, Gateway preflight, or risk checks fail.

The agent can downgrade only for a concrete failed gate, preflight failure, missing required data, or explicit session constraint. It should not invent custom presets, free-form ranges, or extra live-entry thresholds.

## LP Executor Rails

The executor is the hands of the strategy. The agent selects policy-level knobs; the LP executor opens, monitors, and closes the on-chain CLMM position.

V1 uses one `lp_executor` with:

- `connector_name: solana-mainnet-beta` unless runtime config overrides;
- `lp_provider: orca/clmm`;
- `side: 3` for double-sided range LP;
- `pool_address` from the selected candidate;
- `lower_price` and `upper_price` from the preset range;
- `lower_limit_price` and `upper_limit_price` outside the LP range;
- `keep_position: false` so post-close handling targets clean quote inventory;
- `controller_id` supplied as Condor requires.

If the centered LP range needs base inventory and the wallet only has quote, the agent may run the agent-local `pre_lp_rebalance` routine through `manage_routines` to perform one Gateway Jupiter quote-to-base swap before creating the executor, but only when `ALLOW_PRE_LP_REBALANCE: true`, the swap quote succeeds, the swap confirms, the SOL fee buffer remains intact, and the total LP spend stays inside the session budget and pool-share caps.

The tiny live-test budget is capped at 10 quote units by default. The agent must also respect pool TVL share, pool volume share, account-cap, fee-buffer, and max-open-executor limits.

## Exits

The active LP position is supervised by `lp_position_report`.
The routine fetches executor and LP position facts from the Hummingbot API; its inputs should be limited to exit-policy knobs such as max age, take-profit, stop-loss, out-of-range grace, and missing-position grace.

Exit or escalation conditions include:

- take-profit after costs;
- stop-loss after costs;
- max position age;
- hard limit price crossing;
- soft out-of-range grace exceeded;
- failed executor state;
- repeated missing position info;
- operator stop or manual-review trigger.

After close or failure, the agent writes a post-close audit before considering a new position.

## V1 And V2

V1 is deliberately single-pool. It keeps behavior easy to inspect and prevents hidden portfolio complexity during early validation.

V2 is feature-flagged with `v2.enable_multi_pool: false` by default. When enabled after V1 validation, V2 should reuse the same scan, gates, scoring, preflight, executor rails, and audit pattern, but add a capped basket allocation layer across two or three pools. It must keep one executor per selected pool and enforce aggregate budget, token concentration, drawdown, and executor-count limits.

## Evidence Status

This V1 implementation starts with dry-run readiness. No live executor should be created by the files alone. Explicit session mode, scan profile, Gateway health, wallet balances, and user intent are required for live tests.

## Outside Scope

V1 does not author raw Solana transactions, depend on paid APIs, manage private credentials, optimize a secret portfolio model, or continue scanning new pools while a position is already active.

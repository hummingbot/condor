# Orca LP Agent Strategy

Orca LP Agent is a Condor trading agent for the Orca hackathon track. V1 demonstrates transparent, cautious liquidity provision on a single Orca Whirlpool CLMM pool. It is not a hidden-alpha system; it is a public-data agent that shows how Condor can select, open, supervise, close, and audit an Orca LP position.

## What It Does

The agent scans public Orca pool data using configured risk profiles and Orca category filters, rejects unsuitable pools with hard gates, scores the remaining pools with a simple MCDA model, chooses one named preset, and then uses the Hummingbot LP executor path to manage one Orca CLMM position.

Default execution mode is `dry_run`. Live execution requires Condor runtime config to explicitly set `execution_mode: run_once` or `execution_mode: loop`.

## Public Data

The selection path uses Orca public pool data only:

- pool address;
- token symbols, mints, and decimals;
- current price;
- TVL;
- 24h and 7d volume;
- 24h and 7d fees;
- yield-over-TVL where available;
- 24h price delta;
- warning flags, fee tier, and tick spacing when exposed.

The live path must additionally verify the selected pool through Gateway and portfolio/balance checks before opening.

Risk profiles can narrow discovery by Orca category. `meme_scout` and `meme_tiny_live` default to `memecoin` category scans, while explicit empty categories scan all pools. Manual `include_pool_addresses` should be used only for debug or forced inspection of known pools.

## Hard Gates

Hard gates run before scoring. A weighted score can never rescue a pool that fails a gate.

V1 rejects malformed records, missing pool addresses, missing token metadata, Orca warning pools, low TVL, low 24h or 7d volume, disallowed quote assets, missing required price data, extreme 24h price movement, explicit exclude-list pools, and any pool that cannot pass Gateway preflight before live execution.

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

## Named Presets

The routine may recommend only four outcomes: `no-trade`, `conservative`, `balanced`, or `wide`.

- `conservative` uses wider safety posture for lower-confidence candidates.
- `balanced` is used when liquidity, activity, fees, and volatility are all acceptable.
- `wide` is available in live mode within risk caps when volatility is high but still allowed and fee/activity evidence is strong.
- `no-trade` is mandatory when gates, confidence, Gateway preflight, or risk checks fail.

The agent can downgrade risk, but it should not invent custom presets or free-form ranges.

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

The tiny live-test budget is capped at 10 quote units by default. The agent must also respect pool TVL share, pool volume share, account-cap, fee-buffer, and max-open-executor limits.

## Exits

The active LP position is supervised by `lp_position_report`.

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

This V1 implementation starts with dry-run readiness. No live executor should be created by the files alone. Condor runtime configuration, Gateway health, wallet balances, and user intent are required for live tests.

## Outside Scope

V1 does not author raw Solana transactions, depend on paid APIs, manage private credentials, optimize a secret portfolio model, or continue scanning new pools while a position is already active.

---
name: orca_pool_selection
description: Interpret the neutral Orca MCDA shortlist, verify live Whirlpool facts, and choose a pool and range or HOLD without turning rank into a command.
when_to_use: Read when two or more valid Orca candidates are close, source coverage is degraded, native facts contradict the neutral rank, or a rank override or difficult range judgment needs explicit reasoning.
references_routine: scan_orca_pools
source: agent:lp_agent_lite
---

# Orca Pool Selection

Use this playbook after `scan_orca_pools` returns current compact evidence. It
guides judgment; it does not repair missing identity, incomplete required facts,
a technical rejection, or a registry conflict.

Use the returned scan schema and coverage for selection. Its best-effort Condor
report is a human diagnostic copy of sanitized input, output, and trace;
`report_id` and `report_error` do not affect rank, coverage, candidate validity,
or permission to deploy, and report failure never justifies another scan.

Before comparison, parse only exact injected execution-learning records with
`BLACKLIST_POOL=<pool>` and `BLACKLIST_TOKEN=<base_mint>`. Exclude the exact
pool and every candidate with that exact BASE mint before selection. Ignore
malformed records and all general learning prose. A blacklist is persistent
exclusion evidence only; it supplies no price, quality, ownership, or mutation
authority.

## Establish Evidence Quality

1. Confirm the scan used current `min_pool_tvl_usd`, `candidate_scan_limit`, and
   the exact five configured `mcda_weights`.
2. Require exact pool address; an exact quote-side match to configured symbol,
   mint, and decimals; exact base mint and decimals; finite positive
   price/TVL/tick spacing; fee metadata; consistent duplicate identity; and
   explicit no-warning evidence.
3. Interpret source coverage honestly:
   - all four discovery lenses: complete source coverage for the four bounded
     requests, not the whole Orca universe;
   - two or three: degraded comparison evidence, not a fabricated complete set;
   - fewer than two: unavailable for deployment selection.
4. A transport-bounded candidate prefix is complete per returned row but not the
   entire eligible universe. Do not invent facts for omitted candidates.

The four discovery lenses are 24-hour fee yield/TVL, 7-day fee yield/TVL,
24-hour volume, and 7-day volume. The normalized evidence covers
1h/4h/24h/7d periods. An absent Gateway token is later registration work, not a
pool-quality penalty; conflicting token metadata is a hard candidate block.

Read the compact candidate schema exactly:

- `base=[symbol,mint,decimals]` and `pool` is the Whirlpool address;
- `volume_usd`, `fees_usd`, and `price_change` follow returned
  `windows=["1h","4h","24h","7d"]` in that order;
- `mcda` follows `[fee_productivity,recent_activity,price_stability,
  liquidity_depth,execution_simplicity]` in configured-weight order;
- `fee_meta=[fee_rate,fee_tier,adaptive]`; do not invent units for a raw fee
  field, and verify current fee mechanics natively;
- `returned` counts included rows and `omitted` counts eligible rows excluded by
  the response bound. Neither count says the four API requests covered all pools.

## Read The MCDA Components

- Fee productivity: prefer repeatable fee generation relative to supplied
  liquidity, not one isolated spike.
- Recent activity: distinguish persistent flow from fading or artificial volume.
- Price stability: favor fee opportunity that is plausible after adverse
  selection and impermanent-loss risk; stability is not a demand for zero
  volatility.
- Liquidity depth: use TVL as execution and resilience evidence while respecting
  the absolute configured floor.
- Execution simplicity: the neutral component combines tick-spacing/adaptive-fee
  mechanics with fee persistence. Wallet inventory, token registration, schema
  support, and range feasibility are separate later admission checks.

The configured weights and routine math own the neutral score. Do not rescore in
prose, change weights from session language, or treat rank 1 as an automatic
trade.

## Verify And Compare

For the bounded shortlist, use
`explore_dex_pools(action="get_pool_info", connector="orca",
network=<config.network>, pool_address=<exact pool>)` to confirm current
price, liquidity, fee, token identity, and tick mechanics. Use bounded
GeckoTerminal `pool_detail`, `multi_pools`, `token_info`, or `ohlcv` only when a
close score, contradiction, token concern, or range choice needs it. Its network
argument is the tool-specific `solana` key after `<config.network>` has been
validated as the supported Solana mainnet network; do not pass a Gateway network
identifier to GeckoTerminal.

Compare candidates on:

- sustainability of fees and volume across horizons;
- current native facts versus the official scan;
- price direction, volatility, and whether a useful range is plausible;
- depth and tick spacing relative to the intended small position;
- token identity and current registry feasibility;
- overlap with existing wallet-wide LP exposure;
- source coverage and the cost of being wrong.

Prefer a lower-ranked candidate only when specific current evidence supports a
more sustainable fee opportunity, safer executable range, or materially better
portfolio fit. State the facts supporting the override. Reject only the affected
candidate; one unsuitable pool does not force a global `HOLD` when another is
fully eligible.

## Choose Range Intent

Orca uses ticks and tick spacing. Select a strategic half-width within current
config bounds using verified price, recent realized movement, fee activity, and
the trade-off between concentration and time in range. The deterministic sizing
routine aligns prices and calculates token composition; do not estimate raw
token amounts or tick arithmetic here.

- Narrower ranges concentrate fee earning but exit range sooner and create more
  inventory conversion risk.
- Wider ranges remain active longer but dilute fee concentration.
- In a strong one-directional move, widen defensively, choose another candidate,
  or `HOLD`; high volume alone does not compensate automatically.
- Existing same-base or correlated LP exposure is a reason to prefer
  diversification, not permission to exceed any cap.

Return one selected exact pool plus a range thesis and allocation within config,
or `HOLD`. Registration, exact feasible-size math, preparation, execution, and
reconciliation remain with the Strategy.

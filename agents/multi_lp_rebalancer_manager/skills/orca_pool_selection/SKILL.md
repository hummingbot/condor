---
name: orca_pool_selection
description: Interpret the deterministic Orca shortlist for diversified risk-profile and token-focus selection without treating rank as a command.
when_to_use: Read only during current selection when candidates are close, discovery coverage is degraded, native facts conflict, token-focus judgment is unusual, or a rank override needs justification. Do not read for ordinary supervision or a clear eligible selection.
references_routine: scan_orca_pools
source: agent:multi_lp_rebalancer_manager
---

# Orca Pool Selection

Use only before a pool is committed. The scan routine supplies normalized identity,
rank, MCDA components, trend signal, and range math; this playbook guides the LLM's
bounded portfolio judgment. It never repairs missing evidence or authorizes mutation.

## Routine Guide: `scan_orca_pools`

Call exactly `manage_routines(action="run", name="scan_orca_pools",
agent="multi_lp_rebalancer_manager", config={...})`. Config is an exact API.

### Top-Level Config Parameters

<!-- routine-config:scan_orca_pools -->
| Config key | Presence | Exact source and use |
|---|---|---|
| `quote_token_mint` | required | Current canonical USDC mint. |
| `min_pool_tvl_usd` | required | Positive current TVL floor. |
| `candidate_scan_limit` | required | Requested shortlist from five through thirty; transport returns at least five when that many eligible rows exist. |
| `mcda_weights` | required | Exact five-weight object below. |
| `risk_profile` | required | `conservative`, `balanced`, or `high_yield`. |
| `trend_thresholds_pct` | required | Exact three threshold values below. |
| `trend_history_min_points` | required | Minimum valid positive history points; at least seven. |
| `range_profiles` | required | Exact three profile objects below. |
| `downtrend_width_multiplier` | required | Configured positive DOWN-range multiplier. |
| `downside_offset_width_ratio` | required | Configured zero-to-one downside offset ratio. |
| `rebalance_threshold_width_ratio` | required | Configured positive width-to-threshold ratio. |
| `minimum_rebalance_threshold_pct` | required | Positive configured lower clamp. |
| `maximum_rebalance_threshold_pct` | required | Positive configured upper clamp. |
| `excluded_base_mints` | optional | Exact current-session/config mint exclusions. |
| `excluded_pool_addresses` | optional | Exact current-session/config pool exclusions. |
| `required_pool_addresses` | optional | Exact current waiting-controller pools that must be returned if valid. |
| `request_size` | optional | Routine transport bound; normally omit. |
| `timeout_seconds` | optional | Routine request timeout; normally omit. |
<!-- /routine-config -->

### Nested `mcda_weights` Parameters

<!-- routine-config:scan_orca_pools.mcda_weights -->
| Config key | Presence | Exact meaning |
|---|---|---|
| `fee_productivity` | required | Repeatable fee generation. |
| `recent_activity` | required | Recent flow and acceleration. |
| `price_stability` | required | Lower adverse movement. |
| `liquidity_depth` | required | TVL resilience. |
| `execution_simplicity` | required | Tick/adaptive-fee mechanics. |
<!-- /routine-config -->

### Nested `trend_thresholds_pct` Parameters

<!-- routine-config:scan_orca_pools.trend_thresholds_pct -->
| Config key | Presence | Exact meaning |
|---|---|---|
| `change_24h` | required | Neutral band in percentage points for official 24-hour change. |
| `recent_history` | required | Neutral band for third-last to latest history return. |
| `seven_day_history` | required | Neutral band for first to latest history return. |
<!-- /routine-config -->

### Nested `range_profiles.<profile>` Parameters

<!-- routine-config:scan_orca_pools.range_profile -->
| Config key | Presence | Exact meaning |
|---|---|---|
| `movement_multiplier` | required | Movement-to-width multiplier. |
| `min_width_pct` | required | Minimum total range width percentage. |
| `max_width_pct` | required | Maximum total range width percentage. |
<!-- /routine-config -->

`range_profiles` must contain exactly `conservative`, `balanced`, and `high_yield`,
each using that item schema.

Parse the inner JSON, which is always shorter than 1,900 characters. Schema v3 uses
`format=compact_rows_v1`; decode every candidate row in this exact order:

`[rank, pool_address, base_symbol, base_mint, base_decimals, tvl_usd, score,
price_stability, liquidity_depth, execution_simplicity, trend, trend_signal_id,
range_side, position_width_pct, downside_offset_pct, rebalance_threshold_pct]`.

The top-level `observed_at` applies to every row. Require the exact row length; never infer
a missing tail or partially decode a row. Required pool rows come first, then low ranks
that add distinct base mints before duplicate-base alternatives; every row retains its
explicit rank. The routine returns at least five complete candidates when at least five
eligible requested rows exist, or all available rows otherwise. `omitted>0` is acceptable
and is not permission to invent omitted candidates. `complete` is four discovery lenses;
`degraded` is two or three and needs caution; `unavailable` cannot select. Require
`mutation=false`.
Coverage reports completed, failed, and missing-required counts. In a targeted scan,
compare the exact `required_pool_addresses` input with returned pool addresses to identify
any missing required pool; never guess its identity from the count.
The first iteration accepts only Orca pools with BASE as pool token A and USDC as token
B. This matches LP Executor/Gateway amount and price orientation; USDC-token-A pools are
rejected rather than silently inverted. Returned price is always USDC per BASE.
Missing/malformed/too-short history preserves other pool facts but emits `trend=UNKNOWN`
and null range fields; that candidate is
not deployable or rearmable.

## Portfolio Comparison

Apply hard exclusions and no-duplicate active/unresolved pool/base first. Then compare:

- `conservative`: prioritize stability, depth, execution simplicity, and sustained fees;
- `balanced`: require credible yield plus reasonable stability/depth;
- `high_yield`: accept more movement or token risk only with current activity, execution
  feasibility, and no hard-policy violation;
- `deep_liquidity`: favor established deep pools;
- `mixed`: diversify quality and opportunity without forcing a token category;
- `memecoin`: favor eligible speculative tokens while respecting all hard evidence;
- `custom`: follow explicit current operator focus; absent/unclear preference means HOLD.

Preferred mints are a tie-breaker, never admission bypass. Compare more than one eligible
candidate when at least two exist; one suitable candidate is valid underfilled occupancy.
A lower rank is valid only with specific current sustainability or portfolio
fit evidence. Use native Orca pool detail only to resolve a concrete conflict or selected
candidate's current mechanics; never use GeckoTerminal or another fallback.

For selected pools, copy exact scanner identities/components/trend/range into allocation
and committed deployment. One or two suitable distinct pools are valid even when target
occupancy is three. Do not rescore MCDA, recompute trends, or estimate range math.

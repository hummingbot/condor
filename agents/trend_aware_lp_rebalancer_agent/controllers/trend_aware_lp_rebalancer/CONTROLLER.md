---
type: generic
description: Quote-funded multi-pool Orca LP session with trend-aware ranges, isolated inventory, and controller-owned exit and cleanup.
---

# Trend-aware LP rebalancer

`TrendAwareLPRebalancer` and `TrendAwareLPRebalancerConfig` implement
`generic/trend_aware_lp_rebalancer`. This single Python file is the source of truth;
Hummingbot API holds a synchronized copy. It requires Hummingbot's LP and order
Executors, Solana connector, Orca CLMM and Jupiter router support. Copying the source
does not install or upgrade those execution dependencies.

## Responsibility and lifecycle

The Agent selects pools and future range formations. The controller serializes
execution, prepares each allocation from canonical USDC, opens LPs, handles range
breaches and rearm, and owns close, attributable inventory cleanup and triple barriers.
Executor history establishes attribution; wallet balances are readiness evidence.
The Agent must not duplicate the controller's execution or PnL decisions.

The controller exports `custom_info` with `schema_version: 3`. The loop requires exact
controller, pool, position, Executor and runtime identities. A top-level `EXITED` is
insufficient: every configured position must be `EXITED` with ownership `ABSENT`
before terminal archive. API archive confirmation is a separate step.

## Configuration contract

The complete session config and fixed defaults are specified in `loops/orca/loop.md`.
`loops/orca/config.example.yml` configures the Agent loop, not this controller.
Each deployment gets a unique session-generated config; there are no static styles.

| Fields | Meaning and constraints |
|---|---|
| `id`, `controller_type`, `controller_name` | Unique generation; `generic`; `trend_aware_lp_rebalancer`. |
| `connector_name`, `lp_provider`, `swap_provider` | `solana-mainnet-beta`, `orca/clmm`, `jupiter/router`. |
| `quote_token_mint`, `total_amount_quote` | Canonical Solana USDC and a positive session budget. |
| `lp_positions` | Nonempty list of unique position IDs and pools; allocation percentages sum exactly to 100. |
| Position identity | `position_id`, `trading_pair`, `pool_address`, `base_token_mint`, `allocation_pct`; preserve after deployment. |
| Position formation | `market_trend` (`UP`, `SIDEWAYS`, `DOWN`), `position_width_pct`, `downside_offset_pct`, `rebalance_threshold_pct`; updates configure the next formation, not the active LP. |
| `lp_sizing_buffer_pct` | Default 2; at least 2 and below 10. |
| `min_sol_reserve` | Nonnegative SOL reserve; provided by the loop config. |
| `cleanup_min_quote_value` | Default 0.01 USDC. |
| `rebalance_cooldown_minutes` | Default 5; 0–1440. |
| `max_consecutive_controller_failures` | Default 3; 1–10. |
| `failure_retry_backoff_seconds` | Default 30; 0–3600. |
| `controller_take_profit_ratio`, `controller_stop_loss_ratio` | Positive ratios at most 1; provided by the loop config. |
| `controller_time_limit_minutes` | Positive integer at most 525600; provided by the loop config. |
| `controller_pnl_grace_period_minutes` | Default 5; strictly below the session time limit. |
| `exit_requested`, `exit_reason` | Initially false/`none`; operator early exit uses true/`operator`. |
| `candles_config`, `initial_positions`, `manual_kill_switch` | Loop sends `[]`, `[]`, false; initial inventory adoption is unsupported. |

## Source maintenance

Use Condor's `controller_sources` playbook and `manage_agent_controllers` to compare
and synchronize this file with the selected API server. Resolve missing source or
drift during maintenance before launching a new bot. A differing server copy requires
the normal diff/impact preview and authorization; never overwrite blindly.

The trading loop only reads source/status. A successful sync changes the server copy,
not the class already loaded by a running bot. New bots use the new server source;
existing bots must finish their controller-owned lifecycle before a planned redeploy.
Controller behavior tests remain in Hummingbot API.

## Failure handling

Missing balances, prices, token registration, or exact ownership evidence can block
readiness. Stale or malformed telemetry cannot establish lifecycle progress. Ambiguous
mutations require reconciliation, not resubmission. Controller faults and unresolved
cleanup require the loop's quarantine/exit handling; stopping Condor ticks or applying
a generic shutdown policy does not establish LP closure or cleanup.

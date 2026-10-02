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

The complete session config and fixed defaults are specified in
[`loops/orca/loop.md`](../../loops/orca/loop.md).
[`loops/orca/config.example.yml`](../../loops/orca/config.example.yml) matches the
loop's `default_config` and configures the Agent loop. Each deployment gets a unique
session-generated controller config; there are no static styles.

The Agent copies these values from the current loop config into each deployment.
The loop example and controller model use the same defaults:

| Loop config | Controller config | Default |
|---|---|---|
| `min_sol_reserve` | `min_sol_reserve` | 0.1 SOL |
| `take_profit_ratio` | `controller_take_profit_ratio` | 0.05 (5%) |
| `stop_loss_ratio` | `controller_stop_loss_ratio` | 0.05 (5%) |
| `time_limit_minutes` | `controller_time_limit_minutes` | 720 minutes (12 hours) |

The current loop config supplies the session budget and overrides these defaults
when the operator sets different values. Controller ratios use fractions, while
position formation fields ending in `_pct` use percentages.

| Fields | Meaning and constraints |
|---|---|
| `id`, `controller_type`, `controller_name` | Unique generation; `generic`; `trend_aware_lp_rebalancer`. |
| `connector_name`, `lp_provider`, `swap_provider` | `solana-mainnet-beta`, `orca/clmm`, `jupiter/router`. |
| `quote_token_mint`, `total_amount_quote` | Canonical Solana USDC and a positive session budget. |
| `lp_positions` | Nonempty list of unique position IDs and pools; allocation percentages sum exactly to 100. |
| Position identity | `position_id`, `trading_pair`, `pool_address`, `base_token_mint`, `allocation_pct`; preserve after deployment. |
| Position formation | `market_trend` (`UP`, `SIDEWAYS`, `DOWN`), `position_width_pct`, `downside_offset_pct`, `rebalance_threshold_pct`; updates configure the next formation, not the active LP. |
| `lp_sizing_buffer_pct` | Default 2; at least 2 and below 10. |
| `min_sol_reserve` | Default 0.1 SOL; nonnegative; copied from the current loop config. |
| `cleanup_min_quote_value` | Default 0.01 USDC. |
| `rebalance_cooldown_minutes` | Default 5; 0–1440. |
| `max_consecutive_controller_failures` | Default 3; 1–10. |
| `failure_retry_backoff_seconds` | Default 30; 0–3600. |
| `controller_take_profit_ratio`, `controller_stop_loss_ratio` | Default 0.05 (5%) each; positive ratios at most 1; copied from the current loop config. |
| `controller_time_limit_minutes` | Default 720 minutes (12 hours); positive integer at most 525600; copied from the current loop config. |
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
Controller lifecycle tests remain in Hummingbot API. Agent-local polling-guard tests
are in `tests/test_controller_gateway_polling.py` and require a Hummingbot environment.

## Failure handling

Missing balances, prices, token registration, or exact ownership evidence can block
readiness. Stale or malformed telemetry cannot establish lifecycle progress. Ambiguous
mutations require reconciliation, not resubmission. Controller faults and unresolved
cleanup require the loop's quarantine/exit handling; stopping Condor ticks or applying
a generic shutdown policy does not establish LP closure or cleanup.

At startup, this controller installs a polling guard on its bot's Gateway connector
instance. It polls hashed orders individually and leaves market swaps with non-finite
order prices to the native realized-amount swap receipt handler. This prevents a peer
LP confirmation from completing the wrong swap and prevents synthetic NaN fills.
The connector must belong to this controller; a second controller cannot reuse its
guard. The guard stays installed during shutdown and does not edit Hummingbot files.

Missing swap receipts report `recovery.state: WAITING_FOR_SWAP_RECEIPT` and
`recovery.pending_swaps`, block new controller mutations and leave PnL unavailable.
Fresh wallet balances are readiness/reconciliation evidence, not inferred fills:
concurrent LP closes can change the same USDC balance. A native `FILLED` order with
finite positive executed base and quote amounts releases its pending receipt. After
the connector order cache expires, a completed owned Executor's exact serialized
order receipt can establish the same outcome; aggregate amounts cannot. Controller,
connector, pair, side, order ID, and known transaction hash must match.

The guard also observes native operation-failure callbacks. Structured Gateway codes
`TRANSACTION_FAILED`, `SIMULATION_FAILED`, `INSUFFICIENT_BALANCE`, `SLIPPAGE_EXCEEDED`,
and `NO_ROUTE_FOUND` release only the exact failed order with zero executed amounts
and request fresh balances. Native failure handling and retries remain unchanged.
Timeouts, unclassified errors, malformed amounts, and missing receipts stay blocked;
error-message text and wallet changes cannot establish a fill or authorize a retry.
This guard does not repair accounting already contaminated before startup.

Pending Executor creation reports `WAITING_FOR_EXECUTOR_ACK` with exact IDs in
`recovery.pending_creates`. After `failure_retry_backoff_seconds`, it reports
`EXECUTOR_CREATION_RECONCILIATION_REQUIRED`. This interval is a diagnostic grace
period: it never clears the hold or resubmits a create. A matching Executor report
clears the acknowledgment hold, including when other readiness gates are blocked.

An exhausted autonomous LP close (`hold_reason: close_retries_exhausted`) enters the
same bounded recovery path as a failed controller-requested close. Recovery checks
the exact on-chain position and ownership before submitting any close; unavailable
or conflicting evidence cannot authorize a close.

Operator and controller time-limit exits latch even while settlement is blocked.
`recovery.exit_blockers` identifies pending orders, creation acknowledgments, active
Executors, and unresolved ownership. An exit request does not bypass an uncertain
swap or establish that positions have closed.

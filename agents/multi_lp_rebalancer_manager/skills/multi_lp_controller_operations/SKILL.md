---
name: multi_lp_controller_operations
description: Exceptional operations guide for a fully identified trend-aware LP bot's defensive rearm, inherited cleanup, exit, archive, fault isolation, and portfolio wind-down.
when_to_use: Read only after fresh complete lifecycle telemetry establishes the exact bot and the tick handles WAITING_FOR_TREND_REFRESH, inherited cleanup, declared controller fault, exit request, EXITED archive, or graceful wind-down. Use bot_lifecycle_reconciliation instead for provenance ambiguity, missing bots, or conflicting/incomplete evidence.
references_routine: snapshot_trend_aware_lp_bots
source: agent:multi_lp_rebalancer_manager
---

# Multi LP Controller Operations

This is an exception playbook. Begin with one fresh raw snapshot; do not reconstruct bot
authority from an old journal, learning, wallet delta, or formatted status table.

## Routine Guide: `snapshot_trend_aware_lp_bots`

Call exactly `manage_routines(action="run",
name="snapshot_trend_aware_lp_bots", agent="multi_lp_rebalancer_manager",
config={...})`.

### Top-Level Config Parameters

<!-- routine-config:snapshot_trend_aware_lp_bots -->
| Config key | Presence | Exact source and use |
|---|---|---|
| `namespace` | required | Exact stable namespace `multi_lp_rebalancer_manager-orca`. |
| `controller_type` | required | Exact literal `generic`. |
| `controller_name` | required | Exact literal `trend_aware_lp_rebalancer`. |
| `expected_bots` | optional | Complete live fleet of at most three exact actual instance names; omit on ordinary discovery and never pass a vacant requested slot base or partial fleet. |
| `archive_check_bots` | optional | Exact current-session timestamped instances whose `stop_bot` was submitted after exact `EXITED`; omit otherwise. |
| `timeout_seconds` | optional | Bounded read timeout; normally omit. |
<!-- /routine-config -->

Parse the inner JSON and require its schema, namespace, exact compact controller rows,
and `mutation=false`. It is always shorter than 1,900 characters. Decode `compact_rows_v1`
only with the exact positional mapping in the always-loaded Strategy; never guess an
index. `summary_rows_v1`, `degraded`, missing schema version, invalid slot/config pairing, unknown lifecycle,
identity conflict, missing expected bot, or incomplete current config requires scoped
reconciliation/HOLD; it is never a vacancy.

## Defensive Rearm

For `WAITING_FOR_TREND_REFRESH`, or `BLOCKED` with exact
`readiness_state=BLOCKED_TREND` and no active Executor, target only its exact pool in a
fresh scan. Both require non-UNKNOWN, a different newly observed ID, and max-age
compliance. Defensive rearm additionally requires observation strictly after the breach
and elapsed post-cleanup cooldown; an initial stale-signal block has no breach condition. Update the complete
live config with only the runtime-updatable trend triplet and scanner-derived
`position_width_pct`, `downside_offset_pct`, and `rebalance_threshold_pct`. Do not
restart controllers or change immutable identity/capital/reserve/failure limits.

## Normal Exit

The controller alone triggers take-profit, stop-loss, and time-limit exits. Never
recompute them. If raw telemetry declares one but the exact live config has not persisted
it, mirror that same reason once for restart safety. Operator and wind-down exits request
`operator`. The only update shape is:

`manage_bots(action="update_config", bot_name=<exact timestamped instance>,
config_name=<exact config file/id>,
config_data=<complete current config with exit_requested=true and exact exit_reason>,
confirm_override=true)`.

Never use `stop_controllers`; it halts cleanup. YAML readback proves persistence while raw
`custom_info.exit` proves runtime application. Do not resubmit while either is pending;
then supervise `CLOSING`/`CLEANING` until exact `EXITED`. Every update ends its tick.

## Fault And Archive

The controller compounds its configured buffer only after an exact reconciled unlanded
LP-create failure. With the default 2%, bounded attempts size at 98%, 96.04%, then
94.1192%. Preparation failures do not alter LP sizing; uncertain opens, landed positions,
close failures, and cleanup failures never authorize this retry rule. The Agent and
controller both reject configs below 2%.

`FAULTED` with complete identity quarantines its exact controller, pool, base, inventory,
and assigned quote. Route incomplete identity/evidence to lifecycle reconciliation.

For exact `fault_reason=consecutive_failure_limit_reached`, require terminal/non-active LP
and Order Executor envelopes, no orphan position, no ownership error, completed attributable
inventory cleanup, and the complete exact live config. If its exit latch is false, the
config read may fold into this same tick: journal intent, then immediately submit the normal
`update_config` shape with `exit_requested=true`, `exit_reason=fault`, and end the tick. Do
not spend a separate observational `HOLD` tick merely because this is the first fresh fault
observation. If the latch is already persisted or pending, supervise without resubmission.

Any hard fault, active/uncertain Executor, orphan position, ownership failure, incomplete
cleanup, or incomplete config remains quarantined and receives no blind exit/retry. Never
release, resume, or retry uncertainty; continue independent sibling reads and exits.

Archive only an exact `EXITED` controller whose telemetry shows exit complete and no
active LP/Order Executor. `EXITED` already proves controller cleanup terminality;
attributed quote need not equal assigned quote and tolerated base dust is allowed.
Executor envelopes retain their latest completed record by design; verify terminal
or non-active status rather than requiring the envelope to be null. Then call one
`manage_bots(action="stop_bot", bot_name=<exact>)` and reconcile on a later tick with that
exact name in `archive_check_bots`. Release only after exact `archive_confirmed`;
active-status absence, kill switch, stopped status, or wallet balance alone never proves archival.

## Wind-Down

Persist wind-down in current-session journal. Never refill. Request one exit per tick in
priority order: unresolved/exposed or DOWN, greatest drawdown, lowest slot. Archive one
EXITED bot per tick. Faulted siblings remain explicit unresolved quarantine while healthy
siblings continue. Stop the Condor Agent only when no namespaced bot remains and every
current-session archive target is exactly confirmed `ARCHIVED`.

At fresh-session startup, every live bot lacking a complete matching current-session
deployment intent/result uses the same exit/archive mechanics but is never resumed. Block all
new admission until inherited cleanup is complete. Then continue the new Agent session;
startup cleanup alone is not permission to stop it.

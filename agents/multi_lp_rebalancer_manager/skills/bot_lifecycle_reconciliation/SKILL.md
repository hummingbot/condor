---
name: bot_lifecycle_reconciliation
description: Reconcile conflicting, stale, incomplete, or transitional Condor bot and controller lifecycle evidence without mistaking historical performance for a live LP bot.
when_to_use: Read only after the ordinary raw snapshot reports a concrete conflict with injected ownership, CORE DATA, RISK STATE, journal, or a lifecycle receipt, or reports a missing, idle, stopped, duplicated, malformed, or incomplete bot. Never read from injected ownership alone or during complete empty/healthy supervision.
references_routine: snapshot_trend_aware_lp_bots
source: agent:multi_lp_rebalancer_manager
---

# Bot Lifecycle Reconciliation

Use the already-required ordinary `snapshot_trend_aware_lp_bots` result. Do not call the
snapshot twice in one tick merely because another surface disagrees. This playbook
classifies lifecycle evidence; it never authorizes a blind retry or direct LP mutation.

## Evidence Authority Table

| Priority | Evidence | What it may prove | What it never proves |
|---:|---|---|---|
| 1 | Current raw active-bot membership plus fresh exact `custom_info` | Live instance identity and controller lifecycle | Historical PnL outside that live instance |
| 2 | Exact current bot config | Persisted identity and requested policy | Runtime application, LP existence, close, or cleanup |
| 3 | Current-session submitted mutation identity and journal | What this session attempted and must reconcile | Submission from intent alone, success, or terminality |
| 4 | Wallet balance | Capital feasibility | Bot, controller, LP, pool, or inventory ownership |
| 5 | Injected ownership, `[CORE DATA - executors]`, `[RISK STATE]` counts, latest controller performance, formatted status, and prior journal prose | Accounting context or a reason to investigate | Current bot membership, slot occupancy, active LP identity, or lifecycle |

Never let a lower-priority surface overturn complete higher-priority evidence. Preserve
framework risk ceilings and an explicit blocked status, but do not use injected executor
rows/counts to reserve a slot, pool, base mint, or controller budget. Raw absence still
requires the current-session classification below; it does not erase continuity by itself.

## Classification Table

| Observation | Classification | Required response |
|---|---|---|
| First ordinary snapshot is `complete`, has zero owned bots/rows, and this session has no submitted deploy or previously verified live exact bot | `EMPTY_NAMESPACE` | Treat every slot as `VACANT`. Any injected adopted bot or active executor is stale historical context. Do not quarantine it or repeat reconciliation; do not load this skill again, repeat status, or refresh balances for that conflict. Continue the normal tick if time permits. |
| Exact namespaced bot is present with one complete fresh row exactly matching this session's complete deployment intent/result | `LIVE_CURRENT` | Record and use its full timestamped instance plus raw lifecycle row for normal supervision. |
| Exact namespaced bot is present without one complete matching current-session deployment intent/result | `INHERITED_WIND_DOWN` | Never resume, rearm, retune, or include it in the new portfolio. Persist one operator exit, supervise cleanup, then archive it under the startup boundary. |
| Exact namespaced slot bot, current or inherited, is stopped; raw topology has zero controller configs; targeted `get_config` confirms none; and raw performance has no controller, `custom_info`, Executor, or position evidence | `EMPTY_BOT_TERMINAL_NO_EFFECT` | No controller could trade or emit `EXITED`. Journal the exact no-effect proof, call `stop_bot` once for that timestamped instance, and end the tick. Keep its slot/budget reserved until later `archive_confirmed`. |
| Namespaced bot is present but bot state is `idle`, `stopped`, `error`, or `unknown`, or telemetry/config/topology is incomplete | `PRESENT_UNVERIFIED` | Never call it vacant or deploy a replacement. If exact slot/pool/base/budget are known, quarantine that scope. If any are unknown, block all new admission while continuing healthy sibling supervision/exits. Use one targeted config/log read only if it can resolve the anomaly; do not repeat it against unchanged evidence. |
| A deploy was submitted/confirmed this session but its exact instance is not yet visible | `DEPLOY_PENDING` | Keep the slot and budget reserved. End the deployment tick; reconcile next tick. Immediate absence is not rejection or terminal no-effect. |
| A bot previously proved live in this session disappears without prior exact `EXITED` plus archive request | `MISSING_UNRESOLVED` | Quarantine its slot/pool/base/budget. Absence does not prove the LP closed; never redeploy that authority. |
| Exact `EXITED` was proven, `stop_bot` was submitted, and active status omits the exact instance but bot-run archival is not confirmed | `ARCHIVE_PENDING` | Keep slot and budget reserved. Active absence and the background receipt are not archive proof. |
| The exact current-session archive target appears in snapshot `archive_confirmed` | `ARCHIVED_EXACT` | Release its slot and budget. |
| More than one live timestamped instance maps to one slot base, or live rows duplicate a pool/base | `DUPLICATE_LIVE` | Quarantine every conflicting instance and make no admission mutation. Preserve full names; do not choose the newest by timestamp. |
| Bot name is not an exact slot-1..3 timestamped instance or its controller/config ID maps to another slot | `INVALID_SLOT_IDENTITY` | Quarantine it and block new admission. Never normalize or adopt the closest-looking name. |
| Persisted config/update receipt exists but raw acknowledgment is missing | `CONFIG_PENDING` | Keep prior runtime policy authoritative and wait. YAML success is not controller application. |
| Kill-switch/run state conflicts with `custom_info` lifecycle or Executor/position fields contradict lifecycle | `INCOHERENT_LIVE` | Quarantine the exact controller. Do not infer closure, archive, restart, or vacancy. |

## Exact Reconciliation Rules

1. Use the stable slot base only for saved config and a new deploy request. Use the exact
   timestamped live instance for status expectations, config updates, exits, logs, and
   archive.
2. Omit `expected_bots` for ordinary discovery. After an archive request, omit the target
   from live expectations and pass it only in `archive_check_bots`; active absence is
   intermediate evidence until exact bot-run `ARCHIVED` confirmation.
3. Treat `running` bot membership as current transport/process evidence, not as proof of
   `ACTIVE`. Treat `idle` as stale MQTT evidence and `stopped` as a still-present
   container/config surface; neither is vacancy.
4. Require coherent lifecycle details: `ACTIVE` needs an active LP identity and position
   address; `EXITED` needs completed exit plus no active LP/Order Executor; a retained
   terminal Executor envelope is allowed. A contradictory row is scoped quarantine even
   when its schema and immutable identity match.
5. A confirmed saved-config upsert may continue only to the same intent's bounded deploy;
   deploy/update/archive ends its tick. Confirm runtime effects through later raw evidence.
   Timeout is `uncertain`; never turn absence or an error sentence into a retry.
6. One ambiguous controller must not block read-only supervision or provably isolated
   exits/archives of healthy siblings. It does block new shared-wallet admission while a
   state-changing call may still be unresolved.
7. Current-session provenance requires a complete deployment intent/result plus exactly one
   raw timestamped instance matching its slot base, config/controller, pool/base, and budget.
   Injected adoption or namespace similarity cannot convert an inherited bot.
8. For missing config outcome, call `manage_controllers(action="describe",
   config_name=<exact>, include_code=false)` once. Exact full-field match proves saved config,
   not deployment or runtime application; mismatch permits corrected upsert under fresh rules.
9. `EMPTY_BOT_TERMINAL_NO_EFFECT` requires every table condition and exact namespace/slot
   identity. Missing, nonempty, conflicting, active, malformed, or uncertain evidence stays
   `PRESENT_UNVERIFIED`; stopped status alone never suffices. This is the only `stop_bot`
   exception without controller `EXITED` and never authorizes `stop_controllers`.
10. After its `stop_bot`, use the exact instance only in `archive_check_bots`. Release on
    `archive_confirmed`, never active absence. Any later deployment is a new operation after
    fresh signal, wallet, allocation, exclusivity, and exact config-name validation; never
    repair, restart, or redeploy the malformed bot.

## Tick Result

Write one concise journal classification with the exact bot/slot, the conflicting
surfaces, the winning evidence, reserved scope, and next evidence. Do not scan pools when
the result blocks admission. For `EMPTY_NAMESPACE`, do not spend another tick proving the
same absence. Balance refresh is allowed only if funded admission is now the next action;
otherwise proceed to the next normal phase within the existing tick budget.

# Memecoin Trender Run 1 Postmortem

**Run ID:** `01KXNP3DFJSDYSDZCGW2TZF21V`  
**Agent:** `memecoin_trender`  
**Date:** 2026-07-16  
**Run duration:** 7,713 seconds (2h 8m 33s)  
**Terminal state:** manually stopped

## Executive assessment

The run was **mechanically successful but economically unprofitable**.

Condor did the main framework jobs correctly: it froze the effective agent spec, ran and persisted 92 complete ticks, enforced the three-executor/0.1 SOL risk envelope, attributed native executors to the run, honored the six-tick stand-down, and closed the final position when the run was stopped. The agent generally followed its entry and notification policy.

The strategy lost approximately **0.003239 SOL gross before transaction fees** across 29 economically completed round trips plus one final manual-stop exit. With approximately **0.001736 SOL of recorded transaction fees**, the estimated wallet-level result is about **-0.004974 SOL**, subject to reconciliation against wallet transactions. The gross return was about **-0.56% on 0.58 SOL of cumulative entry notional** (not on simultaneously deployed capital).

The most important result is not merely that the signal lost money. Execution quality and instrumentation materially affected the run:

1. Time-limit exits produced most of the loss.
2. Barrier trigger PnL sometimes differed drastically from realized swap PnL.
3. One executor was persisted as a completed take-profit despite having a canceled entry and no exit transaction.
4. `total_volume=0` and `total_amount_quote=100` were not meaningful performance fields for this run.
5. Stop semantics were correct for the frozen config but confusing at the API boundary.

## Evidence and methodology

Primary run evidence:

- Run event stream: `agents/memecoin_trender/runs/01KXNP3DFJSDYSDZCGW2TZF21V.jsonl`
- Executor event ledger: `agents/memecoin_trender/executors.jsonl`
- Notification outbox: `store/notifications.jsonl`
- Frozen agent spec: the `run_started` event in the run stream

Framework references:

- Tick engine and snapshots: `condor/agents/engine.py:500-614`
- Live metric projection: `condor/agents/engine.py:642-695`
- Append-only RunStore: `condor/agents/runstore.py:1-20`, `40-56`
- Journal/event projections: `condor/agents/projections.py:1-16`
- Native executor provider: `condor/agents/providers/native_executors.py:268-388`
- Risk checks: `condor/agents/risk.py:115-190`
- Durable executor risk checks: `condor/executors/ops.py:624-664`
- Executor performance aggregation: `condor/executors/ops.py:704-719`
- Run stop behavior: `condor/agents/engine.py:215-253`, `condor/agents/lifecycle.py:148-180`

Performance was calculated from landed executor proceeds and notification-reported realized PnL. Mark/barrier PnL was used only to assess trigger behavior. The anomalous canceled-entry executor was excluded from economic totals.

## Performance scorecard

### Run-level

| Metric | Result |
|---|---:|
| Complete ticks | 92 |
| Started but interrupted tick | 1 (tick 93) |
| Mean / median tick duration | 72.9s / 69.3s |
| Mean tick-start interval | 83.4s |
| Timed-out ticks | 0 |
| Executor records created | 30 |
| Economically completed exits | 29 |
| Winning / losing exits | 12 / 17 |
| Win rate | 41.4% |
| Cumulative valid entry notional | 0.58 SOL |
| Framework-reported gross PnL | approximately -0.003239 SOL |
| Gross return on cumulative notional | approximately -0.56% |
| Recorded transaction fees | approximately 0.001736 SOL |
| Estimated result after recorded fees | approximately -0.004974 SOL |
| Peak concurrent executors | 3 |
| Peak intended position exposure | 0.06 SOL |

The after-fee number should be reconciled against Solana wallet transactions because executor `amount_spent`/`proceeds` and fee accounting are separate fields.

### Exit-type contribution

| Exit type | Count | Gross PnL | Mean realized return |
|---|---:|---:|---:|
| Take profit | 8 valid | +0.005432 SOL | +3.40% |
| Stop loss | 4 | -0.003759 SOL | -4.70% |
| Time limit | 16 | -0.004875 SOL | -1.52% |
| Manual early stop | 1 | -0.000036 SOL | -0.18% |

There is a ninth executor labeled `take_profit`, but it had no filled entry or exit and is excluded.

### Best and worst realized exits

- Best: `F4GpAFr6`, take-profit exit, **+0.001496 SOL (+7.48%)**.
- Worst: `4ko5tSr5`, time-limit exit, **-0.002722 SOL (-13.61%)**.

### Token contribution

| Token prefix | Trades | Gross PnL |
|---|---:|---:|
| `BCdwQBAn` | 3 | +0.001274 SOL |
| `9cRCn9rG` | 4 | +0.001209 SOL |
| `B4ptaVsU` | 3 | +0.000288 SOL |
| `pumpCmXq` | 2 valid | +0.000102 SOL |
| `CARDSccU` | 1 | +0.000076 SOL |
| `BcHEaaTC` | 1 | -0.000103 SOL |
| `HNg5PYJm` | 2 | -0.000196 SOL |
| `CFPkPq1e` | 4 | -0.000529 SOL |
| `7V6Sk63y` | 2 | -0.000559 SOL |
| `CashcatZ` | 2 | -0.000824 SOL |
| `F4GpAFr6` | 3 | -0.001034 SOL |
| `4ko5tSr5` | 2 | -0.002943 SOL |

The strategy had some repeatable winners, but losses in `4ko5tSr5` and `F4GpAFr6` overwhelmed them.

## Expected behavior versus actual behavior

### 1. Tick pipeline and cadence

**Expected:** each under-capacity tick reads state, scans, judges at most one entry, enters if appropriate, and journals. `frequency_sec=60` is a target cadence, not a guarantee that a full tick completes every minute.

**Actual:** 93 ticks started, 92 completed, and tick 93 was interrupted by the manual stop. No tick timed out. Mean tick duration was 72.9 seconds and mean tick-start interval was 83.4 seconds.

**Assessment:** **Pass.** The lower-than-60-ticks-per-hour throughput follows Condor's serialized model/tool loop rather than a scheduler failure. However, a momentum strategy using m5 signals loses freshness when its decision loop averages 83 seconds and sometimes takes 176 seconds.

### 2. Entry discipline

**Expected:** at most one new position per tick, no token already held, no token stopped out in the prior 24 hours, and only dual-positive m5/h1 momentum.

**Actual:** all 30 executor creations mapped to distinct ticks; no tick opened more than one position. Peak concurrency was three. Snapshots show explicit m5/h1 filtering, held-token exclusion, and recent-stop exclusion.

Examples:

- Tick 1 selected `CFPkPq1e` with m5 +1.47% and h1 +7.53%.
- Tick 48 explicitly excluded `CFPkPq1e` after a recent stop.
- Tick 58 explicitly excluded recent stops before selecting a candidate.

**Assessment:** **Pass, based on persisted model decisions.** A stronger framework-level audit would persist normalized candidate inputs and selected mint separately from prose so compliance can be checked without parsing model responses.

### 3. Capacity and risk limits

**Expected:** maximum three open executors and 0.1 SOL maximum position-size/exposure risk envelope. Default entry size was 0.02 SOL.

**Actual:** every intended entry used 0.02 SOL; peak concurrent exposure was three positions or 0.06 SOL. No risk block or drawdown shutdown was triggered.

**Assessment:** **Pass.** Both model-facing and durable executor-layer risk checks behaved as designed.

### 4. Mandatory barriers and executor-owned exits

**Expected:** every position has TP +3%, SL -5%, and TTL 600 seconds; the native executor owns exits.

**Actual:** all created executor configs carried all three barriers. Exit mix was 8 valid take profits, 4 stop losses, 16 time limits, and 1 manual-stop exit.

**Assessment:** **Pass mechanically, weak economically.** The barriers were present and active, but their trigger values were not equivalent to realizable exit prices.

Two material examples:

- An `F4GpAFr6` executor triggered take profit at mark PnL **+3.72%**, yet landed proceeds were **-0.001266 SOL (-6.33%)** before separately recorded fees.
- A `4ko5tSr5` time-limit executor showed mark PnL about **-2.99%**, yet its landed exit was **-0.002722 SOL (-13.61%)**.

The agent spec warns that stop losses are intents rather than guarantees. The evidence shows the same warning must apply to take-profit and time-limit exits: all barriers are trigger conditions, not realized-price guarantees.

### 5. Stand-down after consecutive stop losses

**Expected:** after two consecutive stop-loss closes, skip entries for the next six ticks and notify once.

**Actual:** tick 50 entered stand-down after the `F4GpAFr6` and `B4ptaVsU` stop losses. Ticks 51–56 opened no positions, and tick 57 resumed scanning. Exactly one stand-down notification was emitted.

**Assessment:** **Pass.** This is the clearest example of the agent following stateful policy correctly across ticks.

### 6. Journaling and learnings

**Expected:** each tick records candidates, choice or skip reason, barriers, holding PnL, and new closed-position lessons.

**Actual:** all 92 completed ticks persisted a `state_snapshot` containing the model response, metrics, and duration. Responses generally included a journal section and recorded closes. The run stream is therefore reconstructable.

**Assessment:** **Pass for episodic history; partial for reusable learning.** Condor's canonical journal is the append-only run event stream, with Markdown as a projection. The agent also called memory tooling, but long-term learning quality was not independently scored here. Structured outcome records would be more reliable than prose-only journal entries.

### 7. Notification discipline

**Expected:** routine ticks remain silent; executor runtime announces entries/exits; the agent only announces FAILED executors or stand-downs.

**Actual:** the outbox contains trade notifications and one agent stand-down notification, with no routine tick spam.

**Assessment:** **Pass with one observability defect.** The anomalous canceled-entry executor emitted an entry notification but no corresponding exit/failure notification.

### 8. Manual stop behavior

**Expected from the frozen config:** `keep_position_on_stop: false` means stopping the live engine closes the position even when `control_run(close=false)` is used. `close=true` forces closure; otherwise the engine honors the agent config.

**Actual:** the final `9cRCn9rG` executor transitioned through early stop and landed a closing swap for **-0.000036 SOL (-0.18%)**. The executor terminal record has a close reference and proceeds.

**Assessment:** **Pass internally, API semantics need clarification.** The control response returned `closed: false` because the request's `close` flag was false, even though the engine closed the position due to `keep_position_on_stop: false`. A caller can easily misreport that the inventory remained open.

## Framework and data-quality findings

### High: completed phantom executor

Executor `position_spot_1422dc398cbd4ceba390f9df40463610` was persisted as:

- `status: CLOSED`
- `close_type: take_profit`
- mark PnL +3.01%

but its only order was an entry marked `CANCELED`, with zero filled quantities, and it had no `close_ref`, `exit_price`, or `proceeds`. It emitted an entry notification and no terminal trade notification.

This record must not count as a trade, win, volume, or PnL. A position executor should not reach active/complete barrier states without a confirmed filled entry. Terminal validation should require coherent fills and settlement fields.

### High: barrier PnL is not realized PnL

`close_reason` embeds mark PnL at trigger time, while notifications report landed proceeds. On thin/volatile assets, these can differ enough for a nominal take profit to realize a loss.

Performance APIs and UI should label these separately:

- `trigger_pnl_pct`
- `realized_pnl_quote`
- `realized_pnl_pct`
- `fees_quote`
- `slippage_from_trigger_pct`

A take-profit close with negative realized PnL should be highlighted as execution degradation, not counted as an ordinary winning TP.

### Medium: `total_volume=0` is an instrumentation gap

The live run reported `total_volume=0.0` despite 29 valid round trips. `NativeExecutorsProvider` does not emit `total_volume`, while the engine defaults the missing field to zero (`condor/agents/engine.py:645-650`; `condor/agents/providers/native_executors.py:378-388`).

Do not use this field to gauge strategy activity until the provider aggregates filled quote quantities.

### Medium: `daily_pnl` is misnamed

The live field is the provider's current run-scoped realized plus unrealized PnL, not necessarily calendar-day PnL (`condor/agents/engine.py:642-651`, `677-695`). It happened to closely match final gross realized PnL here, but its name invites incorrect interpretation.

Rename it to `run_total_pnl` or expose both calendar-day and run-scoped metrics explicitly.

### Medium: `total_amount_quote=100` is irrelevant here

This field comes from generic session configuration (`condor/agents/config.py:54-57`) and is not measured exposure, wallet equity, or cumulative traded notional. Beside a SOL-denominated 0.1 SOL risk cap, it is actively misleading.

### Medium: tool-call event status is incomplete

The run stream contains 190 `tool_call` events, all persisted with `status: pending`; no corresponding completed/error update events appear. Tick snapshots and executor ledgers allow reconstruction, but the tool-call audit trail itself cannot prove outcomes.

Persist terminal tool-call status/output or add a paired `tool_result` event.

### Low: cadence is slow for an m5 strategy

The framework behaved as currently designed, but an 83-second average start interval and 176-second maximum tick duration consume a meaningful part of a five-minute signal horizon. Scanning and deterministic filtering should move into a routine/provider, leaving the model only the final discretionary ranking when necessary.

## Strategy learnings

1. **Time-limit exits are the main drag.** Sixteen TTL exits lost about 0.004875 SOL. The entry filter found momentum that often failed to continue over ten minutes.
2. **A +3% / -5% barrier pair needs a high hit rate or better TTL behavior.** Valid TP exits made +0.005432 SOL, but stop and TTL losses totaled -0.008635 SOL before the manual exit.
3. **Liquidity floors did not guarantee execution quality.** A $100k pool floor still allowed severe trigger-to-fill degradation. Pool depth at the intended 0.02 SOL swap size, route impact, and recent volatility are more useful than headline liquidity alone.
4. **Repeated-token outcomes were concentrated.** `BCdwQBAn` and `9cRCn9rG` worked; `4ko5tSr5` and `F4GpAFr6` did not. Learning should distinguish signal quality from route/impact quality per mint.
5. **Stops based only on consecutive `stop_loss` labels miss catastrophic TTL or TP execution.** The -13.61% TTL and -6.33% realized take-profit loss did not trigger the two-stop cooldown. Stand-down logic should use realized loss magnitude and execution degradation, not only `close_type`.

## Recommendations

### Framework

1. Reject/mark FAILED any position executor whose entry is canceled or zero-filled; never synthesize an active position from requested amounts.
2. Add terminal executor invariants: a completed round trip requires filled entry quantity and, unless preserving inventory, a filled exit plus settlement reference/proceeds.
3. Separate trigger PnL from realized PnL and fees in storage, notifications, aggregation, and UI.
4. Compute native executor volume from filled quote quantities.
5. Rename `daily_pnl` to `run_total_pnl` or correct its semantics.
6. Remove or clearly label generic `total_amount_quote` for native SOL-denominated runs.
7. Persist terminal tool-call results instead of only `pending` events.
8. Return actual post-stop inventory disposition from `control_run`, e.g. `requested_close`, `effective_keep_position`, and `inventory_closed`.

### Agent

1. Replace headline liquidity filtering with executable quote/price-impact checks at the configured size.
2. Add a maximum allowed route impact and reject candidates with unstable/no route.
3. Base cooldowns on realized losses, including TTL exits and adverse TP fills; for example, stand down after any realized loss worse than -8% or two losses worse than -4%.
4. Consider a tighter or adaptive TTL exit rule, since TTL exits were the largest aggregate loss source.
5. Persist structured per-trade learning: signal values at entry, liquidity/depth, trigger PnL, realized PnL, fees, impact, and close type.
6. Move scan/filter work into a deterministic routine to reduce tick latency and make rule compliance directly testable.

## Final verdict

- **Did Condor run the agent correctly?** Mostly yes.
- **Did the agent follow its stated policy?** Yes, with strong evidence for capacity, one-entry-per-tick, recent-stop exclusions, mandatory barriers, and stand-down behavior.
- **Did the strategy perform well?** No. It lost money gross and more after recorded fees.
- **Was the run fully trustworthy as measured?** No. One phantom completed executor and several misleading aggregate fields mean executor-level reconciliation is required.
- **Should this configuration be run unchanged with larger size?** No. Fix executor invariants and realized-PnL instrumentation first, then retest at the same small size with impact-aware entry filtering.

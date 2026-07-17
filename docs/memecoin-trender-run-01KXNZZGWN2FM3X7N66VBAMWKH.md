# Memecoin Trender Run 2 and Harness Incident Review

**Run ID:** `01KXNZZGWN2FM3X7N66VBAMWKH`  
**Agent:** `memecoin_trender`  
**Date:** 2026-07-16  
**Run duration:** 2,407 seconds (40m 7s)  
**Terminal state:** manually stopped

## Executive assessment

This validation run was **profitable, behaviorally compliant, and materially cleaner than run 1**. All eight executors had coherent filled entry and exit orders, settlement references, proceeds, and matching notifications. No phantom or zero-fill completed executor appeared, and no take-profit trigger realized as a loss.

Performance was **+0.002152194 SOL gross** and an estimated **+0.001583819 SOL after separately recorded transaction fees**. The sample is too small to establish positive expectancy, but it is strong evidence that the executor-integrity fixes addressed the most serious defects found in run 1.

The stop incident was not an executor or Condor settlement failure. The first reported stop attempt left no OpenClaw tool-call record and no Condor lifecycle trace. After OpenClaw was restarted, the request reached `control_run`; Condor ended the run immediately and all three remaining executors settled within approximately 11 seconds. The most likely fault domain is therefore the **OpenClaw TUI/MCP harness before Condor invocation**, although the missing failed-attempt record prevents a definitive root cause.

A separate harness anomaly occurred on tick 31: the autonomous trading response was only `Model switched to claude-sonnet-4-6.` despite the persisted prompt containing no such instruction. This consumed a tick without running the strategy and indicates ACP/model-control response contamination or misrouting. It is suspiciously close to the stop incident, but there is not enough evidence to prove that the two share one cause.

## Evidence

Run and trading evidence:

- Run stream: `agents/memecoin_trender/runs/01KXNZZGWN2FM3X7N66VBAMWKH.jsonl`
- Exact prompt artifacts: `agents/memecoin_trender/runs/01KXNZZGWN2FM3X7N66VBAMWKH.artifacts/`
- Executor ledger: `agents/memecoin_trender/executors.jsonl`
- Notification outbox: `store/notifications.jsonl`
- Run 1 audit: `docs/memecoin-trender-run-01KXNP3DFJSDYSDZCGW2TZF21V.md`

Framework and harness references:

- Prompt emission: `condor/agents/engine.py:517-520`
- State snapshot projection: `condor/agents/engine.py:596-620`
- Artifact spill and hashes: `condor/agents/runstore.py:18-20`, `217-270`
- Control client: `condor/control/client.py:22-52`
- Control socket startup hardening: `condor/control/server.py:51-71`
- Run stop implementation: `condor/agents/engine.py:215-253`
- Lifecycle response: `condor/agents/lifecycle.py:148-180`
- OpenClaw successful stop turn: `~/.openclaw/agents/main/sessions/6b7f2218-02d8-4439-9ce2-cf3616973dc4.jsonl`

## Performance scorecard

| Metric | Result |
|---|---:|
| Run duration | 40m 7s |
| Ticks started / completed | 32 / 31 |
| Mean / median tick duration | 68.9s / 66.0s |
| Mean tick-start interval | 76.2s |
| Maximum tick duration | 158.6s |
| Timed-out ticks | 0 |
| Valid round trips | 8 |
| Winners / losers | 6 / 2 |
| Win rate | 75.0% |
| Cumulative entry notional | 0.16 SOL |
| Gross PnL | +0.002152194 SOL |
| Recorded transaction fees | 0.000568375 SOL |
| Estimated after-fee PnL | +0.001583819 SOL |
| Gross return on cumulative notional | +1.35% |
| Estimated after-fee return | +0.99% |
| Peak concurrent positions | 3 |
| Peak intended exposure | 0.06 SOL |
| Failed/phantom executors | 0 |

The after-fee value subtracts executor `extra.tx_fee` from proceeds-minus-entry PnL. Wallet transactions remain the final source for economic reconciliation.

### Exit-type contribution

| Exit type | Count | Gross PnL | Fees | Estimated after fees |
|---|---:|---:|---:|---:|
| Time limit | 4 | +0.000078004 | 0.000266564 | -0.000188560 SOL |
| Take profit | 2 | +0.001435468 | 0.000151847 | +0.001283621 SOL |
| Manual early stop | 2 | +0.000638722 | 0.000149964 | +0.000488758 SOL |

Time-limit exits were slightly positive before fees but negative after fees. This repeats run 1's core strategy concern in milder form: TTL exits do not currently pay for their execution costs.

### Trade-level results

| Entry tick | Token prefix | Exit | Gross PnL | Recorded fees |
|---:|---|---|---:|---:|
| 1 | `pumpCmXq` | time limit | -0.000200347 | 0.000120482 |
| 4 | `CARDSccU` | time limit | +0.000243379 | 0.000025579 |
| 6 | `BcHEaaTC` | time limit | +0.000098990 | 0.000010503 |
| 17 | `CARDSccU` | time limit | -0.000064018 | 0.000110000 |
| 25 | `9cRCn9rG` | take profit | +0.000681071 | 0.000038657 |
| 28 | `BcHEaaTC` | take profit | +0.000754397 | 0.000113190 |
| 29 | `CARDSccU` | manual stop | +0.000058421 | 0.000035686 |
| 30/31 boundary | `9cRCn9rG` | manual stop | +0.000580301 | 0.000114278 |

The final `9cRCn9rG` decision began in tick 30 and the executor persisted just after tick 31 started. This is an event-ordering boundary, not evidence that tick 31 intentionally opened an extra position.

## Comparison with run 1

| Metric | Run 1 | Run 2 |
|---|---:|---:|
| Duration | 2h 8m | 40m 7s |
| Valid trades | 29 | 8 |
| Win rate | 41.4% | 75.0% |
| Gross PnL | -0.003239 SOL | +0.002152 SOL |
| Estimated after-fee PnL | -0.004974 SOL | +0.001584 SOL |
| Phantom/zero-fill completed executors | 1 | 0 |
| Nominal TP realized as loss | Yes | No |
| Missing terminal trade notification | Yes | No |
| Coherent entry/exit settlement on all valid records | No | Yes |

The comparison is favorable but not statistically conclusive. Run 2 had only eight trades and was stopped while two positions were profitable.

## Executor integrity and fixed issues

All eight run-2 executors ended `CLOSED` and had:

- nonzero filled entry base quantity,
- nonzero filled exit quantity,
- `open_ref` and `close_ref`,
- proceeds and amount spent,
- coherent entry and exit order records,
- matching entry and exit notifications.

This directly addresses run 1's phantom take-profit executor, whose entry was canceled and zero-filled.

Trigger-to-fill quality also improved:

- `9cRCn9rG` take-profit trigger PnL was about +3.94%; realized proceeds were +3.41%.
- `BcHEaaTC` take-profit trigger and realized return were both about +3.77%.
- No take-profit close realized a loss.

The fixes appear successful for this sample.

## Agent behavior versus specification

### Entry and capacity discipline

**Expected:** at most one new position per tick, 0.02 SOL per entry, maximum three concurrent positions.

**Actual:** all eight entries were 0.02 SOL, no decision tick intentionally opened more than one, and peak concurrency was three/0.06 SOL.

**Assessment:** Pass.

### Momentum judgment

**Expected:** require both m5 and h1 positive, prefer deeper liquidity, reject marginal momentum when unconvinced.

**Actual:** prompt/response evidence shows dual-positive filtering, liquidity comparisons, and multiple under-capacity skips when signals were only barely positive.

**Assessment:** Pass. Persisted full prompts improve the auditability of the candidate context substantially.

### At-capacity behavior

**Expected:** hold and journal without scanning or entering.

**Actual:** ticks 7–10 correctly held at capacity without new entries.

**Assessment:** Pass.

### Recent stop exclusions

**Expected:** do not re-enter a token stopped out in the prior 24 hours.

**Actual:** the agent excluded the relevant recently stopped tokens. However, injected runtime memory referred to a four-hour cooldown while the frozen strategy says 24 hours.

**Assessment:** Operationally safe in this run, but policy sources conflict. Align the enforced and narrated cooldown window.

### Stand-down

**Expected:** six-tick stand-down after two consecutive stop losses.

**Actual:** no stop-loss exits occurred, so no stand-down was required.

**Assessment:** Not exercised.

### Learnings and notifications

**Expected:** routine ticks remain silent; durable new operational facts use the learning tool.

**Actual:** no routine agent-notification spam occurred, and four `record_learning` calls were attempted. Every trade had matching entry/exit notifications.

**Assessment:** Pass at the behavioral level. Tool-call completion remains inadequately recorded, as discussed below.

## New run-storage format

### What changed

Run 1 stored no exact per-tick prompt. Run 2's `tick_started` event carries the exact prompt. Because each prompt exceeded the approximately 16 KB event cap, RunStore wrote it to:

`<run_id>.artifacts/<sequence>-tick_started.json`

The main JSONL retains:

- artifact-relative path,
- SHA-256,
- byte length,
- bounded preview.

`state_snapshot` now also has normalized `decision` and `state` fields alongside the bounded response and metrics.

### Validation

- 32 `tick_started` events referenced 32 artifacts.
- Every referenced artifact existed.
- Every SHA-256 and byte count matched.
- No unreferenced artifacts were present.
- Run file permissions were `0600`.
- Artifact file permissions were `0600`.
- Artifact directory permissions were `0700`.

### Size tradeoff

| Storage | Bytes |
|---|---:|
| Run 1 monolithic JSONL | 206,496 |
| Run 2 main JSONL | 113,700 |
| Run 2 prompt artifacts | 652,589 |
| Run 2 combined | 766,289 |

The new main stream is easier and safer to query because oversized prompt bodies are externalized, but total storage is approximately 3.7 times the old run despite run 2 being much shorter. Exact prompts greatly improve reproducibility and anomaly diagnosis, but retention, compression, and sensitive-context policies are now necessary.

### Important limitation

All 74 run-2 `tool_call` events still have `status: pending`. There are no paired completed/error tool-result events. Prompt reproducibility improved, but tool outcome auditing did not.

Recommended event model:

- `tool_call_started`
- `tool_call_completed` with redacted/bounded result or artifact
- `tool_call_failed`

or equivalent updates keyed by `tool_call_id`.

## Harness anomaly: tick 31

At 11:14:21 PDT, tick 31 started with a valid 21 KB trading prompt captured in its artifact. At 11:14:34, the only model response was:

> Model switched to claude-sonnet-4-6.

The tick recorded zero actions and skipped the full trading pipeline. The exact prompt contains no model-switch request or user directive that explains this response.

This is **strong evidence of ACP/model-control response contamination, stale output, or response misrouting**. It is not a strategy decision and should not be accepted as a successful trading tick.

Recommended harness invariant: validate that a live trading response begins with an admissible decision/status shape. If the response is a known CLI/control acknowledgement, classify the tick as a harness error and retry once with a fresh client rather than persisting it as a successful tick.

## Stop-control incident

### Timeline

- 10:35:29 — Condor daemon/control socket active.
- 10:35:51 — run started.
- 11:14:21 — tick 31 started.
- 11:14:34 — anomalous model-switch response persisted.
- 11:15:21 — tick 32 started.
- First stop attempt — reported by the user, but no durable tool/control trace survives.
- OpenClaw exited/restarted.
- 11:15:53 — successful stop request entered the surviving OpenClaw session.
- 11:15:58 — `condor__control_run` invoked.
- 11:15:59.021 — Condor wrote `run_ended`.
- 11:15:59.076 — OpenClaw received the successful tool result.
- By approximately 11:16:10 — all remaining executor settlements completed.

### Fault-domain assessment

#### High confidence: the successful stop worked correctly

Once invoked, the OpenClaw-to-Condor tool call returned in approximately 0.16 seconds. Condor canceled the active tick, wrote a terminal run event, and initiated executor stops. Two early-stop settlements and one take-profit settlement landed shortly afterward.

#### High confidence: the first reported attempt did not reach the surviving Condor control path

There is no earlier `control_run` tool call in the surviving OpenClaw session and no earlier Condor terminal lifecycle event. Condor continued ticking. Had the one-shot control client reached a missing/refused socket, it should have raised an explicit 502/503 error (`condor/control/client.py:22-52`) rather than silently reporting success.

#### Medium confidence: restarting OpenClaw repaired the failing component

The Condor daemon and socket remained the same, while fresh OpenClaw/MCP processes appeared around the restart. This makes a stale/hung TUI turn, interrupted OpenClaw session, or stale MCP child more likely than a Condor daemon failure.

Because the failed attempt itself was not durably logged, it is impossible to distinguish among:

- a TUI message that was never submitted,
- an interrupted model turn that never selected the tool,
- a stale/unresponsive MCP subprocess,
- another OpenClaw harness routing failure.

#### Low confidence: Condor control socket was the cause

The socket existed continuously and the fresh OpenClaw/MCP process used it successfully without restarting Condor. There is no control-socket error evidence for this incident.

### Relationship to tick 31 anomaly

The anomalous model-switch output occurred about 79 seconds before the successful stop request entered OpenClaw. Both point toward harness/process-state problems, but they travel through different paths:

- Tick 31: Condor -> ACP model client -> model response.
- Stop: user -> OpenClaw -> Condor MCP tool -> Unix control socket.

Treat a shared root cause as plausible, not proven.

## Stop-response semantics

The successful response was conceptually:

```json
{
  "stopped": true,
  "closed": false,
  "stopped_executors": ["...", "...", "..."]
}
```

All remaining inventory was nevertheless closed because the frozen run config had `keep_position_on_stop: false`.

Current meaning:

- `closed: false` means the caller did not pass `close=true`.
- It does **not** mean inventory was preserved.
- `stopped_executors` means stop was requested for those executor records.
- It does **not** mean every settlement was complete when the response returned.

The response arrived before two closing swaps settled. Therefore the correct immediate assistant message should have been: **“Stop accepted; three executors are winding down. Inventory disposition is pending verification.”**

Recommended response fields:

- `stop_accepted`
- `requested_close`
- `effective_keep_position`
- `executors_signaled`
- `settlement_pending`
- `inventory_closed` only after terminal verification

## Recommendations

### Harness and lifecycle

1. Persist every lifecycle attempt as `control_requested`, `control_applied`, or `control_failed`, including source surface and request ID.
2. Record OpenClaw/MCP failed and interrupted tool turns durably enough to distinguish “user message never reached agent” from “tool transport failed.”
3. Health-check and recycle stale MCP subprocesses after tool-server source/schema changes.
4. Detect known CLI/model-control acknowledgements in autonomous tick output and retry with a clean ACP client.
5. Do not mark a tick successful when it performs no pipeline work due to a harness response.
6. Return effective inventory semantics rather than echoing only the caller's `close` flag.
7. Report stop acceptance separately from executor settlement; optionally wait for bounded terminal verification.

### Run format

1. Keep exact prompt artifacts: they made the tick-31 anomaly provable.
2. Add compression and retention policies; run 2 used 766 KB for 40 minutes.
3. Keep current `0600`/`0700` permissions and hash verification.
4. Persist terminal tool results keyed by `tool_call_id`.
5. Consider storing normalized candidate/decision records so policy compliance is machine-checkable without parsing prose.

### Strategy

1. Continue testing at 0.02 SOL; do not infer stable profitability from eight trades.
2. Track TTL performance after fees; it remained negative despite positive gross PnL.
3. Align the 24-hour spec exclusion with the four-hour runtime-memory cooldown.
4. Continue monitoring trigger-to-fill divergence, even though run 2 showed no catastrophic cases.

## Verification

Targeted tests were run after the audit:

```text
tests/test_runstore.py
tests/test_run_identity.py
tests/test_control_channel.py
tests/test_review_findings.py
```

Result: **44 passed**.

These tests validate the current RunStore/artifact and control-channel implementation, but they do not reproduce the missing first OpenClaw stop attempt or tick-31 response contamination.

## Final verdict

- **Trading performance:** Good for this small sample; +0.002152 SOL gross and approximately +0.001584 SOL after recorded fees.
- **Executor fixes:** Successful in this run; all eight round trips were coherent and fully notified.
- **Agent policy adherence:** Broadly correct, with one cooldown-source inconsistency.
- **New run format:** A meaningful auditability improvement with a substantial storage cost.
- **Condor stop behavior:** Correct once invoked; all remaining inventory closed.
- **First stop failure:** Most likely in the OpenClaw TUI/MCP harness before Condor invocation; exact root cause is unprovable because the failed attempt was not recorded.
- **Harness quality:** Not fully reliable yet. Tick 31 proves an unrelated model-control acknowledgement can be accepted as a successful autonomous tick.

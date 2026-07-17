# Memecoin Trender Run 3 Postmortem

**Run:** `01KXPDJDW4ZM87R9QY36BDDXX6`  
**Agent:** `memecoin_trender`  
**Window:** 2026-07-16 14:33:22–14:50:55 PDT  
**Duration:** 17m 33s  
**Terminal state:** `stopped` / `manual stop`

## Executive summary

Run 3 was mechanically clean but economically unacceptable. It produced two normal take-profit exits, then gave back more than ten times those combined gains when a same-token re-entry collapsed and the nominal 5% stop exited at **-69.06%**.

- 3 valid round trips; 2 wins and 1 loss.
- Gross PnL: **-0.012449382 SOL**.
- Recorded fees: **0.000057222 SOL**.
- Estimated after-fee PnL: **-0.012506604 SOL**.
- Return on 0.06 SOL cumulative entry notional: **-20.75% after recorded fees**.
- Every executor had a filled entry, filled exit, settlement references, proceeds, fees, and matching entry/exit notifications.
- The critical failure was market/execution risk, not phantom accounting: the third position lost 0.013811683 SOL in about nine seconds despite a configured 5% stop.
- The agent behaved conservatively after the loss and made no further entries, but the 15% run drawdown limit did not prevent the already-open position from realizing a loss near that limit.
- Run 3's recording format differs materially from run 2: tool calls now carry terminal `completed` status and large outputs spill to Markdown artifacts, but exact full per-tick prompts are no longer stored as one artifact per tick.

The strategy should not run again unchanged. Same-token re-entry after a TP needs a cooldown, and entry eligibility needs stronger executable-liquidity and price-impact controls. A stop-loss barrier cannot cap loss in a discontinuous memecoin market.

## Evidence and method

Primary evidence:

- Run stream: `agents/memecoin_trender/runs/01KXPDJDW4ZM87R9QY36BDDXX6.jsonl`
- Run artifacts: `agents/memecoin_trender/runs/2026-07-16_21-33-22Z.artifacts/`
- Executor ledger: `agents/memecoin_trender/executors.jsonl`
- Notifications: `store/notifications.jsonl`
- Frozen strategy/config: `run_started.payload.frozen_spec` in the run stream
- Prior reviews:
  - `docs/memecoin-trender-run-01KXNP3DFJSDYSDZCGW2TZF21V.md`
  - `docs/memecoin-trender-run-01KXNZZGWN2FM3X7N66VBAMWKH.md`

PnL is calculated as executor `proceeds - amount_spent`. Recorded fees are the terminal executor `state.extra.tx_fee`. The after-fee figure subtracts those fees separately. Wallet transactions remain the final source for economic reconciliation.

## Performance scorecard

| Metric | Result |
|---|---:|
| Run duration | 17m 33s |
| Ticks started / completed | 14 / 14 |
| Mean / median tick duration | 72.5s / 60.3s |
| Maximum tick duration | 145.2s |
| Mean tick-start interval | 76.6s |
| Timed-out ticks | 0 |
| Valid round trips | 3 |
| Winners / losers | 2 / 1 |
| Win rate | 66.7% |
| Cumulative entry notional | 0.06 SOL |
| Gross PnL | -0.012449382 SOL |
| Recorded transaction fees | 0.000057222 SOL |
| Estimated after-fee PnL | -0.012506604 SOL |
| Gross return on cumulative notional | -20.75% |
| Estimated after-fee return | -20.84% |
| Failed/phantom executors | 0 |
| Peak open positions | 1 |

A 66.7% win rate is misleading here. The single loss was roughly **10.1 times** the two winners' combined gross profit and **18.4 times** the mean winning trade. Loss magnitude dominated hit rate.

## Trade-level results

| Entry | Token | Exit | Holding time | Gross PnL | Fees | Est. after fees |
|---:|---|---|---:|---:|---:|---:|
| Tick 1 | `4ko5tSr5` | take profit | ~65s | +0.000749599 | 0.000037167 | +0.000712432 SOL |
| Tick 2 | `7QjNfL5J` | take profit | ~392s | +0.000612702 | 0.000010055 | +0.000602647 SOL |
| Tick 9 | `7QjNfL5J` | stop loss | ~9s | -0.013811683 | 0.000010000 | -0.013821683 SOL |

Executor IDs:

- `position_spot_02e8353efb284268ab1820791a6ffe10`
- `position_spot_141c5cfd3ee84d678695cdd8b199e667`
- `position_spot_3797d6e467b74142885376c3958b5528`

All three ended `CLOSED` with nonzero filled entry and exit quantities and transaction references.

## The catastrophic stop-loss exit

The third executor entered `7QjNfL5J` at approximately `3.942366892e-7` SOL per token with 0.02 SOL. About 3.15 seconds after entry, the barrier observed a mark near `1.219830803e-7`, a **-69.06%** move. Settlement completed roughly 5.72 seconds later for only `0.006188317 SOL` proceeds.

Configured versus realized:

| Item | Value |
|---|---:|
| Configured stop loss | -5.0% |
| Trigger/observed PnL | -69.0584% |
| Realized proceeds return | -69.0584% |
| Overshoot beyond intended loss | 64.06 percentage points |
| Intended 5% loss on 0.02 SOL | 0.001 SOL |
| Actual gross loss | 0.013811683 SOL |
| Actual / intended loss | 13.81× |

This was not ordinary one-percent route slippage. The quoted/observable market repriced between barrier checks, or executable liquidity vanished. The executor did what the strategy documentation warns about: a stop is an intent, not a guaranteed fill.

The two `7QjNfL5J` entries also show adverse re-entry selection:

- First entry price: `3.638924173e-7`; profitable exit after about 6.5 minutes.
- Re-entry price: `3.942366892e-7`, about **8.34% above** the first entry.
- The re-entry occurred about 143 seconds after the prior exit and failed almost immediately.

A prior TP proved only that the earlier entry worked. It did not establish that the token remained safely executable. The agent's newly recorded learning correctly recognizes this, but the guard needs to be deterministic rather than left to future model judgment.

## Agent behavior and policy compliance

### What worked

- At most one position was open at a time, below the configured maximum of three.
- Every entry used 0.02 SOL and included TP, SL, TTL, and 1% route-slippage settings.
- The agent used mint addresses rather than symbols.
- It recorded genuinely material outcomes as learnings.
- After the severe stop, ticks 10–14 made no new entries and repeatedly acknowledged only 1.2 percentage points of drawdown headroom.
- It did not misclassify the loss as a normal 5% stop.

### What needs correction

The frozen instructions prohibit re-entry only after a **stop-out**. They allow rapid re-entry after a TP. Run 3 demonstrates that this is insufficient for thin, reflexive markets.

The agent also inferred that the first `7QjNfL5J` TP confirmed a reliable signal, then re-entered the same token. That conclusion was based on one successful observation in the current session and was too strong. The durable learning recorded after the first TP says it “confirms” reliability, even though one trade cannot do that. Tick 9 also says “Both positions closed TP,” although only one position had been open immediately beforehand.

Two pre-existing policy/harness issues remain:

- The frozen prose says a stopped token is excluded for 24 hours, while active `entry_guards.stop_loss_cooldown.hours` is 4. The effective rule should be made singular and explicit.
- The model repeated `ToolSearch` on all 14 ticks even though the prompt asks for it only “at the very start.” This was harmless but wasteful.

Recommended policy changes:

1. Add a deterministic same-token cooldown after **any** exit, not only a stop loss; start with 30–60 minutes.
2. Prohibit same-session re-entry after a TP unless executable depth and route price impact have improved, not merely momentum percentages.
3. Add minimum pool age, holder concentration, and executable depth/price-impact checks for the full exit size.
4. Size positions from credible worst-case loss, not nominal stop distance. If a position can lose 70–100%, 0.02 SOL is the real risk amount.
5. Consider a session stand-down after any single loss above a fixed threshold such as 25% of position notional, regardless of consecutive-stop count.

## Risk-limit behavior

The run's `max_drawdown_pct` was 15%. After the loss, Condor reported run PnL `-0.012449382 SOL` and the prompt represented this as 13.8% drawdown, leaving 1.2% headroom.

This limit did not malfunction in the narrow sense: pre-trade gates cannot prevent a live position from gapping through its stop. But the denominator and semantics deserve scrutiny. The run had only 0.06 SOL cumulative notional and at most 0.02 SOL concurrent exposure, while the displayed drawdown percentage appears based on a larger risk budget. Users should not interpret “15% max drawdown” as “this run cannot lose more than 15% of deployed trade notional.”

Recommended controls:

- Add a hard per-executor realized-loss circuit breaker and an agent-wide stand-down on extreme barrier overshoot.
- Report drawdown denominator explicitly in every risk-state snapshot.
- Surface both nominal barrier risk and full-notional-at-risk.

## Executor and notification integrity

Run 3 passes the mechanical integrity checks that failed in run 1:

- 3 requested executors and 3 economically valid round trips.
- No canceled-entry or zero-fill executor counted as a trade.
- All entry and exit orders were `FILLED`.
- All three had `open_ref`, `close_ref`, proceeds, and recorded fees.
- Exactly six matching trade notifications exist: three entries and three exits.
- Notification PnL matches executor proceeds-minus-entry values.

The catastrophic result is therefore economically real in the ledger, not an aggregation artifact.

One minor schema issue remains: terminal states have `closed_at: null` even though ledger timestamps and `CLOSED` events establish closure. Persisting an explicit terminal timestamp would simplify holding-period and latency analysis.

## Run-storage and harness observations

Run 3 used another recording shape:

- Main JSONL: **247,212 bytes**.
- Referenced large tool-call artifacts: 14 files, **203,311 bytes** total.
- Entire shared artifact directory, including `prompt.md` and `journal.md`: **215,826 bytes**.
- Combined main stream plus directory: **463,038 bytes**.
- All 14 referenced SHA-256 hashes verified.
- Referenced files are mode `0600`; artifact directory is `0700`.

Improvements over run 2:

- Non-spilled tool calls now contain `status: completed`; run 2's 74 calls all remained `pending`.
- Large scan outputs are readable Markdown artifacts rather than opaque JSON payload dumps.
- `tick_started` includes a prompt suffix and `prompt_sha256`, and a shared `prompt.md` preserves the stable prompt body.
- No run-2-style model-switch/control-response contamination appeared.

Tradeoffs/regressions:

- Run 2 persisted one exact full prompt artifact for every tick. Run 3 stores a stable `prompt.md` plus per-tick suffix/hash, so reconstruction must combine multiple artifacts and verify the hash.
- Artifact directories are named by wall-clock start time (`2026-07-16_21-33-22Z.artifacts`) rather than run ID. The content includes the run ID, but run-ID naming would make lookup and collision avoidance clearer.
- Spilled tool-call events omit top-level `name` and `status`; parsers must resolve the artifact instead of assuming those keys exist on every `tool_call` event.

The terminal `manual stop` occurred 13.1 seconds after tick 14 completed, with zero open positions, so it did not alter trade PnL. The run stream records no dedicated `control_requested` event or caller identity. Because the current conversation did not issue a stop before asking for status, and no matching `condor__control_run` call appears in the surviving OpenClaw session record, the provenance of this manual stop cannot be established. It may have come from another control surface or a process-lifecycle path. This repeats the lifecycle-observability gap identified in run 2: record the source, request ID, timestamp, transport result, and applied action for every control request. Since inventory was already flat, run 3 provides no new test of active-position winddown or `closed:false` response semantics.

## Three-run comparison

| Metric | Run 1 | Run 2 | Run 3 |
|---|---:|---:|---:|
| Duration | 2h 8m | 40m 7s | 17m 33s |
| Valid trades | 29 | 8 | 3 |
| Win rate | 41.4% | 75.0% | 66.7% |
| Gross PnL | -0.003239 | +0.002152 | -0.012449 SOL |
| Est. after-fee PnL | -0.004974 | +0.001584 | -0.012507 SOL |
| Phantom/zero-fill completed executors | 1 | 0 | 0 |
| Coherent settlements on valid records | No | Yes | Yes |
| Worst valid exit | -0.002722 | -0.000200 | -0.013812 SOL |
| Extreme barrier overshoot | No | No | Yes |

Across all three audited runs, approximate estimated after-fee PnL is **-0.015897 SOL**. Run 3 alone lost about **7.9 times** run 2's after-fee gain and about **2.5 times** run 1's after-fee loss.

The progression is clear:

- Executor bookkeeping and notification integrity improved after run 1.
- Run 2 showed that the strategy can produce a clean profitable sample.
- Run 3 exposed the dominant tail risk: one discontinuous exit can erase many normal 3% wins.

With a 3% TP and a 69% realized loss, roughly **23 wins of 3% each** are needed to offset one such loss before fees. The current payoff distribution is not viable without reducing gap/liquidity risk.

## Recommended next actions

### Before another live run

1. Add a post-exit same-token cooldown, including TP exits.
2. Add full-size executable-depth and price-impact guards at entry.
3. Add an extreme-loss stand-down independent of the two-consecutive-SL rule.
4. Reduce `amount_quote` until worst-case full-notional losses are acceptable.
5. Make risk UI state the drawdown denominator and full-notional-at-risk explicit.

### Executor/runtime

6. Persist barrier trigger price/time separately from final fill price/time.
7. Persist `closed_at` on terminal executor records.
8. Alert when realized loss exceeds configured stop by a material multiple.
9. Consider faster monitoring only if RPC/quote load and false-trigger behavior remain acceptable; faster polling cannot solve absent liquidity.

### Run/harness auditability

10. Add `control_requested` and `control_applied` events with caller/source metadata.
11. Prefer artifact directories keyed by run ID.
12. Provide a supported resolver that reconstructs and hash-verifies the exact prompt for each tick.
13. Keep terminal tool statuses; this is a material improvement over run 2.

## Final verdict

**Executor integrity: pass. Strategy safety: fail. Statistical validation: fail.**

Run 3 proves that mechanically correct execution is not enough. The two take profits were normal and fully auditable, but a rapid same-token re-entry encountered a discontinuous 69% loss that overwhelmed the entire sample. The agent recognized the event and became conservative afterward, yet recognition after settlement cannot recover the loss.

Do not treat the 5% stop as the economic risk bound. For these tokens, the full 0.02 SOL entry is the credible per-trade risk. Another live validation should happen only after deterministic re-entry and executable-liquidity guards are added, with smaller size.
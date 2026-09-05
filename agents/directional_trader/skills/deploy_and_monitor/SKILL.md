---
name: deploy_and_monitor
description: Phase 4 — deploy the backtest winner live, monitor early performance,
  and compare live results against backtest expectations to detect drift
when_to_use: After backtesting produces a confirmed winner and the user wants to deploy
  it, or when comparing live results against backtest expectations and deciding whether
  to scale, tune, or stop a live directional strategy
created: '2026-07-30T20:11:52Z'
source: agent:directional_trader
---

# Phase 4: Deploy, Monitor and Compare

Deploy the validated config, watch the early trades, then compare live results
against what the backtest said would happen. Output: a decision to scale, hold,
reduce or stop.

## Step 1 — Pre-deploy checklist

Confirm every item with the user:

- [ ] **Winner confirmed** — passed the `backtesting` go/no-go, config exists
      (`manage_controllers(action="describe", config_name="...")`)
- [ ] **Exchange connectivity** — connector configured, API keys set
- [ ] **Leverage matches the backtest** — never deploy at a different leverage than
      was simulated
- [ ] **Position mode** — HEDGE if the strategy goes both long and short:
      `set_account_position_mode_and_leverage(...)`
- [ ] **Stops set** — `stop_loss` and `take_profit` present; never deploy without them
- [ ] **`max_loss_quote`** set as a circuit breaker
- [ ] **Sizing** — ≤ 5% of account for one strategy, and start at 25–50% of the
      intended size; scale only after the comparison in Step 4 passes
- [ ] **`avg_trade_duration_hours` noted** — it sets the minimum monitoring window
- [ ] User is watching the first 3 trades

## Step 2 — Deploy

```python
manage_bots(
    action="deploy",
    bot_name="{strategy_slug}_live_v1",
    controllers_config=["{strategy_slug}_best"],
    account_name="master_account",
    max_global_drawdown_quote=<2 × stop_loss × total_amount>,
    max_controller_drawdown_quote=<1.5 × stop_loss × total_amount>,
)
manage_bots(action="status")   # no bot_name — returns all active bots
```

**Record the deployment timestamp.** It is the start of the live comparison window
and cannot be recovered later.

For a single lightweight controller, an executor may fit better than a bot —
consult the `executor_manager` agent.

## Step 3 — Early monitoring

**Cadence:** every 30 min for the first 4 hours → every 2 hours for the next 24h →
every 6–12 hours until the comparison window is reached.

```python
manage_bots(action="status")
manage_bots(action="logs", bot_name="{strategy_slug}_live_v1", log_type="error")
list_executors(status="RUNNING")
```

**What to check:** is it running, is it trading, and does the trade rate match the
backtest (backtest had N trades in M days → expect ~N/M per day).

**Early warning signals:**
- **No trades** after 2× the expected interval → the signal is not firing; suspect
  a column-name mismatch or a data feed issue
- **Far more trades than backtest** → signal flicker; consider a debounce
- **Executor open longer than 2× `avg_trade_duration_hours`** → check the exit logic
- **Immediate large drawdown** → stop and investigate before continuing

**Abort immediately** on: any unhandled exception in `update_processed_data`;
drawdown exceeding `max_controller_drawdown_quote` within 5 trades; or a signal
firing continuously with no exit (runaway position).

```python
manage_bots(action="stop_bot", bot_name="{strategy_slug}_live_v1")
```

## Step 4 — Live vs backtest comparison

Run once enough live exposure has accumulated: at least 2× the average trade
duration, and enough trades to mean anything (aim for ≥ 10).

Re-run the backtest over the **live window with the exact same config** — do not
change a single parameter. The question is what the backtest *would* have done in
the same period.

```python
manage_routines(
    action="run_async",
    name="backtest_chart",
    config={
        "config_name": "{strategy_slug}_best",
        "start_date": <live_deploy_date>, "end_date": <today>,
        "resolution": "1m", "trade_cost": 0.0006,
    },
)
# → read it back with manage_routines(action="get_instance", name=<instance_id>)
```

```
Live vs Backtest: {strategy_slug}_best | Jul 15 – Jul 30 (15d)
──────────────────────────────────────────────────────
Metric          Live      Backtest   Delta    Flag
Net PnL         +3.2%     +4.8%      -1.6pp   ✅
Trade Count     12        15         -20%     ✅
Win Rate        58.3%     53.3%      +5.0pp   ✅
Max Drawdown    -4.1%     -3.5%      +0.6pp   ✅
Sharpe          0.95      1.18       -0.23    ✅
──────────────────────────────────────────────────────
Verdict: ✅ CONSISTENT — continue live
```

| Metric            | Flag threshold      | What it means                                        |
|-------------------|---------------------|------------------------------------------------------|
| Sharpe delta      | > 0.3               | Regime shift or execution slippage — investigate     |
| PnL delta         | > 30% relative      | Curve fitting, or unmodelled spread/slippage cost    |
| Trade count delta | > 30%               | Signal behaves differently on live data — check feed |
| Max DD delta      | > 2× backtest DD    | Reduce size; check whether the stop fired late       |
| Win rate delta    | > 15pp              | Minor — likely noise if trade count is low           |

## Step 5 — Decision

**✅ CONSISTENT** (all deltas within thresholds)
Scale to full intended size. Move to a daily monitoring cadence. Re-compare weekly
for the first month, then monthly. If live DD is under 50% of backtest DD, consider
tightening stops.

**⚠️ MINOR DRIFT** (1–2 metrics slightly over)
Hold at current size — do **not** scale up. Extend the window by another 1× average
trade duration and re-compare. Widening drift → escalate.

**❌ MAJOR DRIFT** (Sharpe delta > 0.5 or PnL delta > 50%)
Reduce to minimum size or stop. Investigate in order: regime change (compare current
ADX/volatility against the backtest period), execution slippage (fill vs intended
price), data discrepancy (live vs historical candles for the same window), signal
flicker. Then re-sweep parameters over the most recent 30 days via the `backtesting`
playbook. If the re-sweep fails, the hypothesis is dead — back to `research`.

**❌ CRITICAL** (live DD > 2× backtest DD, or loss beyond user tolerance)
Stop immediately with `manage_bots(action="stop_bot", ...)`, close open positions,
write a post-mortem. Do not redeploy without a full Phase 1–3 cycle on a new
hypothesis.

## Step 6 — Ongoing cadence

| Period      | Cadence      | Action                              |
|-------------|--------------|-------------------------------------|
| Week 1–2    | Daily        | Quick metrics check                 |
| Week 3–4    | Every 3 days | Metrics + mini comparison           |
| Month 2+    | Weekly       | Full live vs backtest comparison    |
| Quarterly   | Once         | Full parameter re-sweep on 90 days  |

If Sharpe stays below 0.5 for two consecutive months, trigger a full research
refresh. Log findings with `manage_memory` so the next strategy inherits them.

For automated daily snapshots, write a monitoring routine yourself — read the
`routine_cookbook` playbook first, then create and test it.

## Artifacts

1. Completed pre-deploy checklist
2. Deployment record — bot name, config, timestamp, initial size
3. Early monitoring log (first 48h)
4. Live performance snapshot
5. Backtest-over-live-period results
6. Comparison table with delta flags
7. Decision + action plan

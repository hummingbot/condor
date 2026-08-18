# Out-of-Sample Validation and the Deploy Decision

Companion to the `backtesting` playbook. The last gate before live capital.

## Step 1 — Run the held-out window

Take the sweep winner and run it once over the ~30 days deliberately excluded from
the sweep. Same resolution, same trade cost, `1m` fidelity.

```python
manage_routines(
    action="run_async",
    name="backtest_chart",
    config={
        "config_name": "{strategy_slug}_best",
        "start_date": <held_out_start>, "end_date": <today>,
        "resolution": "1m", "trade_cost": 0.0006,
    },
)
# → instance_id; read it back with manage_routines(action="get_instance", name=...)
```

Never tune anything after seeing this result. The moment you sweep against the
held-out window it stops being out-of-sample and this gate is gone.

## Step 2 — Acceptance criteria

✅ **GO** — all of:

| Criterion              | Threshold                                |
|------------------------|------------------------------------------|
| Sharpe (out-of-sample) | ≥ 0.5                                    |
| Sharpe retention       | ≥ 50% of the in-sample Sharpe            |
| Max drawdown           | ≤ 25% (or the user's stated tolerance)   |
| Profit factor          | ≥ 1.3                                    |
| Win rate               | ≥ 45%                                    |
| Trade count            | ≥ 10 out-of-sample, > 20 in-sample       |
| Overfitting flags      | none from `parameter_sweep.md`           |

❌ **NO-GO** on any of:

- Sharpe < 0.5 even after the sweep → the hypothesis does not work on this
  pair/interval. Back to `research` — do not re-sweep until it passes.
- Sharpe retention < 50% → the parameters fit the in-sample window, not the market
- Max DD > tolerance with no parameter set fixing it → risk profile is wrong
- Trade count < 10 → the signal is too rare to validate here
- The user is uncomfortable with the drawdown profile

A NO-GO is a completed workflow, not a failure. Document what didn't work and why,
save it with `manage_memory`, and return to the phase that owns the problem.

## Step 3 — Document the winner

```
winning_config: {strategy_slug}_best
in_sample_period:     {start} → {end}
out_of_sample_period: {start} → {end}
resolution: 1m   trade_cost: 0.0006
metrics (out-of-sample):
  sharpe: X.XX               max_drawdown_pct: XX.X%
  total_trades: NNN          win_rate: XX%
  profit_factor: X.XX        avg_trade_duration_hours: N.N
in_sample_sharpe: X.XX       retention: XX%
stability_evidence: adjacent params {a}, {b} → Sharpe {x}, {y}
deployment_readiness: YES / NO
```

`avg_trade_duration_hours` is not decoration — `deploy_and_monitor` uses it to size
the live comparison window.

## Step 4 — Hand off

Surface the block above and get explicit user confirmation before deploying.
On GO, continue with the `deploy_and_monitor` playbook.

## Artifacts

1. Baseline report (metrics table)
2. Sweep grid + results matrix per dimension
3. Stability analysis
4. Out-of-sample validation run
5. Winner block above + GO/NO-GO with reasoning

---
name: backtesting
description: The directional-specific half of backtesting — window sizing, the metric
  threshold table, parameter sweeps, and the out-of-sample go/no-go. The tool contract
  and the family-agnostic rules live in the shared `backtest_flow` skill.
when_to_use: After a controller config is uploaded, whenever you run, interpret, sweep,
  or compare backtests. Read this hub first, then pull the companion for the step you
  are on. Never deploy a config that has not passed the go/no-go here.
created: '2026-07-30T20:11:29Z'
source: agent:directional_trader
---

# Backtesting

Take an uploaded config → a named winner with confirmed metrics, ready to deploy.
Read this hub, then fetch the companion for the step you are actually on:

```
manage_skill(action="read_file", name="backtesting", file="interpret_metrics.md")
```

## Which companion to read

| You are…                                                          | Read                       |
|-------------------------------------------------------------------|----------------------------|
| Running anything for the first time this session                   | shared `backtest_flow`     |
| Sizing the window for a controller interval                        | `windows_and_costs.md`     |
| Reading results — thresholds, red flags, how to report them        | `interpret_metrics.md`     |
| Designing or ranking a parameter sweep; checking for overfitting   | `parameter_sweep.md`       |
| Validating out-of-sample and deciding deploy / don't deploy        | `go_no_go.md`              |

A full pass reads `backtest_flow` once, then all four in that order. A one-off
"what does this Sharpe mean?" needs only `interpret_metrics.md`.

## The tool contract — read it once, elsewhere

How to run a backtest is **not** directional knowledge, so it is not duplicated
here. The shared `backtest_flow` skill holds the single copy: the `backtest_chart`
routine, blocking vs `run_async`/`get_instance`, the `table_data` columns,
`chart=False` for sweeps, `task_id` persistence and retention, plus the rules that
hold for every strategy family (trade cost, resolution fidelity, the trade-count
gate, one-parameter sweeps, mandatory out-of-sample).

```
manage_skill(action="read", name="backtest_flow")
```

Read it before your first run of a session. What follows in *this* playbook is the
part that is calibrated to directional trading and does not transfer to other
strategy families.

## The loop

1. **Baseline** — one run over a meaningful window (`windows_and_costs.md`).
2. **Read it** — apply the threshold table; stop early if the signal is noise
   (`interpret_metrics.md`).
3. **Sweep** — one parameter at a time, look for a plateau (`parameter_sweep.md`).
4. **Validate** — held-out window, then decide (`go_no_go.md`).
5. **Document the winner** and hand it to the `deploy_and_monitor` playbook.

## Non-negotiables

The universal ones — one parameter at a time, plateau over peak, mandatory
out-of-sample, the trade-count gate, report the bad numbers too — live in
`backtest_flow` and apply here unchanged. On top of them, for directional:

- **Sharpe < 0 means the signal is actively harmful.** Go back to `research`; do
  not sweep a negative-edge signal until it looks positive.
- **Never deploy a config that has not passed `go_no_go.md`** — a good baseline is
  not a decision.
- **Judge the exit, not just the entry.** An average trade duration far off the
  controller interval means `time_limit` or the stop is driving exits, not the
  signal — a "profitable" config of that shape is not the strategy you think you
  are deploying.

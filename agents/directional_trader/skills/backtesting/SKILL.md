---
name: backtesting
description: The single reference for backtesting a directional controller — windows
  and costs, metric interpretation, parameter sweeps, and the out-of-sample go/no-go.
  Routes to a companion file per topic.
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

| You are…                                                          | Read                     |
|-------------------------------------------------------------------|--------------------------|
| Choosing the window, resolution, or trade cost; launching a run    | `windows_and_costs.md`   |
| Reading results — thresholds, red flags, how to report them        | `interpret_metrics.md`   |
| Designing or ranking a parameter sweep; checking for overfitting   | `parameter_sweep.md`     |
| Validating out-of-sample and deciding deploy / don't deploy        | `go_no_go.md`            |

A full pass reads all four in that order. A one-off "what does this Sharpe mean?"
needs only `interpret_metrics.md`.

## The tool contract

There is exactly **one** way to run a backtest: the shared `backtest_chart`
routine. It runs the backtest, saves it, charts it, and hands back the metrics as
data. Dates are `YYYY-MM-DD` strings, not epoch seconds.

```python
# Blocking — one run you are waiting on interactively.
manage_routines(action="run", name="backtest_chart", config={
    "config_name": ..., "start_date": "2025-04-22", "end_date": "2025-07-22",
    "resolution": "1m", "trade_cost": 0.0002, "chart": False,
})

# Fire and forget — long windows and every sweep. submit → read back.
manage_routines(action="run_async", name="backtest_chart", config={...})  # → instance_id
manage_routines(action="get_instance", name=<instance_id>)
```

Read the numbers from `result.table_data` — one row per run, with `task_id`,
`net_pnl_quote`, `net_pnl_pct`, `sharpe_ratio`, `max_drawdown_pct`, `accuracy_pct`,
`profit_factor`, `total_executors`, `win_signals`/`loss_signals`, `total_fees_quote`,
`total_volume`. N runs concatenate into one table; never parse `result.text`.

Set **`chart=False` for every sweep** — otherwise each run pushes an image into the
user's chat. The chart still reaches the web report; a single run you are presenting
is the one case worth leaving `chart=True`.

Re-render or inspect any past run with `config={"task_id": ...}` — no re-run, no API
call. `backtest_compare` ranks saved runs against each other.

Config variants are created with
`manage_controllers(action="upsert", target="config", config_name=..., config_data={...})`,
adding `confirm_override=True` when overwriting.

## The loop

1. **Baseline** — one run over a meaningful window (`windows_and_costs.md`).
2. **Read it** — apply the threshold table; stop early if the signal is noise
   (`interpret_metrics.md`).
3. **Sweep** — one parameter at a time, look for a plateau (`parameter_sweep.md`).
4. **Validate** — held-out window, then decide (`go_no_go.md`).
5. **Document the winner** and hand it to the `deploy_and_monitor` playbook.

## Non-negotiables

- **Never sweep every parameter at once** — that is curve fitting with extra steps.
- **Stability beats the peak.** An isolated Sharpe spike surrounded by collapse is
  overfit; a plateau is a result.
- **Never deploy on in-sample numbers alone** — the held-out run is mandatory.
- Trade count is a validity gate, not a statistic: under ~20 trades, no metric in
  the report means anything.
- Report the numbers you actually got, including the bad ones. A NO-GO is a
  successful outcome of this workflow.

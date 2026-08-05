# Parameter Sweeps

Companion to the `backtesting` playbook. Only start once the baseline passed the
go/no-go in `interpret_metrics.md`.

## Grid design

- **3–5 values per parameter**, centred on the baseline value
- Include both a tighter and a looser variant
- Steps must be meaningful — `10, 20, 30`, never `20, 21, 22`
- One dimension at a time; a full cross-product is overfitting with a bigger bill

**Sweep order** (each stage inherits the previous winner):

1. Core indicator period(s) — `ema_fast`, `rsi_period`
2. Entry filter thresholds — `adx_threshold`, `rsi_oversold`
3. Risk parameters — `stop_loss`, `take_profit`

```
ema_fast:      [10, 15, 20, 30, 40]     (baseline 20)
ema_slow:      [30, 50, 75, 100, 150]   (baseline 50)
adx_threshold: [15, 20, 25, 30, 35]     (baseline 25)
```

Sweep every `ema_fast` value first, fix the winner, then sweep `ema_slow`, then
`adx_threshold`.

## Execution

For each variant: create the config, then submit the backtest. Submit the whole
stage, then read the instances back — do not run them one at a time.

```python
manage_controllers(
    action="upsert",
    target="config",
    config_name="{strategy_slug}_sweep_{param}_{value}",
    config_data={**base_config, param: value},
)
manage_routines(
    action="run_async",
    name="backtest_chart",
    config={
        "config_name": "{strategy_slug}_sweep_{param}_{value}",
        "start_date": start_date, "end_date": end_date,
        "resolution": "1m", "trade_cost": 0.0006,
        "chart": False,          # a sweep must not push an image per variant
    },
)
# → instance_id; collect with manage_routines(action="get_instance", name=<instance_id>)
```

`chart=False` is not optional here: without it a 15-variant sweep pushes 15 images
into the user's chat. The charts stay reachable in the dashboard by `task_id`.

Hold window, resolution and trade cost **constant** across the whole sweep.

Build the ranking table below straight from each run's `result.table_data` row —
`sharpe_ratio`, `max_drawdown_pct`, `net_pnl_pct`, `total_executors`. Never read
the numbers out of `result.text`.

## Ranking — stability over peak

```
Param Sweep: ema_fast (ema_slow=50, adx_threshold=25)
──────────────────────────────────────────────────
ema_fast  Sharpe  MaxDD   PnL%   Trades  WinRate
10        0.82    -12.1%  +6.3%  142     48.6%
15        1.15    -9.2%   +10.1%  98     52.0%
20        1.24    -8.3%   +12.4%  67     54.2%   ← baseline
30        1.18    -7.9%   +11.8%  43     55.8%
40        0.91    -6.5%   +8.2%   28     57.1%
──────────────────────────────────────────────────
Best: ema_fast=20 (Sharpe 1.24)
Stability: ✅ adjacent values (15, 30) also > 1.0
```

**The rule:** pick the value where Sharpe is high **and** both adjacent values stay
within ~20% of it. A plateau is a result. A peak surrounded by cliffs is an
artifact of this particular window — discard it and take the best plateau instead,
even if its headline Sharpe is lower.

## Overfitting red flags

- Sharpe drops > 50% at an adjacent parameter value → fragile, not robust
- The best value sits at the edge of the grid → extend the grid; the real peak is
  outside it and you are reading a boundary
- Best variant has < 20 trades → the sample cannot support the ranking
- Suspiciously high Sharpe (> 3.0) → assume a bug or a look-ahead leak in the
  signal until proven otherwise; check `.shift()` usage in `update_processed_data`

## Output

Carry forward to `go_no_go.md`: the winning parameter set, its in-sample metrics,
and the stability evidence (the adjacent-value rows) that justified the pick.

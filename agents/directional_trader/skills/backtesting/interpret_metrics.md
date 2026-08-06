# Interpreting Backtest Metrics

Companion to the `backtesting` playbook.

## Metrics to extract

- `net_pnl_quote` / net PnL % — total profit
- `sharpe_ratio` — risk-adjusted return
- `max_drawdown_pct` — peak-to-trough
- `total_trades` — the validity gate
- `win_rate` — % profitable
- `profit_factor` — gross profit / gross loss
- `avg_trade_duration_hours` — sets the live monitoring window later

## Threshold table

| Metric             | Acceptable      | Good   | Excellent | Red flag        |
|--------------------|-----------------|--------|-----------|-----------------|
| Sharpe ratio       | > 0.5           | > 1.0  | > 2.0     | < 0             |
| Max drawdown       | < 20%           | < 10%  | < 5%      | > 30%           |
| Net PnL %          | > 0%            | > 5%   | > 15%     | < -5%           |
| Win rate           | > 40%           | > 50%  | > 60%     | < 30%           |
| Trade count        | > 20            | > 50   | > 100     | < 10            |
| Profit factor      | > 1.0           | > 1.5  | > 2.0     | < 0.8           |
| Avg trade duration | Matches intent  | —      | —         | Far off interval|

## Interpretation rules

- **Sharpe < 0** → the signal logic is actively harmful. Go back to `research`;
  do not sweep a negative-edge signal into looking positive.
- **Trade count < 10** → no verdict is possible. Widen the window or loosen filters
  and re-run before reading anything else.
- **Max DD > 30%** → risk management is too loose. Tighten `stop_loss` or reduce
  leverage before sweeping the signal parameters.
- **Win rate < 30% but profit factor > 1.5** → acceptable. Few large winners carry
  the strategy; make sure the user expects that shape.
- **Win rate > 70% but profit factor < 1.2** → dangerous. Many small wins hiding
  rare catastrophic losses; inspect the largest losing trade before continuing.
- **Avg trade duration far off the interval** → the exits are being driven by
  `time_limit` or the stop, not by the signal. Check which.

## Baseline go/no-go

Before spending any time on a sweep:

- `total_trades` < 30 → widen the window or drop to a finer interval
- `sharpe_ratio` < 0.5 **and** `profit_factor` < 1.2 → the signal is noise; back to research
- `max_drawdown_pct` > 40% → fix sizing/stops first, then re-baseline

## Report format

```
Baseline: ema_cross_adx_v1 | 90d | BTC-USDT 1h | res 1m | cost 0.0006
──────────────────────────────────────────────
Sharpe:        1.24      Max DD:        -8.3%
Net PnL:      +12.4%     Win Rate:      54.2%
Trades:        67        Avg Duration:  14.2h
Profit Factor: 1.89
──────────────────────────────────────────────
Verdict: ✅ GO for parameter sweep
```

Always print the window, resolution and trade cost in the header — a metric
without them cannot be compared against the next run.

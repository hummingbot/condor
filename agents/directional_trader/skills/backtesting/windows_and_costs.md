# Windows, Resolution and Trade Cost

Companion to the `backtesting` playbook — read it for the tool contract.

## Window selection

**Minimum by interval** (below this the trade count is never valid):

| Controller interval | Minimum window | Preferred      |
|---------------------|----------------|----------------|
| 15m                 | 7 days         | 30–60 days     |
| 1h                  | 30 days        | 90–180 days    |
| 1d                  | 90 days        | 180–365 days   |

**Rules:**
- Prefer 3–6 months covering at least one full regime cycle (trend *and* range).
- Avoid a window that is entirely one regime — a pure bull run just fits the trend.
- Include recent data (end within the last 7 days) so the result is relevant.
- Hold out the most recent ~30 days from the sweep; `go_no_go.md` needs it clean.

```python
import time
end_time = int(time.time())
start_time = end_time - (90 * 86400)   # 90 days
```

## Resolution

`backtesting_resolution` is the granularity the engine simulates fills at, not the
controller's candle interval.

- `1m` — maximum fidelity, slowest. Use it for the baseline and the final
  validation run.
- Match the controller interval (`15m`, `1h`) for speed during a wide sweep, then
  re-run the winner at `1m` before deploying.
- A winner that only survives at coarse resolution is not a winner — the gap is
  usually intrabar stop/TP ordering.

## Trade cost

`trade_cost` is a decimal fraction of notional.

| Setting  | Meaning                                                    |
|----------|------------------------------------------------------------|
| `0.0002` | Tool default — roughly a maker leg                         |
| `0.0006` | **Recommended default** — conservative taker round-trip     |

Per-exchange reference (one leg):

| Exchange | Maker    | Taker     |
|----------|----------|-----------|
| Binance  | 0.0002   | 0.0004    |
| Bybit    | 0.0002   | 0.00055   |

Use the taker rate unless the controller is provably passive. Understating cost is
the single easiest way to manufacture a profitable backtest that loses money live —
if the edge disappears between `0.0002` and `0.0006`, there was no edge.

## Blocking vs fire-and-forget

Both are the same routine — the difference is only whether you wait.

- **`action="run"`** — one short run you are waiting on interactively. It gives up
  after ~2 minutes and hands you the `instance_id` to read later; the run itself
  keeps going.
- **`action="run_async"`** → **`action="get_instance"`** — long windows and *every*
  sweep. Submit the whole grid, then read the instances back; do not serialize a
  15-variant sweep through blocking calls.

`get_instance` returns the finished run in full — the metrics row in `table_data`,
the summary in `text`, the error text on failure, and the status otherwise. You
never need to re-run a window just to see its numbers.

### Every run is saved

The routine stores each completed backtest under its `task_id`, whoever ran it and
from wherever. That is what makes the rest possible:

- re-render a past run with `backtest_chart` `config={"task_id": ...}` — no re-run;
- rank past runs against each other with `backtest_compare`;
- a run from the chat, the dashboard or an agent is one record, not three.

The `task_id` is in the metrics row and in the summary text. Record it.

### Result retention

The Hummingbot API archives finished results to `bots/data/backtests/{task_id}.json.gz`;
only their metrics stay resident, and reads rehydrate the full payload from disk.

- Retention is a **count of results** — 100 by default, via `BACKTESTING_MAX_RESULTS`
  (`BACKTESTING_RESULTS_PATH` sets the directory).
- Results **survive an API restart** — an index file restores finished tasks on
  startup.
- Condor's own copy is independent of that reaping, so a run you saved stays
  renderable and comparable after the API has dropped it.

Record for every run: `config_name`, window, resolution, trade cost. A metric
without its window and cost is not comparable to anything.

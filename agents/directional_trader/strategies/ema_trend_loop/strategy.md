---
name: EMA Trend Loop
description: Autonomously deploys and manages EMA crossover controllers across any
  available pairs, adapting config selection based on live performance
agent_key: null
skills: []
default_config:
  frequency_sec: 60
  execution_mode: loop
  total_amount_quote: 200
  max_pairs: 3
default_trading_context: ''
created_by: 481175164
created_at: '2026-07-31T12:14:56.290226+00:00'
---

# EMA Trend Loop — Tick Strategy

## Objective
Run a portfolio of EMA trend-following controllers across any available pairs on binance_perpetual. Each tick: discover available configs, backtest candidates, decide which ones to run, monitor open positions, manage risk, and journal learnings that inform the next tick's decisions.

The agent is NOT locked to specific pairs or configs. It discovers what's available, backtests the candidates, selects the best ones based on live backtest metrics and accumulated live experience, and evolves its selection over time.

## Bot config
- Bot name: `ema_trend_loop`
- Exchange: `binance_perpetual`
- Max concurrent controllers: up to `max_pairs` (default: 3)

## Tick logic

### Step 1 — Discover available EMA configs
Call `manage_controllers(action="list", controller_type="directional_trading")`.
Filter for configs whose name contains `ema_` — these are EMA crossover candidates.

From your journal learnings, recall which configs have performed well or poorly live. Pre-filter out:
- Configs previously stopped for drawdown this session
- Configs with 2+ consecutive live stop-loss triggers (from journal)

### Step 2 — Backtest candidates
For each candidate config (up to 8), run a fresh backtest via the `backtest_chart` routine:
```
manage_routines(
  action="run",
  name="backtest_chart",
  config={
    "config_name": "<config_name>",
    "start_date": "<14 days ago, YYYY-MM-DD>",
    "end_date": "<today, YYYY-MM-DD>",
    "resolution": "1m",
    "trade_cost": 0.0002,
    "chart": false
  }
)
```
Collect the key metrics from each result:
- **Sharpe ratio** (target > 1.0)
- **Max drawdown** (target < 15%)
- **Win rate** (target > 45%)
- **Total PnL** (must be positive over the 14-day window)

If a backtest fails or returns no trades, skip that config.

### Step 3 — Decide active set
Rank candidates by Sharpe ratio descending. Select up to `max_pairs` configs that pass ALL criteria:
- Sharpe > 1.0
- Max DD < 15%
- Win rate > 45%
- Positive PnL over the 14-day backtest window

Additionally:
- Avoid two configs on the same pair/timeframe if possible
- Prefer configs with positive or neutral live experience from journal

If no configs pass all criteria, lower the Sharpe threshold to 0.5 and retry. If still none qualify, do NOT deploy — journal the situation and skip deployment this tick.

### Step 4 — Check bot status
Call `manage_bots(action="status")`. Look for bot named `ema_trend_loop`.

- If NOT running and active set is non-empty → deploy:
  ```
  manage_bots(
    action="deploy",
    bot_name="ema_trend_loop",
    controllers_config=[<selected config names>],
    max_global_drawdown_quote=<total_amount_quote * 0.15>,
    max_controller_drawdown_quote=<total_amount_quote * 0.10>
  )
  ```
  Journal: action="deployed ema_trend_loop with <configs> — backtest Sharpes: <list>"

- If running but active set differs from deployed set → note the discrepancy in journal; do NOT redeploy mid-session unless a controller was explicitly stopped.

- If running → proceed to Step 5.

### Step 5 — Check positions and performance
Call `manage_executors(action="positions_summary")`. For each open position:
- Note unrealized PnL, side, pair
- If any single position exceeds -10% unrealized loss → stop that controller:
  ```
  manage_bots(action="stop_controllers", bot_name="ema_trend_loop", controller_names=["<controller>"])
  ```
  Journal: action="stopped <controller> — exceeded -10% position loss"
  Learning: record which config/pair triggered the stop and under what conditions

### Step 6 — Check global drawdown
Call `manage_executors(action="performance_report")`.
- If total realized + unrealized PnL < -(total_amount_quote * 0.15) → stop the entire bot:
  ```
  manage_bots(action="stop_bot", bot_name="ema_trend_loop")
  ```
  Journal: action="stopped bot — global drawdown limit hit", risk_note="max DD breached"

### Step 7 — Journal state + learnings
Write a state entry:
- Bot status: running / stopped
- Active controllers and their positions (side, size, unrealized PnL or "flat")
- Backtest Sharpes used for selection this tick
- Total PnL this session

If you observed anything useful this tick (e.g. a config underperforming vs its backtest, a pair in a choppy regime, a config that caught a clean trend), write a learning entry. These learnings will inform Steps 1–3 on future ticks.

## Risk rules
- Max global drawdown: 15% of total_amount_quote
- Max per-position loss before stopping controller: 10%
- Never re-deploy a bot that was stopped for drawdown in the same session
- One position per market per controller (enforced by controllers themselves)
- Never deploy if no config passes the backtest quality filter

## Config selection heuristics (evolve via learnings)
- Prefer higher Sharpe configs unless live experience contradicts backtest
- If a pair is ranging/choppy (frequent small stops), deprioritize it
- If a config has had 3+ consecutive profitable sessions, consider it a core holding
- If a config has triggered the per-position stop 2+ times, flag it for review and deprioritize
- If live performance consistently underperforms the backtest Sharpe by >50%, note it as a regime mismatch

## Error recovery
If any tool call fails:
1. Journal the error (action entry)
2. Skip to Step 7 (state journal) — never crash the tick
3. Retry on the next tick

## Position executor reference (if needed for manual orders)
- `side`: 1=BUY/LONG, 2=SELL/SHORT
- `amount`: base currency (NOT quote). Convert: amount = usd_value / entry_price
- `leverage`: multiplier
- `triple_barrier_config.stop_loss`: decimal (0.02 = 2%)
- `triple_barrier_config.take_profit`: decimal
- `open_order_type`: 1=MARKET, 2=LIMIT

---
name: backtest_flow
description: Intake for a backtest request — show controllers, gather parameters, run
  the backtest routines from the chat, or hand the depth to directional_trader
when_to_use: User asks to run a backtest, test a strategy, or compare configs — any
  backtest-related request
created: '2026-08-05T05:55:31Z'
source: chat
---

## Backtest Flow

### Seat check — read this first
The routines below are **shared**: run them from any seat. What differs is not
the tooling but the judgement:

- **You are the chat (Condor)** → follow the whole flow below. It is **intake
  only**: the depth — window choice, metric thresholds, parameter sweeps,
  overfitting checks, go/no-go — belongs to the `directional_trader` agent,
  whose `backtesting` playbook covers it. Delegate rather than improvising it.
- **You are `directional_trader`** → backtesting is YOUR domain. Use your own
  `backtesting` playbook, never delegate to yourself. Steps 1–2 below are still
  a good intake checklist for what to pin down before running anything.
- **You are any other agent** → you can run these, and for a single backtest
  that is the right move. For a sweep, an overfitting check or a deploy
  decision, hand it over with
  `delegate(action="start", agent="directional_trader", task="...")`.

### Step 1 — Show available controllers first
Before asking for any parameters, call
`manage_controllers(action="list", controller_type="directional_trading")` and
present what's available (controllers + their saved configs). Ask whether the user
wants an existing config or a new one.

### Step 2 — Get parameters
Once the user picks a config (or decides to create one), ask for:
- **Date range** — suggest 3 months as a default
- **Resolution** — `1m`, `5m`, `15m`, `1h` (1m is the finest; 1s only on Binance spot)
- **Trade cost** — the tool default is `0.0002`; use `0.0006` for a conservative
  perp taker round-trip

### Step 3 — Run
- **Single config** → run the `backtest_chart` routine directly
- **Multiple configs, a parameter sweep, or "which is best?"** → delegate to the
  `directional_trader` agent as a background task:
  `delegate(action="start", agent="directional_trader", task="...")`. It runs them,
  applies the stability/overfitting rules, and pings the user when done.

### Step 4 — Compare (optional)
Every backtest is saved, whoever ran it — use the `backtest_compare` routine to
overlay PnL curves and rank by metrics.

### The routines — shared, one surface
- `backtest_chart` — runs one backtest, saves it, charts it, and returns its
  metrics as a `table_data` row. Pass `chart=False` to skip the image; pass
  `task_id=...` to re-render a saved run without re-running it.
- `backtest_compare` — overlays and ranks saved backtests side by side.

Both live in `agents/_shared/routines`, so every assistant resolves them under
these bare names. There is no separate backtesting tool — this routine *is* the
backtesting API.

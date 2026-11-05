---
name: Trend Position Loop
description: One LONG or SHORT position on one pair, opened and closed on a
  rule (by default EMA 20/50, RSI and ATR). With NACE_API_KEY set, Drex answers
  each tick and the model runs only when there is something to do.
agent_key: null
skills: []
default_config:
  connector_name: binance_perpetual
  trading_pair: BTC-USDT
  # One tick per 1h candle, the interval the rule reads.
  frequency_sec: 3600
  execution_mode: loop
  total_amount_quote: 100
  risk_limits:
    max_position_size_quote: 100
    max_open_executors: 1
  # Before each tick the engine asks Drex hold, start or stop for this side,
  # and on hold the model is not run. Without NACE_API_KEY, or when Drex
  # fails, the model runs every tick and applies the same rule itself.
  # start_when / stop_when replace the default EMA/RSI/ATR rule with your own,
  # in words, over the snapshot's fields, e.g.
  # start_when: "RSI 14 below 30 and close more than 2 ATR below EMA 20."
  drex_gate:
    side: LONG
    interval: 1h
default_trading_context: ''
created_by: 0
created_at: '2026-11-05T00:00:00+00:00'
---

# Trend Position Loop — Tick Playbook

One position at a time: `drex_gate.side` on `trading_pair` (`connector_name`),
sized `total_amount_quote` in quote. Nothing else trades on this account,
connector and pair.

## What you get each tick

`[CORE DATA - drex_gate]` holds the snapshot (close, EMA 20/50, RSI 14, ATR 14,
and the open PnL in ATR when a position is open) and one of:

- `Drex (...): START` or `Drex (...): STOP`, with probabilities. Drex has applied
  the rule; carry it out.
- `Decide from the snapshot:` and the rule. Drex gave no answer this tick (no
  `NACE_API_KEY`, or the request failed). Apply the rule yourself.
- `Drex was not asked:` the book is neither empty nor one filled position of
  this side on this pair (an order still filling, a second executor, another
  pair). Read the executors and decide yourself.

On a Drex HOLD the engine journals the answer and skips your turn (a dry run
shows it to you instead). The position's own stop-loss is what protects it
between turns.

## Acting

- **START** (flat): read the `directional_position` skill, then
  `create_position_executor` with `side` 1 for LONG or 2 for SHORT, `amount` =
  `total_amount_quote` / price (base currency), and `stop_loss` at 2 ATR from
  entry as a fraction (`2 * atr_14 / close`). No take-profit: the STOP rule exits.
- **STOP** (open): `stop_executor` with the open executor's id.
- **HOLD**: do nothing.

Journal one line: the action, and whether Drex or you decided it.

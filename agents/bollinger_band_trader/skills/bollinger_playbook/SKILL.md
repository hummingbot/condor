---
name: bollinger_playbook
description: End-to-end procedure for trading a Bollinger Band setup — classify the volatility
  regime, pick the matching setup, derive levels from the bands, size it, deploy the position
  executor, and manage the exit.
when_to_use: When you need to act on a Bollinger reading rather than just describe it — a
  squeeze fired, price is walking a band, or a band touch needs to be faded or followed.
  Also read it before any deploy, because the sizing and veto gates live here.
created: '2026-07-29T00:00:00+00:00'
source: agent:bollinger_band_trader
references_routine: band_state
---

# Bollinger Playbook

The procedure from "the bands look interesting" to a live, sized, managed position.
Follow it in order. Each gate exists because skipping it is a known way to lose money.

## Step 1 — Classify before you look at price

Run the state read:

```
manage_trading_agent(action="run_routine", strategy_id="bollinger_band_trader",
                     name="band_state",
                     config={"trading_pair": "<pair>", "connector_name": "<connector>"})
```

Read `verdict` on the entry timeframe and on the trend timeframe. **Do not read `%B`
first.** %B without the regime is the classic trap: 0.98 is a sell in a range and a buy
in an expansion, and nothing about the number itself tells you which.

If `setup` is `no_trade`, stop and report the reason. The routine already applied the
R:R floor, the volume filter, and the higher-timeframe veto.

## Step 2 — Match the setup to its rules

| `setup` | Entry | Stop | Target | Manage |
|---|---|---|---|---|
| `squeeze_pending` | none yet | — | — | Watch `long_trigger` / `short_trigger`. Re-run each candle close. |
| `squeeze_breakout_long/short` | Close beyond the band | Middle band | 2R, then trail the middle band | Exit fully if price closes back inside the band within 2 candles |
| `band_walk_long/short` | Pullback to the middle band that holds | 1 ATR past the middle band | Opposite band | Exit when %B crosses back through 0.5 |
| `reversion_long/short` | At the band | 0.5 ATR beyond the band | Middle band | Exit at the mean; do not hold for the opposite band |
| `failed_breakout_long/short` | On the close back inside | Beyond the failure extreme | Middle band, then the opposite band | Tighten to breakeven once the middle band is reached |

**The veto that matters most:** never take `reversion_*` when the trend timeframe reads
`band_walk_*` or `expansion_*` in the opposing direction. `band_state` already blocks
this, but if you are reasoning from a manual reading, apply it yourself.

## Step 3 — Size it (mandatory)

```
manage_trading_agent(action="run_routine", strategy_id="bollinger_band_trader",
                     name="band_trade_sizer",
                     config={"trading_pair": "<pair>", "connector_name": "<connector>",
                             "side": "long", "entry_price": <entry>, "stop_price": <stop>,
                             "target_price": <target>, "risk_pct": 0.5, "leverage": 1})
```

The routine returns `verdict: PASS|FAIL`, the base-currency `amount`, and a ready
`position_executor` payload. **A `FAIL` is final** — report `blocked_by` and stop. Do not
hand-adjust the size to get around a failed guardrail; the guardrails are the reason the
strategy survives a losing streak.

Read the `WARN` rows too. `Size capped` means the risk-derived size was reduced by the
position cap or the reserve, so the trade will risk less than the target — that is fine,
but say so.

## Step 4 — Deploy

Use the payload exactly as returned:

```
manage_executors(action="create", executor_type="position_executor", config=<payload>)
```

If the create fails, re-fetch the schema with `manage_executors(executor_type="position_executor")`,
fix the payload against the returned fields, and retry **once**. Journal the error either
way. Never retry a failed create more than once in the same tick — a schema error will
not fix itself, and a rejected order usually means a balance or precision problem that
another attempt will hit again.

## Step 5 — Manage the position

Re-run `band_state` on the same cadence you deployed at, and act on the transition, not
on the level:

- **Squeeze breakout that closes back inside the band within 2 candles** → the break
  failed. Close it. Do not wait for the stop.
- **Band walk where %B crosses back through 0.5** → the walk is over. Close it.
- **Reversion trade that reaches the middle band** → take it. Holding for the opposite
  band converts a 1.5R winner into a coin flip.
- **Bandwidth collapsing back to a squeeze while in a position** → volatility is leaving.
  Tighten to breakeven; there is no move left to capture.

## Step 6 — Record what happened

```
manage_memory(action="add", content="<pair> <setup> resolved <outcome> — <what the bands did>")
```

Write down the bandwidth rank at entry and how the setup resolved. Over time this is what
tells you whether `squeeze_rank=20` is the right threshold for this operator's markets or
whether it needs to be 10.

## Finding candidates

When there is no specific pair in question, screen first:

```
manage_trading_agent(action="run_routine", strategy_id="bollinger_band_trader",
                     name="squeeze_screener",
                     config={"trading_pairs": "BTC-USDT,ETH-USDT,SOL-USDT", "interval": "1h"})
```

Then run `band_state` on the top candidates. A screener row is never a trade on its own —
a squeeze has no direction.

## Non-negotiables

- Classify the regime before reading %B.
- Never fade a band walk.
- Never deploy without `band_trade_sizer` returning `PASS`.
- Never widen a stop. The middle band moves on its own; that is the only stop movement
  allowed, and only in your favor.
- Max 3 concurrent Bollinger positions, and never two same-direction majors at once.

This playbook is advisory text. Deploying an executor still goes through the normal
risk and confirmation controls — following these steps is not a bypass.

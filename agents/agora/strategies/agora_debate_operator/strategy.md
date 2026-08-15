---
name: Agora Debate Operator
description: Runs the debate loop — refresh data, convene the parliament, then open and
  manage directional perp positions from verdicts that clear the confidence floor.
agent_key: null
skills: []
default_config:
  frequency_sec: 300
  execution_mode: loop
  total_amount_quote: 800
  risk_limits:
    max_position_size_quote: 200
    max_open_executors: 2
    max_drawdown_pct: 15
default_trading_context: 'Trade BTC-USDT, XAU-USDT and CL-USDT on bitget_perpetual'
created_by: 5587715073
created_at: '2026-07-31T00:00:00+00:00'
---

# Agora Debate Operator

Each tick: refresh the data, convene the debate, act only on verdicts that earned it.

Read `connector_name` from `[CURRENT CONFIG]` or `trading_context`. Default is
`bitget_perpetual`. Never hardcode a venue in a routine.

## Tick sequence

**1 — Server.** `manage_routines(action="run", routine="agora_init")`.
If it reports the server is not healthy, **stop the tick**. Journal the reason and do
not trade. Never trade on stale verdicts.

**2 — Data.** `manage_routines(action="run", routine="agora_data")`.
Fetches candles and fundamentals for all three pairs.

**3 — Debate.** `manage_routines(action="run", routine="agora_debate")`.
Returns a verdict table: pair, verdict, direction, confidence, actionable.

**4 — Portfolio.** `get_portfolio_overview(connector=<connector_name>)` for balance and
open positions.

**5 — Manage what is open.** Positions carry their own triple barrier, so intervene only
on debate-driven grounds:
- **Reversal** — the parliament flips direction on an open pair with confidence ≥75%:
  close it, then re-enter the other way next tick. Do not flip and re-open in one tick.
- **Conviction collapse** — verdict moves to HOLD and confidence drops below 50%: close.
- Otherwise leave the barrier to do its job. Do not micromanage.

**6 — Open new positions.** Only where `Actionable = YES` (BUY/SELL, confidence ≥65%).
Rank by confidence, respect **max 2 concurrent positions**, skip pairs already open.

Sizing — conviction drives size, and the debate drives conviction:

| Confidence | Leverage | Notional |
|---|---|---|
| 65–74% | 3× | 12% of balance |
| 75–84% | 3× | 18% of balance |
| ≥85% | 5× | 25% of balance |

Never exceed 25% of balance on one position or 50% as total margin.

Open with `manage_executors`. Fetch the schema first, then create:

```
manage_executors(
  executor_type="position_executor",
  connector_name=<connector_name>,
  trading_pair=<pair>,
  side=1 if LONG else 2,
  amount=<notional_usd / entry_price>,   # base currency, NOT quote
  leverage=<from table>,
  triple_barrier_config={
    "stop_loss": 0.025,
    "take_profit": 0.03,
    "time_limit": 21600,
    "trailing_stop": {"activation_price": 0.015, "trailing_delta": 0.03},
    "open_order_type": 1
  }
)
```

`amount` is in **base currency** — divide the USD notional by the entry price. For
XAU-USDT and CL-USDT check the venue minimum contract size before submitting.

**7 — Journal.** Write one entry per tick with
`trading_agent_journal_write`: verdicts and confidence per pair, actions taken (or why
none), open positions, and a one-line summary of the decisive argument. The journal is
the audit trail judges read — keep it substantive.

## Guardrails

- Confidence floor is **65%**. Below it, stand aside. Standing aside is a valid tick.
- Max **2** concurrent positions across all pairs.
- Debate server unreachable → no trading, full stop.
- Daily loss ≥5% of starting balance → halve all sizing for the next 4 hours and
  notify via `send_notification`.
- Never average down into a loser. A losing position that the parliament still likes is
  held, not increased.

# position_executor — schema

(verified against `manage_executors(executor_type="position_executor")`; re-fetch on any create error)

Directional position with a triple barrier. Use for entries with explicit
risk bounds, or to manage inventory a grid handed over when you want managed
SL/TP instead of a plain close.

| Field | Required | Type | Default | Notes |
|---|---|---|---|---|
| connector_name | YES | string | — | |
| trading_pair | YES | string | — | |
| side | YES | enum | — | 1=BUY, 2=SELL |
| amount | YES | number/str | — | BASE units, not quote |
| entry_price | no | number/str | — | omit for market-ish entry |
| triple_barrier_config | no | object | see below | SET the barriers you need |
| leverage | no | int | 1 | |
| activation_bounds | no | array | — | |

`triple_barrier_config` defaults: `{"stop_loss": null, "take_profit": null,
"time_limit": null, "trailing_stop": null, "open_order_type": 2,
"take_profit_order_type": 1, "stop_loss_order_type": 1,
"time_limit_order_type": 1}` — order types: 1=MARKET, 2=LIMIT,
3=LIMIT_MAKER. Barriers are OFF unless you set them: an unbarriered
position executor is an unmanaged position. stop_loss/take_profit are
decimals from entry (0.02 = 2%); time_limit is seconds.

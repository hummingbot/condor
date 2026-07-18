# grid_executor — schema + mechanics

(verified against `manage_executors(executor_type="grid_executor")`; re-fetch on any create error)

## Config schema

| Field | Required | Type | Default | Notes |
|---|---|---|---|---|
| connector_name | YES | string | — | |
| trading_pair | YES | string | — | |
| side | no (SET IT) | enum | 1 | 1=BUY grid, 2=SELL grid — limit_price does NOT set direction |
| start_price | YES | number/str | — | lower band boundary |
| end_price | YES | number/str | — | upper band boundary |
| limit_price | YES | number/str | — | safety stop: LONG below start, SHORT above end |
| total_amount_quote | YES | number/str | — | grid capital |
| triple_barrier_config | YES | object | — | take_profit + order types (see below) |
| min_spread_between_orders | no | number/str | 0.0005 | level spacing (decimal) |
| min_order_amount_quote | no | number/str | 5 | per-order size floor → level count |
| max_open_orders | no | int | 5 | concurrent order cap |
| max_orders_per_batch | no | int | — | batch size |
| order_frequency | no | int | 0 | seconds between batches |
| activation_bounds | no | number/str | — | only rest orders within this % of price |
| safe_extra_spread | no | number/str | 0.0001 | extra edge on placement |
| leverage | no | int | 20 | override low for MM (see agent risk rules) |
| keep_position | no | bool | false | set true to HAND OVER inventory on stop |
| coerce_tp_to_step | no | bool | false | TP ≥ grid step; usually true |
| deduct_base_fees | no | bool | false | leave default |

`triple_barrier_config` for grids: `{"take_profit": <decimal>,
"open_order_type": 3, "take_profit_order_type": 3}` (3 = LIMIT_MAKER).
There is NO stop_loss on a grid — limit_price + keep_position is the risk
mechanism.

## Direction geometry (create fails or misbehaves if wrong)

- LONG grid (side=1): `limit_price < start_price < end_price`
- SHORT grid (side=2): `start_price < end_price < limit_price`

## Density math

- max levels from capital = `total_amount_quote / min_order_amount_quote`
- max levels from spacing = `band_width / (min_spread_between_orders × mid)`
- actual levels = min of the two. Size for 8–15 levels; floor spacing so each
  completed pair clears two maker fees (see the fee floor in SKILL.md).

## Behavior notes

- Each filled level places its opposite TP at take_profit distance; TP for
  levels above current price computes from the theoretical level price.
- Price exits the band on the profit side → all quote realized, grid idle.
- Price crosses limit_price → grid stops; with keep_position=true the
  accumulated inventory is handed over (recycle it — see SKILL.md).
- `activation_bounds` keeps distant orders off the book (rate limits,
  liquidity hygiene).

# order_executor — schema

(verified against `manage_executors(executor_type="order_executor")`; re-fetch on any create error)

Simple order execution with retry — the CLEANUP primitive for closing
leftover inventory (e.g. what a stopped grid handed over).

| Field | Required | Type | Default | Notes |
|---|---|---|---|---|
| trading_pair | YES | string | — | |
| connector_name | YES | string | — | |
| side | YES | enum | — | 1=BUY, 2=SELL (opposite of the holding to close) |
| amount | YES | number/str | — | BASE units |
| execution_strategy | YES | enum | — | re-fetch the schema for the current enum values before first use |
| position_action | no | str | OPEN | set "CLOSE" when closing an existing position |
| price | no | number/str | — | for limit strategies |
| chaser_config | no | object | — | for chaser strategies |
| leverage | no | int | 1 | |

`execution_strategy` is required and its enum values are venue/version
dependent — ALWAYS `manage_executors(executor_type="order_executor")` before
your first create in a session and use the values it reports.

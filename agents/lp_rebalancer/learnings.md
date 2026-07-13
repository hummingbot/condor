# Learnings

## Market Observations

## Execution Notes
- [2026-07-13 19:52] [lp_rebalance] Use mcp__condor__manage_executors (not mcp__mcp-hummingbot__manage_executors) for all executor creates/stops — the condor tool uses chain_network/connector field names and supports executor_type "swap" and "lp".
- [2026-07-13 19:53] [lp_rebalance] plan_lp_position lp_create_args use executor_type "lp" with chain_network/connector/wallet_address fields — pass verbatim to mcp__condor__manage_executors, do not remap to hummingbot schema.

## Retired Insights

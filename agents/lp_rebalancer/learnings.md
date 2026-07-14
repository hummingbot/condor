# Learnings

## Market Observations

## Execution Notes
- [2026-07-13 21:30] [lp_rebalance] Executor management is hummingbot-api now (mcp-hummingbot manage_executors), executor_type "lp_executor". The condor-native executor layer (executor_type "lp"/"swap") was removed. plan_lp_position emits `lp_create_args` as {executor_type:"lp_executor", executor_config:{...}} — pass to manage_executors(action="create") verbatim. The exact lp_executor config field names are unconfirmed on the first live run; verify against the schema (manage_executors(executor_type="lp_executor")) and journal the correct names.

## Retired Insights

# Orca LP Agent Learnings

Durable lessons belong here only after a completed dry run or live run proves them.

## Policy

- Record at most one or two actionable lessons per completed session.
- Do not store secrets, wallet keys, raw private balances, transaction dumps, or temporary emotions.
- Prefer lessons about Orca API field reliability, Gateway pool-info behavior, LP executor accounting, preset fit, and audit quality.

## Durable Lessons

No session-proven lessons yet.

## Execution Notes
- [2026-07-27 11:36] lp_position_report rejects session-level exit fields and total_amount_quote; its config accepts per-position exit controls only.
- [2026-07-27 11:52] lp_position_report can fail closed with routine_exception even when called with only accepted per-position exit controls.
- [2026-07-27 13:07] lp_position_report exit-control fields require the position_ prefix; archived_executor_ids is also part of its supervision config.
- [2026-07-27 13:11] Telegram notifications fail when CONDOR_CHAT_ID is not configured.

## Market Observations
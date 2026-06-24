# Journal - orca_lp_agent_12

## Summary
Last tick: #1 at 15:32 UTC
Status: Running | PnL: $+0.00 | Open: 0 executors
Last action: Scanning Orca pools with the session overrides and USDC-only note applied. If the candidate is weak 

## Decisions
- **#1** (15:31) 2026-06-22T15:00:00+07:00 active_state=flat routine=orca_pool_scan routine_status=success action=no-trade pool=J5jzvT22u1Mt6de4gkBhEsTSTjBfYS7A6aF5jzu9ihkC pair=Fartcoin-USDC preset=wide range=0.108478-0.157111 score=4.3827 tvl_usd=45686.49 vol24h_usd=568426.30 vol7d_usd=na price_delta_24h=na hard_gates=min_tvl_usd_failed,reject_warning_unclear risk_checks=max_open_executors_ok,budget_ok reason=selected candidate is below active min_tvl_usd 500000 and scan reported warnings so live preflight/open blocked expected_next_state=remain_flat_and_rescan_next_tick -- Held because the only scan candidate failed the active TVL gate and the routine output included warnings. [No executor opened; preserves full $10 quote budget.]

## Ticks
- tick#1 | 2026-06-22 15:32 | actions=0 | Scanning Orca pools with the session overrides and USDC-only note applied. If the candidate is weak or preflight data is incomplete, I will hold and journal a no-trade decision.The scan produced one m

## Executors

## Snapshots
- 2026-06-22 15:32 | pnl=$+0.00 | volume=$0 | open=0 | exposure=$0.00

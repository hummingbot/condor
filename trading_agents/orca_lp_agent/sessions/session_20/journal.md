# Journal - orca_lp_agent_20

## Summary
Last tick: #2 at 06:23 UTC
Status: Running | PnL: $-0.15 | Open: 1 executors
Last action: Supervising the existing Orca LP executor for tick `#2`. I’m using the active-state path first becau

## Decisions
- **#1** (06:17) 2026-06-23T13:17:00+07:00 active_state=opened routine=orca_pool_scan+pre_lp_rebalance status=success/quote_no_swap_needed action=open_lp pool=J5jzvT22u1Mt6de4gkBhEsTSTjBfYS7A6aF5jzu9ihkC pair=Fartcoin-USDC preset=balanced range=0.120144-0.128976 price=0.1247887763 tvl_usd=43275.80 vol24h_usd=724592.43 vol7d_usd=na price_delta_24h=na score=4.4231 gates=scan_passed+gateway_passed+balances_passed risk=alloc<=10,tvl_share_ok,vol_share_ok,max_open_executors=1 executor_id=9y4KxhDUubbLs2sgVtkwu9EYyfac1hspYxzFPf5cBoTq state=RUNNING reason=scan selected viable USDC meme pool and confirmed-swap base inventory funded centered LP without extra rebalance next=supervise_lp -- Opened one balanced Orca LP after scan, Gateway pool info, wallet balances, and rebalance quote all passed with no extra swap needed. [Small size only; hard close rails set at range edges and one active LP enforced.]
- **#2** (06:23) 2026-06-23T13:17:00+07:00 active_state=active_lp routine=lp_position_report status=success action=close_lp pool=J5jzvT22u1Mt6de4gkBhEsTSTjBfYS7A6aF5jzu9ihkC pair=Fartcoin-USDC preset=balanced range=0.120144-0.128976 price=na tvl_usd=43275.80 vol24h_usd=724592.43 vol7d_usd=na price_delta_24h=na score=4.4231 gates=active_state_supervision risk=max_open_executors=1,sl=1%,tp=0.5%,age<=480 executor_id=9y4KxhDUubbLs2sgVtkwu9EYyfac1hspYxzFPf5cBoTq state=RUNNING reason=lp_position_report recommended close on stop_loss_reached with pnl=-155.60% and no new scan allowed while executor active next=await_executor_close_then_audit -- Active-state policy requires supervising the existing LP first and closing when the report hits the configured stop-loss. [No replacement position this tick; close path keeps one-active-executor cap and targets clean quote inventory.]

## Ticks
- tick#1 | 2026-06-23 06:17 | actions=0 | Scanning Orca candidates from the session profile first. If the scan yields a viable pool, I’ll use one preflight pass to decide between a live LP open and hold.The scan returned one gated meme-scout 
- tick#2 | 2026-06-23 06:23 | actions=0 | Supervising the existing Orca LP executor for tick `#2`. I’m using the active-state path first because core state shows one active executor under `orca_lp_agent_20`, so I’ll run `lp_position_report` b

## Executors

## Snapshots
- 2026-06-23 06:17 | pnl=$+0.00 | volume=$0 | open=0 | exposure=$0.00
- 2026-06-23 06:23 | pnl=$-0.15 | volume=$10 | open=1 | exposure=$0.00

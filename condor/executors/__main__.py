"""CLI for driving executors directly — the M0 live-validation harness.

NB: this runs its own ExecutorRuntime against condor.db. Do not use
`run`/`stop` while the main Condor server is up — both processes would
adopt the same executors. With the server running, drive executors via
the /executors REST routes or the condor MCP `manage_executors` tool.

    python -m condor.executors swap --base SOL --quote USDC --amount 0.01 --side SELL --wallet <addr>
    python -m condor.executors lp-open --connector raydium --pool <addr> --pair SOL-USDC \
        --quote-amount 0.5 --width-pct 4 --wallet <addr> [--rebalance-threshold-pct 1]
    python -m condor.executors run          # reconcile + resume open executors, then serve
    python -m condor.executors status       # dump store contents
    python -m condor.executors stop <id> [--no-keep-position]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from decimal import Decimal

from condor.executors.gateway import GatewayClient
from condor.executors.lp import LpConfig
from condor.executors.ranges import RangePolicy, Side, plan_position
from condor.executors.runtime import ExecutorRuntime
from condor.executors.store import ExecutorStore
from condor.executors.swap import SwapConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("condor.executors.cli")


async def _run_until_done(runtime: ExecutorRuntime) -> None:
    """Run executors to completion; SIGINT/SIGTERM = kill test (positions survive)."""
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    runtime.start_watchdog()
    wait_task = asyncio.create_task(runtime.wait_all())
    stop_task = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait({wait_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        logger.warning("signal received — shutting down WITHOUT closing positions")
    await runtime.shutdown()


async def cmd_swap(args) -> None:
    runtime = ExecutorRuntime()
    config = SwapConfig(
        chain_network=args.chain_network,
        wallet_address=args.wallet,
        base_token=args.base,
        quote_token=args.quote,
        amount=Decimal(args.amount),
        side=args.side,
        connector=args.connector,
        slippage_pct=Decimal(args.slippage_pct),
    )
    eid = runtime.create_executor(config)
    print(f"created {eid}")
    await _run_until_done(runtime)
    record = ExecutorStore().load(eid)
    print(json.dumps({"id": eid, "status": record.status, "state": record.state,
                      "close_reason": record.close_reason}, indent=2))


async def cmd_lp_open(args) -> None:
    runtime = ExecutorRuntime()
    gateway = runtime.gateway
    pool = await gateway.clmm_pool_info(args.connector, args.chain_network, args.pool)
    current_price = Decimal(str(pool["price"]))
    print(f"pool price: {current_price}")

    policy = RangePolicy(
        position_width_pct=Decimal(args.width_pct),
        rebalance_threshold_pct=Decimal(args.rebalance_threshold_pct),
    )
    plan = plan_position(Side.RANGE, current_price, Decimal(args.quote_amount), policy)
    if plan is None:
        raise SystemExit("no valid position possible at current price")
    print(f"planned: [{plan.lower_price:.6f} - {plan.upper_price:.6f}] "
          f"base={plan.base_amount:.6f} quote={plan.quote_amount:.6f} "
          f"limits=[{plan.lower_limit_price:.6f}, {plan.upper_limit_price:.6f}]")

    config = LpConfig(
        chain_network=args.chain_network,
        wallet_address=args.wallet,
        connector=args.connector,
        pool_address=args.pool,
        trading_pair=args.pair,
        lower_price=plan.lower_price,
        upper_price=plan.upper_price,
        base_amount=plan.base_amount,
        quote_amount=plan.quote_amount,
        lower_limit_price=plan.lower_limit_price,
        upper_limit_price=plan.upper_limit_price,
        keep_position=not args.no_keep_position,
        update_interval=args.update_interval,
    )
    eid = runtime.create_executor(config)
    print(f"created {eid}")
    await _run_until_done(runtime)


async def cmd_run(args) -> None:
    runtime = ExecutorRuntime()
    resumed = await runtime.reconcile()
    print(f"reconciled; resumed live: {resumed or 'none'}")
    if not runtime.list_running():
        print("nothing running — exiting")
        await runtime.shutdown()
        return
    await _run_until_done(runtime)


async def cmd_status(args) -> None:
    store = ExecutorStore()
    for r in store.list_all(limit=args.limit):
        print(f"{r.id}  {r.status:8s}  {r.close_reason or '-'}")
        if args.verbose:
            print(f"  config: {json.dumps(r.config)}")
            print(f"  state:  {json.dumps(r.state)}")


async def cmd_stop(args) -> None:
    # Out-of-process stop: adopt via reconcile, request stop, run to completion.
    runtime = ExecutorRuntime()
    resumed = await runtime.reconcile()
    if args.id not in resumed:
        record = ExecutorStore().load(args.id)
        status = record.status if record else "not found"
        raise SystemExit(f"executor {args.id} not resumable (status: {status})")
    runtime.stop_executor(args.id, keep_position=not args.no_keep_position)
    await _run_until_done(runtime)
    record = ExecutorStore().load(args.id)
    print(f"{args.id}: {record.status} ({record.close_reason})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m condor.executors")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("swap", help="execute a one-shot swap")
    p.add_argument("--chain-network", default="solana-mainnet-beta")
    p.add_argument("--wallet", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--quote", required=True)
    p.add_argument("--amount", required=True)
    p.add_argument("--side", required=True, choices=["BUY", "SELL"])
    p.add_argument("--connector", default=None)
    p.add_argument("--slippage-pct", default="1.0")
    p.set_defaults(func=cmd_swap)

    p = sub.add_parser("lp-open", help="open an LP position (RANGE side, centered)")
    p.add_argument("--chain-network", default="solana-mainnet-beta")
    p.add_argument("--wallet", required=True)
    p.add_argument("--connector", required=True)
    p.add_argument("--pool", required=True)
    p.add_argument("--pair", required=True)
    p.add_argument("--quote-amount", required=True, help="total deposit in quote units")
    p.add_argument("--width-pct", default="4")
    p.add_argument("--rebalance-threshold-pct", default="1")
    p.add_argument("--update-interval", type=float, default=5.0)
    p.add_argument("--no-keep-position", action="store_true")
    p.set_defaults(func=cmd_lp_open)

    p = sub.add_parser("run", help="reconcile and resume open executors")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="list executors in the store")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "stop",
        help="stop an executor: default detaches (position stays open on-chain); "
        "--no-keep-position closes the position",
    )
    p.add_argument("id")
    p.add_argument("--no-keep-position", action="store_true")
    p.set_defaults(func=cmd_stop)

    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()

"""CLI for driving executors directly — the M0 live-validation harness.

NB: this runs its own ExecutorRuntime against the append-only ExecutorLog
(agents/<slug>/executors.jsonl). Do not use `run`/`stop` while the main
Condor server is up — both processes would adopt the same executors. With
the server running, drive executors via the /executors REST routes or the
condor MCP `manage_executors` tool.

    python -m condor.executors swap --base SOL --quote USDC --amount 0.01 --side SELL --wallet <addr>
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

from condor.executors.log import ExecutorLog
from condor.executors.order import OrderSpotConfig
from condor.executors.runtime import ExecutorRuntime

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
    config = OrderSpotConfig(
        chain_network=args.chain_network,
        wallet_address=args.wallet,
        base_token=args.base,
        quote_token=args.quote,
        amount=Decimal(args.amount),
        side=args.side,
        slippage_pct=Decimal(args.slippage_pct),
    )
    eid = runtime.create_executor(config)
    print(f"created {eid}")
    await _run_until_done(runtime)
    record = runtime.store.load(eid)
    print(json.dumps({"id": eid, "status": record.status, "state": record.state,
                      "close_reason": record.close_reason}, indent=2))


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
    store = ExecutorLog()
    for r in store.list_all(limit=args.limit):
        print(f"{r.id}  {r.status:8s}  {r.close_reason or '-'}")
        if args.verbose:
            print(f"  config: {json.dumps(r.config)}")
            print(f"  state:  {json.dumps(r.state)}")


async def cmd_pnl(args) -> None:
    """Realized P&L per closed executor, from the store alone.

    Swaps are conversions, not P&L: only their fee counts as cost.
    """
    from decimal import Decimal

    store = ExecutorLog()

    if args.group_by:
        from condor.executors.performance import aggregate_performance

        for g in aggregate_performance(store, group_by=args.group_by,
                                       agent_id=args.agent_id, limit=args.limit):
            wr = f"{g['win_rate']:.0%}" if g["win_rate"] is not None else "-"
            print(f"{g['key']:30s} open {g['open_count']}  closed {g['closed_count']}"
                  f"  failed {g['failed_count']}  pnl {g['realized_pnl_quote']:+.6f}"
                  f"  costs -{g['costs_quote']:.6f}  win {wr}  {g['close_types']}")
        return

    records = [r for r in store.list_all(limit=args.limit) if r.status == "CLOSED"]
    if args.agent_id:
        records = [r for r in records if r.agent_id == args.agent_id]

    def _decimals(state: dict) -> dict:
        out = {}
        for k, v in state.items():
            if isinstance(v, (int, float, str)):
                try:
                    out[k] = Decimal(str(v))
                except Exception:
                    pass
        return out

    total_pnl = Decimal("0")
    total_costs = Decimal("0")
    for r in reversed(records):  # oldest first
        s = _decimals(r.state)
        if r.type == "order_spot":
            extra = r.state.get("extra") or {}
            fee = Decimal(str(extra.get("fee", 0)))
            quoted = s.get("entry_price", Decimal("0"))
            cost = fee * quoted  # fee is native; approximate at quoted price
            total_costs += cost
            total_pnl -= cost
            print(f"{r.id}  [swap] {r.config.get('base_token')}-{r.config.get('quote_token')} "
                  f"{r.config.get('side')} {r.config.get('amount')}  agent={r.agent_id or '-'}")
            print(f"   conversion (not P&L); tx fee cost: -{cost:.6f} quote")
    print(f"\nTOTAL net realized: {total_pnl:+.6f} quote  (of which costs: -{total_costs:.6f})")


async def cmd_stop(args) -> None:
    # Out-of-process stop: adopt via reconcile, request stop, run to completion.
    runtime = ExecutorRuntime()
    resumed = await runtime.reconcile()
    if args.id not in resumed:
        record = runtime.store.load(args.id)
        status = record.status if record else "not found"
        raise SystemExit(f"executor {args.id} not resumable (status: {status})")
    runtime.stop_executor(args.id, keep_position=not args.no_keep_position)
    await _run_until_done(runtime)
    record = runtime.store.load(args.id)
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

    p = sub.add_parser("run", help="reconcile and resume open executors")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="list executors in the store")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("pnl", help="realized P&L per closed executor, from the store")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--agent-id", default=None)
    p.add_argument("--group-by", default=None,
                   choices=["agent", "run", "strategy", "venue", "type"],
                   help="aggregate instead of listing per-executor")
    p.set_defaults(func=cmd_pnl)

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

"""PancakeSwap Solana CLMM, driven through condor's manage_clmm tool.

Written to unblock GW-18, which is the last wrong stored number still open. The connector's
close hardcodes three fields rather than reading them off the transaction:

    positionRentRefunded: 0,   // Position rent refund (simplified)
    baseFeeAmountCollected: 0, // Included in balance changes
    quoteFeeAmountCollected: 0,

A close reporting 0/0 is indistinguishable from a position that genuinely earned nothing,
which is how the bug survived. So this script does not just close a position — it
establishes, before closing, that the numbers are not zero.

The obvious way to do that does not work, and finding out why widened GW-18. `info` cannot
be the witness, because `positionInfo` hardcodes the same two fields:

    pancakeswap-sol.ts:550    const baseFeeAmount = 0;
                              const quoteFeeAmount = 0;

so it answers 0 whether or not the position earned anything. What does work is
`collect_fees`, which derives its numbers from balance deltas and is the only fee-bearing
route on this connector that reports real ones:

    open  ->  wait  ->  collect  ->  close
                        ^^^^^^^      ^^^^^
                        fees > 0     rent reported 0

A nonzero `collect` against an `info` that says 0 at the same moment proves the read is
hardcoded; the close that follows then reports rent 0 against a rent known from the open.
Rent needs no setup — a Solana position account always holds some, so any close reporting
exactly 0 is already wrong, and the wallet's SOL delta says what it should have been.

EVERY STEP EXCEPT `reads` AND `info` SIGNS AND SUBMITS A MAINNET TRANSACTION. Run one at a
time and read the result before continuing:

    ./.venv/bin/python scripts_lp_test/test_pancakeswap_sol_clmm.py            # list steps
    ./.venv/bin/python scripts_lp_test/test_pancakeswap_sol_clmm.py reads
    ./.venv/bin/python scripts_lp_test/test_pancakeswap_sol_clmm.py open
    ./.venv/bin/python scripts_lp_test/test_pancakeswap_sol_clmm.py info <position_address>
    ./.venv/bin/python scripts_lp_test/test_pancakeswap_sol_clmm.py close <position_address>

Amounts are ~0.01 SOL plus the USDC that balances it, so roughly $1.75 at risk.

The range straddles spot and is deliberately wider than the ones in test_orca_clmm.py. Both
choices are needed: straddling keeps the position in range so it actually earns the fees
this test depends on, and the extra width avoids GW-25, where a 1%-wide in-range close
failed its slippage check twice — once on-chain, for gas. Fee accrual is slower at this
width, but the test needs any nonzero fee, not a large one.
"""

import asyncio
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_servers.hummingbot_api import server  # noqa: E402


def tool(name):
    obj = getattr(server, name)
    return getattr(obj, "fn", obj)


manage_clmm = tool("manage_clmm")

NET = "solana-mainnet-beta"
WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"
CONNECTOR = "pancakeswap-sol"
POOL = "4QU2NpRaqmKMvPSwVKQDeW4V6JFEKJdkzbzdauumD9qN"  # pancakeswap-sol SOL-USDC, 0.03%

API = os.environ.get("HUMMINGBOT_API_URL", "http://localhost:8000")
AUTH = os.environ.get("HUMMINGBOT_API_AUTH", "admin:admin")

# 3% wide, straddling spot — see the module docstring on why this is not the 1% used for
# Orca. WIDTH is total width, so the range runs 1.5% either side.
WIDTH = 0.03

BASE_AMOUNT = 0.01


def _pool_price() -> float:
    """Spot, read live. The range is derived from it rather than hardcoded: a fixed range
    stops straddling spot as soon as the market moves, and a position that falls out of
    range stops earning the fees this test is built around."""
    request = urllib.request.Request(
        f"{API}/gateway/clmm/pool-info"
        f"?connector={CONNECTOR}&network={NET}&pool_address={POOL}")
    request.add_header("Authorization", "Basic " + base64.b64encode(AUTH.encode()).decode())
    with urllib.request.urlopen(request, timeout=60) as response:
        return float(json.load(response)["price"])


async def reads():
    """Pool state and any positions already held. Signs nothing.

    The pool is read over HTTP rather than through a tool because pool *listing* is not
    implemented for this connector — `explore_dex_pools` answers 400 for anything outside
    meteora and orca — while `pool-info` on a known address works fine.
    """
    out = [f"--- pool {POOL}", f"spot {_pool_price():.4f}"]
    out.append("--- positions owned by this wallet on " + CONNECTOR)
    out.append(str(await manage_clmm(
        action="position_info", connector=CONNECTOR, network=NET, wallet_address=WALLET)))
    return "\n".join(o.strip() for o in out)


async def _open(base_amount: float = BASE_AMOUNT):
    """Open a position straddling spot, funded on both sides.

    Both amounts are offered because the range spans spot and so holds both tokens. The
    quote side is sized at spot so the two roughly balance; the connector takes what the
    range implies and returns the rest.

    Takes an optional base amount because of GW-28: this connector sizes liquidity from
    the slippage-inflated maximum and then fails if the program's round-trip rounds one
    unit above it, which happens or does not depending on the fractional part of a float.
    A different amount re-rolls that rounding. The failure is caught at simulation and
    costs nothing, so retrying is free — see the GW-28 entry before assuming otherwise.
    """
    spot = _pool_price()
    lower, upper = spot * (1 - WIDTH / 2), spot * (1 + WIDTH / 2)
    quote_amount = base_amount * spot

    print(f"spot {spot:.4f}  ->  range {lower:.4f}-{upper:.4f}  ({WIDTH:.0%} wide, straddling)")
    print(f"funding {base_amount} SOL + {quote_amount:.4f} USDC")
    return await manage_clmm(
        action="open", connector=CONNECTOR, network=NET, pool_address=POOL,
        lower_price=f"{lower:.6f}", upper_price=f"{upper:.6f}",
        base_token_amount=str(base_amount), quote_token_amount=f"{quote_amount:.6f}")


def steps(arg):
    """Each step is a no-arg lambda so nothing runs until one is selected."""
    return {
        "reads": reads,
        "open": lambda: _open(float(arg) if arg else BASE_AMOUNT),

        # Reports composition and range correctly, but its fee fields are hardcoded to 0
        # — see the docstring. Useful for the range and the amounts, not as fee evidence.
        "info": lambda: manage_clmm(
            action="position_info", connector=CONNECTOR, network=NET, position_address=arg),

        # The GW-18 evidence step, and the only route here that computes fees from what
        # actually moved. A nonzero result is what `info` and `close` both deny.
        "collect": lambda: manage_clmm(
            action="collect_fees", connector=CONNECTOR, network=NET, position_address=arg),

        # Reports positionRentRefunded / baseFeeAmountCollected / quoteFeeAmountCollected,
        # all three hardcoded to 0. Worse, its baseTokenAmountRemoved reads the native SOL
        # delta, which on this connector is the rent and not the liquidity — the liquidity
        # comes back as WSOL and is never unwrapped. See GW-18 and GW-30.
        "close": lambda: manage_clmm(
            action="close", connector=CONNECTOR, network=NET, position_address=arg),
    }


NEEDS_POSITION = ("info", "close", "collect")


async def main():
    name = sys.argv[1] if len(sys.argv) > 1 else None
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    table = steps(arg)

    if name not in table:
        print(__doc__)
        print("Steps:")
        for key in table:
            print(f"  {key}")
        return

    if name in NEEDS_POSITION and not arg:
        print(f"{name} needs a position address: ... {name} <position_address>")
        return

    print(f"--- {name} ---")
    try:
        print((await table[name]()).strip())
    except Exception as e:
        print(f"!! {type(e).__name__}: {e}")


asyncio.run(main())

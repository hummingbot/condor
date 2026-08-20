"""Orca CLMM, driven through condor's manage_clmm tool.

test_lp_write.py exercises the CLMM lifecycle on Meteora only. Orca is a different SDK
underneath — v8 builds the whole close flow in one call, and its close is the one that
reports collected fees and rent refunded separately — so the same sequence is worth
running against it independently.

EVERY STEP EXCEPT `reads` SIGNS AND SUBMITS A MAINNET TRANSACTION. Run one at a time and
read the result before continuing:

    ./.venv/bin/python scripts_lp_test/test_orca_clmm.py            # list the steps
    ./.venv/bin/python scripts_lp_test/test_orca_clmm.py reads
    ./.venv/bin/python scripts_lp_test/test_orca_clmm.py open-across
    ./.venv/bin/python scripts_lp_test/test_orca_clmm.py add <position_address>

Amounts are ~0.01 SOL / ~1 USDC per step.

Three opens, because a range has three possible relationships to spot and a CLMM position
holds a different thing in each. Entirely above spot it is 100% base; entirely below, 100%
quote; across spot, both, in the ratio the range implies. Each step funds only the side its
range can use, which is the invariant worth testing — offering the other one is what GW-1
was about, where Meteora quoted a paired amount for a one-sided range and then rejected its
own quote.

The ranges are derived from live spot rather than written down, because a fixed range is
only "above" or "below" until the market moves. Each is 1% wide, and the one-sided ones sit
2% clear of spot.
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
POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"  # orca SOL-USDC, 0.04%

API = os.environ.get("HUMMINGBOT_API_URL", "http://localhost:8000")
AUTH = os.environ.get("HUMMINGBOT_API_AUTH", "admin:admin")

# Each range is 1% wide and the one-sided ones sit 2% clear of spot. Both numbers matter:
# the pool's bin_step is 4, so 1% is ~25 usable ticks and snapping cannot collapse the
# range, while 2% of clearance is more than spot has moved between any two steps of this
# script. A one-sided range that drifts across spot stops testing what it is named for.
WIDTH = 0.01
CLEARANCE = 0.02


def _pool_price() -> float:
    """Spot, read live. The ranges below are derived from it rather than hardcoded: a
    fixed range is only above or below spot until the market moves."""
    request = urllib.request.Request(
        f"{API}/gateway/clmm/pool-info?connector=orca&network={NET}&pool_address={POOL}")
    request.add_header("Authorization", "Basic " + base64.b64encode(AUTH.encode()).decode())
    with urllib.request.urlopen(request, timeout=60) as response:
        return float(json.load(response)["price"])


async def _open(side: str):
    """Open a position on one side of spot, or across it.

    A CLMM position holds only the token the range is denominated away from: entirely
    above spot it is all base, entirely below it is all quote, and across spot it is
    both. So each case funds only the side it can actually use — offering the other one
    is what GW-1 was about, where Meteora quoted a paired amount for a one-sided range
    and then rejected its own quote.
    """
    spot = _pool_price()
    lower, upper = {
        "above": (spot * (1 + CLEARANCE), spot * (1 + CLEARANCE + WIDTH)),
        "below": (spot * (1 - CLEARANCE - WIDTH), spot * (1 - CLEARANCE)),
        "across": (spot * (1 - WIDTH / 2), spot * (1 + WIDTH / 2)),
    }[side]
    amounts = {
        "above": {"base_token_amount": "0.01"},                                  # all base
        "below": {"quote_token_amount": "1"},                                    # all quote
        "across": {"base_token_amount": "0.01", "quote_token_amount": "1"},      # both
    }[side]

    print(f"spot {spot:.4f}  ->  {side} range {lower:.4f}-{upper:.4f}  "
          f"funding {'+'.join(amounts)}")
    return await manage_clmm(
        action="open", connector="orca", network=NET, pool_address=POOL,
        lower_price=f"{lower:.6f}", upper_price=f"{upper:.6f}", **amounts)

async def reads():
    """Every orca read, in one go. Signs nothing."""
    out = []
    out.append("--- positions owned by this wallet on orca")
    out.append(await manage_clmm(action="position_info", connector="orca",
                                 network=NET, wallet_address=WALLET))
    return "\n".join(str(o).strip() for o in out)


def steps(arg):
    """Each step is a no-arg lambda so nothing runs until one is selected."""
    return {
        "reads": reads,

        # The three shapes a CLMM range can have relative to spot. Ranges are derived
        # from live spot — see _open.
        "open-above": lambda: _open("above"),     # all base, no quote required
        "open-below": lambda: _open("below"),     # all quote, no base required
        "open-across": lambda: _open("across"),   # both sides

        "add": lambda: manage_clmm(
            action="add_liquidity", connector="orca", network=NET,
            position_address=arg, base_token_amount="0.005"),
        "remove": lambda: manage_clmm(
            action="remove_liquidity", connector="orca", network=NET,
            position_address=arg, percentage_to_remove="50"),
        "collect": lambda: manage_clmm(
            action="collect_fees", connector="orca", network=NET, position_address=arg),
        "close": lambda: manage_clmm(
            action="close", connector="orca", network=NET, position_address=arg),

        # Reads one position rather than the whole wallet — the two go through different
        # code paths in hummingbot-api, and they have disagreed before.
        "info": lambda: manage_clmm(
            action="position_info", connector="orca", network=NET, position_address=arg),
    }


NEEDS_POSITION = ("add", "remove", "collect", "close", "info")


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

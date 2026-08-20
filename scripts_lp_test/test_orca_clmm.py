"""Orca CLMM, driven through condor's manage_clmm tool.

test_lp_write.py exercises the CLMM lifecycle on Meteora only. Orca is a different SDK
underneath — v8 builds the whole close flow in one call, and its close is the one that
reports collected fees and rent refunded separately — so the same sequence is worth
running against it independently.

EVERY STEP EXCEPT `reads` SIGNS AND SUBMITS A MAINNET TRANSACTION. Run one at a time and
read the result before continuing:

    ./.venv/bin/python scripts_lp_test/test_orca_clmm.py            # list the steps
    ./.venv/bin/python scripts_lp_test/test_orca_clmm.py reads
    ./.venv/bin/python scripts_lp_test/test_orca_clmm.py open
    ./.venv/bin/python scripts_lp_test/test_orca_clmm.py add <position_address>

Amounts are ~0.01 SOL / ~1 USDC per step.

Two opens, because the ranges answer different questions. `open` straddles spot, so the
position should hold both tokens. `open-above` sits entirely above spot, where a CLMM
position is 100% base — it should take the SOL and require no USDC at all. That second
case is the one GW-1 was about: Meteora used to quote a nonzero paired amount for it and
then reject its own quote.
"""

import asyncio
import sys
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

        # Straddles spot (~85): the position should take both tokens.
        "open": lambda: manage_clmm(
            action="open", connector="orca", network=NET, pool_address=POOL,
            lower_price="80", upper_price="90",
            base_token_amount="0.01", quote_token_amount="1"),

        # Entirely above spot: 100% base, and the quote side should stay untouched.
        "open-above": lambda: manage_clmm(
            action="open", connector="orca", network=NET, pool_address=POOL,
            lower_price="95", upper_price="105", base_token_amount="0.01"),

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

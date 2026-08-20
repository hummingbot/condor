"""The fund-moving swap / AMM / CLMM actions, driven through condor's MCP tools.

EVERY STEP HERE SIGNS AND SUBMITS A MAINNET TRANSACTION from the wallet below.
Run one step at a time and read the result before continuing:

    uv run python scripts_lp_test/test_lp_write.py <step>
    uv run python scripts_lp_test/test_lp_write.py            # list the steps

Steps that need a position address take it as a second argument:

    uv run python scripts_lp_test/test_lp_write.py clmm-open
    uv run python scripts_lp_test/test_lp_write.py clmm-add <position_address>

Amounts are deliberately small (~0.01 SOL / ~1 USDC per step). Balances when this
was written: 2.687 SOL, 20.288 USDC.

Note these calls bypass the agent confirmation gate — that gate lives in the chat
handler, not in the tools. This script IS the confirmation: you ran it on purpose.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_servers.hummingbot_api import server  # noqa: E402


def tool(name):
    obj = getattr(server, name)
    return getattr(obj, "fn", obj)


manage_gateway_swaps = tool("manage_gateway_swaps")
manage_amm = tool("manage_amm")
manage_clmm = tool("manage_clmm")

NET = "solana-mainnet-beta"
MET_CLMM = "2sf5NYcY4zUPXUSmG6f66mskb24t5F8S11pC1Nz5nQT3"   # SOL-USDC, spot ~82
RAY_AMM = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"    # SOL-USDC
ORCA_POSITION = "4G5GyCPi9U75GQ9NwLAov7psKngofiHThPAJQJ48cvUR"  # out of range, has fees


def steps(arg):
    """Each step is a no-arg lambda so nothing runs until one is selected."""
    return {
        # ---- swaps: one per connector type ---------------------------------
        "swap-router": lambda: manage_gateway_swaps(
            action="execute", connector="jupiter", network=NET,
            trading_pair="SOL-USDC", side="SELL", amount="0.01"),
        "swap-clmm": lambda: manage_gateway_swaps(
            action="execute", connector="meteora/clmm", network=NET,
            trading_pair="SOL-USDC", side="SELL", amount="0.01"),
        "swap-amm": lambda: manage_gateway_swaps(
            action="execute", connector="raydium/amm", network=NET,
            trading_pair="SOL-USDC", side="SELL", amount="0.01"),

        # ---- CLMM lifecycle: open -> add -> remove -> close -----------------
        # Range straddles spot, so the position takes both tokens. With the
        # quote-position fix in place, a range that does NOT straddle spot
        # should now quote zero on the unused side — worth trying too.
        "clmm-open": lambda: manage_clmm(
            action="open", connector="meteora", network=NET, pool_address=MET_CLMM,
            lower_price="78", upper_price="87", base_token_amount="0.01"),
        "clmm-add": lambda: manage_clmm(
            action="add_liquidity", connector="meteora", network=NET,
            position_address=arg, base_token_amount="0.005"),
        "clmm-remove": lambda: manage_clmm(
            action="remove_liquidity", connector="meteora", network=NET,
            position_address=arg, percentage_to_remove="50"),
        "clmm-close": lambda: manage_clmm(
            action="close", connector="meteora", network=NET, position_address=arg),

        # The wallet's existing orca position already has uncollected fees.
        "clmm-collect-orca": lambda: manage_clmm(
            action="collect_fees", connector="orca", network=NET,
            position_address=ORCA_POSITION),
        "clmm-close-orca": lambda: manage_clmm(
            action="close", connector="orca", network=NET,
            position_address=ORCA_POSITION),

        "clmm-create-pool": lambda: manage_clmm(
            action="create_pool", connector="meteora", network=NET,
            base_token="SOL", quote_token="USDC", initial_price="82.3",
            extra_params={"binStep": 100, "feeBps": 40}),

        # ---- AMM -----------------------------------------------------------
        "amm-add": lambda: manage_amm(
            action="add_liquidity", connector="raydium", network=NET,
            pool_address=RAY_AMM, base_token_amount="0.01", quote_token_amount="1"),
        "amm-remove": lambda: manage_amm(
            action="remove_liquidity", connector="raydium", network=NET,
            pool_address=RAY_AMM, percentage_to_remove="100"),
    }


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

    if name in ("clmm-add", "clmm-remove", "clmm-close") and not arg:
        print(f"{name} needs a position address: ... {name} <position_address>")
        return

    print(f"--- {name} ---")
    try:
        print((await table[name]()).strip())
    except Exception as e:
        print(f"!! {type(e).__name__}: {e}")


asyncio.run(main())

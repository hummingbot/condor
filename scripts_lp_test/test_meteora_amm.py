"""Meteora DAMM v2, driven through condor's manage_amm tool.

test_lp_write.py exercises the AMM surface on Raydium only, which is a fungible-LP CPMM.
Meteora DAMM v2 is the other kind: positions are NFTs, so a wallet can hold several in one
pool, add_liquidity opens a new one when no position_address is given, and remove_liquidity
requires one. None of that is reachable through the Raydium path.

The pool here is a memecoin/SOL pool the wallet already has history with, because no
Meteora AMM pool is in Gateway's configured list — only Meteora CLMM. That has a
consequence the script is built around: DAMM v2 needs both sides of the pair, and the
wallet holds none of the base token, so `swap-in` has to run first.

That first swap does something else worth watching. Gateway learns tokens and pools from
the chain as they are used, so before `swap-in` this mint has no symbol and this pool is
in no list; afterwards both should be recorded, and `lists` shows it.

EVERY STEP EXCEPT `reads` AND `lists` SIGNS AND SUBMITS A MAINNET TRANSACTION:

    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py           # list the steps
    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py lists     # before
    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py swap-in
    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py lists     # after — token learned
    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py add
    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py remove <position_address>

Sized at ~0.01 SOL per leg. The pool charges 4%, so a round trip through it is expected to
lose a few percent of the amount committed — that is the pool's fee, not a defect.
"""

import asyncio
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


manage_amm = tool("manage_amm")
manage_gateway_swaps = tool("manage_gateway_swaps")

NET = "solana-mainnet-beta"
WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"

# Meteora DAMM v2, 4% fee. Base is an unlisted memecoin mint; quote is wrapped SOL, which
# is why the pair below can be written against SOL.
POOL = "2fckuYXUrwbA9LHKMP1az5DTFstFrRjqqbBstz7yvVXL"
MINT = "DpBzjtgGLF7QA9Ug3eUVGbnqa6j3jvYBn1XuQuktvfhm"
PAIR = f"{MINT}-SOL"

# How much of the base token to buy and then commit. At ~0.0000017 SOL each, 5000 is
# roughly 0.0087 SOL — the same order as every other step in this suite.
BASE_UNITS = "5000"

API = os.environ.get("HUMMINGBOT_API_URL", "http://localhost:8000")
AUTH = os.environ.get("HUMMINGBOT_API_AUTH", "admin:admin")


def _get(path):
    request = urllib.request.Request(f"{API}{path}")
    import base64
    request.add_header("Authorization", "Basic " + base64.b64encode(AUTH.encode()).decode())
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


async def lists():
    """What Gateway currently knows about this token and this pool. Signs nothing.

    Run before and after swap-in: Gateway records a token and a pool the first time one is
    used, reading name, symbol and decimals off the chain rather than from a third party.
    """
    # Scan the full list rather than passing ?search=<address>: that filter matches on
    # symbol and name only, so it reports a token absent when it is present. The pool
    # filter on the same router does match an address, which is what makes it a trap.
    all_tokens = _get(f"/gateway/networks/{NET}/tokens")["tokens"]
    token = [t for t in all_tokens if t.get("address") == MINT]
    pool = _get(f"/gateway/networks/{NET}/pools?search={POOL}")["pools"]
    totals = (len(all_tokens), _get(f"/gateway/networks/{NET}/pools")["count"])
    return (
        f"token {MINT[:8]}…: {json.dumps(token) if token else 'NOT IN LIST'}\n"
        f"pool  {POOL[:8]}…: {json.dumps(pool) if pool else 'NOT IN LIST'}\n"
        f"totals: {totals[0]} tokens, {totals[1]} pools"
    )


async def reads():
    """Every Meteora AMM read. Signs nothing."""
    out = []
    for title, coro in [
        ("pool_info", manage_amm(action="pool_info", connector="meteora",
                                 network=NET, pool_address=POOL)),
        ("quote_liquidity", manage_amm(action="quote_liquidity", connector="meteora",
                                       network=NET, pool_address=POOL,
                                       base_token_amount=BASE_UNITS,
                                       quote_token_amount="0.01")),
        ("positions_owned", manage_amm(action="positions_owned", connector="meteora",
                                       network=NET, wallet_address=WALLET)),
        ("position_info", manage_amm(action="position_info", connector="meteora",
                                     network=NET, pool_address=POOL,
                                     wallet_address=WALLET)),
    ]:
        out.append(f"--- {title}")
        try:
            out.append(str(await coro).strip())
        except Exception as e:
            out.append(f"  !! {type(e).__name__}: {e}")
    return "\n".join(out)


def steps(arg):
    """Each step is a no-arg lambda so nothing runs until one is selected."""
    return {
        "lists": lists,
        "reads": reads,

        # Buys the base token so there is something to pair with. Also the call that
        # teaches Gateway the mint — a router swap learns the two token addresses it was
        # given, even though it has no pool to record.
        "swap-in": lambda: manage_gateway_swaps(
            action="execute", connector="jupiter", network=NET,
            trading_pair=PAIR, side="BUY", amount=BASE_UNITS),

        # No position_address: DAMM v2 opens a new NFT position.
        "add": lambda: manage_amm(
            action="add_liquidity", connector="meteora", network=NET, pool_address=POOL,
            base_token_amount=BASE_UNITS, quote_token_amount="0.01"),
        # With one: adds to the position that already exists, rather than opening another.
        "add-more": lambda: manage_amm(
            action="add_liquidity", connector="meteora", network=NET, pool_address=POOL,
            position_address=arg, base_token_amount="2000", quote_token_amount="0.005"),

        "remove": lambda: manage_amm(
            action="remove_liquidity", connector="meteora", network=NET,
            pool_address=POOL, position_address=arg, percentage_to_remove="50"),
        "close": lambda: manage_amm(
            action="remove_liquidity", connector="meteora", network=NET,
            pool_address=POOL, position_address=arg, percentage_to_remove="100"),

        # Sells the base token back, leaving the wallet as it started.
        "swap-out": lambda: manage_gateway_swaps(
            action="execute", connector="jupiter", network=NET,
            trading_pair=PAIR, side="SELL", amount=BASE_UNITS),
    }


NEEDS_POSITION = ("add-more", "remove", "close")


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
        print(str(await table[name]()).strip())
    except Exception as e:
        print(f"!! {type(e).__name__}: {e}")


asyncio.run(main())

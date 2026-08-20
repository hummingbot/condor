"""Meteora DAMM v2, driven through condor's manage_amm tool.

test_lp_write.py exercises the AMM surface on Raydium only, which is a fungible-LP CPMM.
Meteora DAMM v2 is the other kind: positions are NFTs, so a wallet can hold several in one
pool, add_liquidity opens a new one when no position_address is given, and remove_liquidity
requires one. None of that is reachable through the Raydium path.

The pool here is a memecoin/SOL pool the wallet already has history with, because no
Meteora AMM pool is in Gateway's configured list — only Meteora CLMM. That has a
consequence the script is built around: DAMM v2 needs both sides of the pair, and the
wallet starts with far less base token than a leg needs, so `swap-in` has to run first.

`lists` shows what Gateway knows about this mint and this pool. Gateway learns both from
the chain the first time either is used, so the list grows as the script runs — reading
`pool_info` alone is enough to record the pool and its two tokens, before any of the
write steps have run.

`balances` reads the wallet. It asks `/portfolio/state` for `refresh: true`, which is not
the default: without it the API answers from a snapshot it rebuilds on a five-minute loop,
so straight after a swap it still reports the pre-swap balance. Every other read in this
script goes live to Gateway, so the cached form is the one thing here that can disagree
with the chain.

EVERY STEP EXCEPT `balances`, `lists` AND `reads` SIGNS AND SUBMITS A MAINNET TRANSACTION:

    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py           # list the steps
    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py lists     # before
    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py swap-in
    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py lists     # after — token learned
    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py add
    ./.venv/bin/python scripts_lp_test/test_meteora_amm.py remove <position_address>

Sized well under 0.01 SOL per leg. The pool charges 4%, so a round trip through it is
expected to give back a few percent less than went in — that is the pool's fee, not a
defect.
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


manage_amm = tool("manage_amm")
manage_gateway_swaps = tool("manage_gateway_swaps")

NET = "solana-mainnet-beta"
WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"

# Meteora DAMM v2, 4% fee. Base is an unlisted memecoin mint; quote is wrapped SOL, which
# is why the pair below can be written against SOL.
POOL = "2fckuYXUrwbA9LHKMP1az5DTFstFrRjqqbBstz7yvVXL"
MINT = "DpBzjtgGLF7QA9Ug3eUVGbnqa6j3jvYBn1XuQuktvfhm"
PAIR = f"{MINT}-SOL"

# At ~0.0000018 SOL each, 5000 units is roughly 0.0088 SOL — the same order as every other
# step in this suite. What is committed to the pool is deliberately less than what is
# bought: a BUY fills at the routed price, not the quoted one, so asking the pool for
# exactly what the swap was told to buy overdraws whenever the fill comes in short. It did
# here — 5000 requested, 4607.86 filled.
SWAP_UNITS = "5000"
ADD_UNITS = "3000"
ADD_MORE_UNITS = "1000"
SWAP_OUT_UNITS = "4000"

# Quote offered alongside. Each is a little above the pool ratio for that base amount, so
# base stays the limiting side and the pool takes the quote it actually needs.
ADD_QUOTE = "0.006"
ADD_MORE_QUOTE = "0.002"

API = os.environ.get("HUMMINGBOT_API_URL", "http://localhost:8000")
AUTH = os.environ.get("HUMMINGBOT_API_AUTH", "admin:admin")


def _call(path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(f"{API}{path}", data=body)
    request.add_header("Authorization",
                       "Basic " + base64.b64encode(AUTH.encode()).decode())
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


async def balances():
    """The wallet, read fresh. Signs nothing.

    refresh=True is the point: the default answers from a snapshot rebuilt every five
    minutes, which still shows the pre-swap balance right after a swap.
    """
    state = _call("/portfolio/state", {"refresh": True})
    rows = [
        f"   {item.get('token'):<10} {item.get('units')}"
        for connectors in state.values()
        for name, items in (connectors or {}).items() if "solana" in name.lower()
        for item in items if float(item.get("units") or 0) > 0
    ]
    return "\n".join(rows) or "no non-zero solana balances"


async def lists():
    """What Gateway currently knows about this token and this pool. Signs nothing.

    Run before and after swap-in: Gateway records a token and a pool the first time one is
    used, reading name, symbol and decimals off the chain rather than from a third party.
    """
    # Scan the full list rather than passing ?search=<address>: that filter matches on
    # symbol and name only, so it reports a token absent when it is present. The pool
    # filter on the same router does match an address, which is what makes it a trap.
    all_tokens = _call(f"/gateway/networks/{NET}/tokens")["tokens"]
    token = [t for t in all_tokens if t.get("address") == MINT]
    pool = _call(f"/gateway/networks/{NET}/pools?search={POOL}")["pools"]
    totals = (len(all_tokens), _call(f"/gateway/networks/{NET}/pools")["count"])
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
                                       base_token_amount=ADD_UNITS,
                                       quote_token_amount=ADD_QUOTE)),
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
        "balances": balances,
        "lists": lists,
        "reads": reads,

        # Buys the base token so there is something to pair with. Also the call that
        # teaches Gateway the mint — a router swap learns the two token addresses it was
        # given, even though it has no pool to record.
        "swap-in": lambda: manage_gateway_swaps(
            action="execute", connector="jupiter", network=NET,
            trading_pair=PAIR, side="BUY", amount=SWAP_UNITS),

        # No position_address: DAMM v2 opens a new NFT position.
        "add": lambda: manage_amm(
            action="add_liquidity", connector="meteora", network=NET, pool_address=POOL,
            base_token_amount=ADD_UNITS, quote_token_amount=ADD_QUOTE),
        # With one: adds to the position that already exists, rather than opening another.
        "add-more": lambda: manage_amm(
            action="add_liquidity", connector="meteora", network=NET, pool_address=POOL,
            position_address=arg, base_token_amount=ADD_MORE_UNITS,
            quote_token_amount=ADD_MORE_QUOTE),

        "remove": lambda: manage_amm(
            action="remove_liquidity", connector="meteora", network=NET,
            pool_address=POOL, position_address=arg, percentage_to_remove="50"),
        "close": lambda: manage_amm(
            action="remove_liquidity", connector="meteora", network=NET,
            pool_address=POOL, position_address=arg, percentage_to_remove="100"),

        # Sells the base token back, leaving the wallet as it started.
        "swap-out": lambda: manage_gateway_swaps(
            action="execute", connector="jupiter", network=NET,
            trading_pair=PAIR, side="SELL", amount=SWAP_OUT_UNITS),
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

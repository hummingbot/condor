"""Executor-level tests for the fixes landed on 2026-08-20.

Everything here goes through `manage_executors`, not the gateway routes directly. That is
the point: the earlier scripts proved the connectors behave, and these prove the layer
strategies actually run on carries the same behaviour. Several of the fixes only exist at
this layer — the slippage ramp is executor state, not a Gateway feature.

What each step is aimed at:

  lp-open / lp-close    GW-25. The executor now starts at a deliberately tight
                        slippage_pct and widens only on a failure Gateway attributes to
                        slippage: 0.05 -> 0.25 -> 1.25 -> 5. Before the fix no executor
                        level set slippage at all, so a close that failed on tolerance
                        failed ten identical times, paying gas on any attempt that
                        reached the chain. Orca is the connector to test it on: it
                        enforces a minimum-amount check on close, while meteora, raydium
                        and pancakeswap-sol currently do not, so the ramp is inert there.

  order-buy             GW-35. The order executor's guide still warns that
                        `executed_amount_base` is the amount REQUESTED rather than
                        received, and tells callers to apply a 0.995 haircut. GW-35 was
                        fixed at the swap-response layer; this checks whether the fix
                        reached the executor, or whether the warning still holds.

  watch                 Reads `custom_info` for a running or finished executor, which is
                        where `current_retries` and the live `slippage_pct` are exposed.
                        A position that took several widenings says so here — this is the
                        only place the ramp is observable.

The range is deliberately NARROW and straddles spot, because that is the shape GW-25 was
filed about: composition moves fastest near a bound, so a close quoted against a stale
composition is most likely to subceed its minimum. A wide or out-of-range position cannot
reproduce it — its composition is frozen.

    ./.venv/bin/python scripts_lp_test/test_executors.py                 # list steps
    ./.venv/bin/python scripts_lp_test/test_executors.py lp-open
    ./.venv/bin/python scripts_lp_test/test_executors.py watch <executor_id>
    ./.venv/bin/python scripts_lp_test/test_executors.py lp-close <executor_id>

lp-open and order-buy SIGN MAINNET TRANSACTIONS. watch and list do not.
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


manage_executors = tool("manage_executors")

NET = "solana-mainnet-beta"
POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"  # orca SOL-USDC, 0.04%
PAIR = "SOL-USDC"

API = os.environ.get("HUMMINGBOT_API_URL", "http://localhost:8000")
AUTH = os.environ.get("HUMMINGBOT_API_AUTH", "admin:admin")

WIDTH = 0.01          # 1% wide, straddling spot — the GW-25 shape
BASE_AMOUNT = "0.01"
SIDE_RANGE = 3        # TradeType.RANGE, double-sided


def _pool_price() -> float:
    request = urllib.request.Request(
        f"{API}/gateway/clmm/pool-info?connector=orca&network={NET}&pool_address={POOL}")
    request.add_header("Authorization", "Basic " + base64.b64encode(AUTH.encode()).decode())
    with urllib.request.urlopen(request, timeout=60) as response:
        return float(json.load(response)["price"])


async def lp_open():
    """Open a narrow, in-range Orca position through the LP executor.

    Slippage is left at its defaults so the ramp is the stock one. 0.05% is tight enough
    that the open may itself need a widening, which is worth seeing: the ramp resets at
    each phase boundary, so however wide the open had to go, the close starts tight again.
    """
    spot = _pool_price()
    lower, upper = spot * (1 - WIDTH / 2), spot * (1 + WIDTH / 2)
    quote_amount = round(float(BASE_AMOUNT) * spot, 6)

    config = {
        "connector_name": NET,
        "lp_provider": "orca/clmm",
        "trading_pair": PAIR,
        "pool_address": POOL,
        "lower_price": f"{lower:.6f}",
        "upper_price": f"{upper:.6f}",
        "base_amount": BASE_AMOUNT,
        "quote_amount": str(quote_amount),
        "side": SIDE_RANGE,
        # Defaults, stated explicitly so the ramp under test is visible in the config
        # rather than implied: 0.05 -> 0.25 -> 1.25 -> 5.
        "slippage_pct": "0.05",
        "slippage_multiplier": "5",
        "max_slippage_pct": "5",
        # Keep the tokens rather than swapping back on close. The close-out swap is a
        # separate phase with its own ramp, and mixing it in makes the close's own
        # slippage behaviour harder to read.
        "keep_position": True,
    }
    print(f"spot {spot:.4f}  ->  range {lower:.4f}-{upper:.4f}  ({WIDTH:.0%} wide, straddling)")
    print(f"funding {BASE_AMOUNT} SOL + {quote_amount} USDC   slippage ramp 0.05 -> 5")
    return await manage_executors(
        action="create", executor_type="lp_executor", executor_config=config)


async def order_buy():
    """A market BUY through the order executor, to check GW-35 at this layer.

    Compare the reported `executed_amount_base` against the wallet delta afterwards. The
    executor guide currently warns they differ and recommends a 0.995 haircut; if GW-35's
    fix reached here, they should agree.
    """
    config = {
        "connector_name": "jupiter/router",
        "trading_pair": PAIR,
        "side": 1,                      # TradeType.BUY
        "amount": BASE_AMOUNT,
        "execution_strategy": "MARKET",
    }
    print(f"MARKET BUY {BASE_AMOUNT} SOL via jupiter/router")
    return await manage_executors(
        action="create", executor_type="order_executor", executor_config=config)


def steps(arg):
    return {
        "lp-open": lp_open,
        "order-buy": order_buy,

        # custom_info carries current_retries and the live slippage_pct — the only place
        # the ramp is observable after the fact.
        "watch": lambda: manage_executors(action="search", executor_id=arg, limit=1),
        "logs": lambda: manage_executors(action="get_logs", executor_id=arg),

        # keep_position is passed explicitly: a caller-initiated stop overrides the
        # config value, and leaving it implicit is how a close-out swap appears in the
        # middle of a slippage measurement.
        "lp-close": lambda: manage_executors(
            action="stop", executor_id=arg, keep_position=True),

        "list": lambda: manage_executors(
            action="search", executor_types=["lp_executor", "order_executor"], limit=8),
    }


NEEDS_ID = ("watch", "logs", "lp-close")


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

    if name in NEEDS_ID and not arg:
        print(f"{name} needs an executor id: ... {name} <executor_id>")
        return

    print(f"--- {name} ---")
    try:
        print(str(await table[name]()).strip())
    except Exception as e:
        print(f"!! {type(e).__name__}: {e}")


asyncio.run(main())

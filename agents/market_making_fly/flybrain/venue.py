"""What differs between one CLOB venue and the next.

The fly's decoder, its chart and its dopamine feedback are venue-agnostic: a
candle chart is a candle chart. Three things are not, and they all bear on
money, so they live here rather than being assumed:

* **spot or perp** — a perp quotes on margin and carries leverage and a
  position mode; a spot book quotes the inventory you actually hold.
* **the maker fee** — the take-profit floor is derived from it. Spot fees run
  three to five times perp fees on the same exchange, so a take-profit that is
  comfortably profitable on a perp loses money on spot. Getting this wrong is
  silent: the bot fills happily and bleeds the difference. On Hyperliquid it
  also differs *within* one connector by a factor of two, so that venue's fee
  is fetched per market rather than assumed — see ``hyperliquid_maker_fee_bps``.
* **where the top of book comes from** — see ``market.py``.

The fee table below is a floor-setting default, not a quote. Defaults are
deliberately **conservative** (too high costs fills, too low loses money
without saying so), and every one of them is overridable per deployment.
"""

from __future__ import annotations

import asyncio

PERP_MARKERS = ("_perpetual", "_perp", "_futures")

SPOT = "spot"
PERP = "perp"
MARKET_TYPES = (SPOT, PERP)

# Maker fee per side, in basis points. Keys are matched as a prefix of the
# connector name, longest first, so `binance_perpetual` beats `binance`.
_MAKER_FEE_BPS: dict[tuple[str, str], float] = {
    # Hyperliquid is fetched, not tabled — see hyperliquid_maker_fee_bps. These
    # two are what a caller gets if that fetch is not used: the dearer of the
    # venue's two families, so an unfetched fee never quotes too tight.
    ("hyperliquid_perpetual", PERP): 2.5,
    ("hyperliquid", SPOT): 5.0,
    ("binance_perpetual", PERP): 2.0,  # 0.02 %
    ("binance", SPOT): 7.5,  # 0.075 %
    ("gate_io_perpetual", PERP): 2.0,
    ("gate_io", SPOT): 9.0,
    ("okx_perpetual", PERP): 2.0,
    ("okx", SPOT): 8.0,
    ("kucoin_perpetual", PERP): 2.0,
    ("kucoin", SPOT): 10.0,
    ("backpack", SPOT): 8.0,
}

# Used when the connector is not in the table at all. A market maker on an
# unknown venue should quote too wide rather than too tight.
_FALLBACK_FEE_BPS = {PERP: 2.5, SPOT: 10.0}


def market_type_for(connector_name: str) -> str:
    """``perp`` when the connector names itself one, else ``spot``.

    This is the same rule Market Making Expert uses, and the same one
    hummingbot follows: the `_perpetual` suffix is the contract type.
    """
    if not connector_name:
        raise ValueError("connector_name is required")
    lowered = connector_name.lower()
    return PERP if any(m in lowered for m in PERP_MARKERS) else SPOT


def default_maker_fee_bps(connector_name: str, market_type: str) -> float:
    """A conservative per-side maker fee for this venue, in bp.

    Always overridable: pass the real figure from the exchange's fee schedule
    when it is known, because everything about the take-profit floor follows
    from it.
    """
    if market_type not in MARKET_TYPES:
        raise ValueError(f"market_type must be one of {MARKET_TYPES}")
    lowered = (connector_name or "").lower()
    matches = [
        (key, fee)
        for (key, kind), fee in _MAKER_FEE_BPS.items()
        if kind == market_type and lowered.startswith(key)
    ]
    if not matches:
        return _FALLBACK_FEE_BPS[market_type]
    # Longest prefix wins: binance_perpetual is not binance.
    return max(matches, key=lambda m: len(m[0]))[1]


def resolve(connector_name: str, market_type: str = "") -> str:
    """The market type to use: an explicit one, else derived from the connector."""
    if not market_type:
        return market_type_for(connector_name)
    if market_type not in MARKET_TYPES:
        raise ValueError(
            f"market_type must be one of {MARKET_TYPES}, got {market_type!r}"
        )
    derived = market_type_for(connector_name)
    if market_type != derived:
        # Naming a perp connector spot (or the reverse) silently changes the
        # leverage rule and the fee floor, so say so rather than proceed.
        raise ValueError(
            f"{connector_name!r} looks like a {derived} connector but was declared "
            f"{market_type!r}; check the connector name"
        )
    return market_type


# ── Hyperliquid: the fee is published, so it is read rather than assumed ──────
#
# One connector serves two families of market, and they do not cost the same:
#
# * a core perp pays the venue's own schedule — 1.5 bp a side to a maker at the
#   base tier;
# * a HIP-3 market (``ISSUER:TOKEN-QUOTE``) pays that schedule scaled by its
#   deployer's own setting, and again by a tenth where the deployer has turned
#   growth mode on. The XYZ dex is in growth mode, so it costs 0.3 bp a side.
#
# Everything above is fetched. One number is not: Hummingbot signs every
# Hyperliquid order with the foundation builder code, and the venue charges
# that on top of its own fee. It is a constant of the client, not of the
# market — ``FOUNDATION_BUILDER_FEE_TENTHS_BPS = 10`` in
# hummingbot/connector/derivative/hyperliquid_perpetual/hyperliquid_perpetual_constants.py
# — and it is not importable here, so it is named rather than fetched.
#
# Checked against fills: 173 maker fills on XYZ:ORCL-USD paid 1.29 bp all-in,
# against 1.5 × 2 × 0.1 + 1.0 = 1.30 bp computed. Hyperliquid reports ``fee``
# inclusive of the builder fee, which is why one number covers both.
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HUMMINGBOT_BUILDER_FEE_BPS = 1.0
GROWTH_MODE_FACTOR = 0.1
# The published schedule, which is what an account with no discounts pays.
# Reading it for a real address would return that account's own rate; a
# market maker sizing a floor wants the undiscounted one.
_SCHEDULE_PROBE_ADDRESS = "0x0000000000000000000000000000000000000001"

_hl_cache: dict[str, dict] = {}
_hl_lock = asyncio.Lock()


def is_hyperliquid(connector_name: str) -> bool:
    return (connector_name or "").lower().startswith("hyperliquid")


async def _hl_info(payload: dict) -> dict:
    """One Hyperliquid info call, cached for the life of the process.

    The fee schedule and a dex's deployer settings change on the order of
    months (the XYZ dex last changed its scale in November 2025), so a scan of
    120 markets should not ask 120 times.
    """
    import aiohttp

    key = repr(sorted(payload.items()))
    async with _hl_lock:
        if key not in _hl_cache:
            async with aiohttp.ClientSession() as session:
                async with session.post(HL_INFO_URL, json=payload) as response:
                    response.raise_for_status()
                    _hl_cache[key] = await response.json()
    return _hl_cache[key]


async def hyperliquid_maker_fee_bps(trading_pair: str, market_type: str) -> float:
    """What one maker side of ``trading_pair`` actually costs, in bp."""
    from flybrain.naming import pair_names

    if market_type not in MARKET_TYPES:
        raise ValueError(f"market_type must be one of {MARKET_TYPES}")
    names = pair_names(trading_pair)
    schedule = (await _hl_info({"type": "userFees", "user": _SCHEDULE_PROBE_ADDRESS}))[
        "feeSchedule"
    ]
    base = float(schedule["spotAdd" if market_type == SPOT else "add"]) * 1e4
    if not names.issuer:
        return base + HUMMINGBOT_BUILDER_FEE_BPS

    meta = await _hl_info({"type": "meta", "dex": names.issuer})
    asset = next(
        (a for a in meta["universe"] if a["name"] == names.hl_coin),
        None,
    )
    if asset is None:
        raise ValueError(
            f"{names.hl_coin!r} is not listed on the {names.issuer!r} dex, so its "
            "fee cannot be read"
        )
    scale = float(asset["deployerFeeScale"])
    # The deployer's own multiplier, as Hyperliquid documents it: below 1 it
    # adds to the venue's fee, at or above 1 it doubles the scale.
    multiplier = scale * 2 if scale >= 1 else scale + 1
    if asset.get("growthMode") == "enabled":
        multiplier *= GROWTH_MODE_FACTOR
    return base * multiplier + HUMMINGBOT_BUILDER_FEE_BPS


async def maker_fee_bps(
    connector_name: str, market_type: str, trading_pair: str
) -> float:
    """The per-side maker fee for one market: fetched where the venue publishes
    it per market, the venue default otherwise."""
    if is_hyperliquid(connector_name):
        return await hyperliquid_maker_fee_bps(trading_pair, market_type)
    return default_maker_fee_bps(connector_name, market_type)

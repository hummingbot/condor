"""What differs between one CLOB venue and the next.

The fly's decoder, its chart and its dopamine feedback are venue-agnostic: a
candle chart is a candle chart. Three things are not, and they all bear on
money, so they live here rather than being assumed:

* **spot or perp** — a perp quotes on margin and carries leverage and a
  position mode; a spot book quotes the inventory you actually hold.
* **the maker fee** — the take-profit floor is derived from it. Spot fees run
  three to five times perp fees on the same exchange, so a take-profit that is
  comfortably profitable on a perp loses money on spot. Getting this wrong is
  silent: the bot fills happily and bleeds the difference.
* **where the top of book comes from** — see ``market.py``.

The fee table below is a floor-setting default, not a quote. Defaults are
deliberately **conservative** (too high costs fills, too low loses money
without saying so), and every one of them is overridable per deployment.
"""

from __future__ import annotations

PERP_MARKERS = ("_perpetual", "_perp", "_futures")

SPOT = "spot"
PERP = "perp"
MARKET_TYPES = (SPOT, PERP)

# Maker fee per side, in basis points. Keys are matched as a prefix of the
# connector name, longest first, so `binance_perpetual` beats `binance`.
_MAKER_FEE_BPS: dict[tuple[str, str], float] = {
    ("hyperliquid_perpetual", PERP): 1.3,  # ~0.29 bp exchange + ~1.0 bp builder
    ("hyperliquid", SPOT): 4.0,
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

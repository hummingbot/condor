"""Import shim — moved to ``condor/venues/polymarket/client.py`` (§6.2b)."""

from condor.venues.polymarket.client import (  # noqa: F401
    DEFAULT_HOST,
    POLYGON,
    USDC_DECIMALS,
    OrderAck,
    PolymarketClient,
    PolymarketError,
    _order_type,
    _side,
)

__all__ = [
    "DEFAULT_HOST",
    "POLYGON",
    "USDC_DECIMALS",
    "OrderAck",
    "PolymarketClient",
    "PolymarketError",
]

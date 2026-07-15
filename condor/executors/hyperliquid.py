"""Import shim — moved to ``condor/venues/hyperliquid/client.py`` (§6.2b)."""

from condor.venues.hyperliquid.client import (  # noqa: F401
    FOUNDATION_BUILDER_ADDRESS,
    FOUNDATION_BUILDER_FEE_TENTHS_BPS,
    AccountSummary,
    Fill,
    HyperliquidClient,
    HyperliquidError,
    OrderAck,
    Position,
)

__all__ = [
    "FOUNDATION_BUILDER_ADDRESS",
    "FOUNDATION_BUILDER_FEE_TENTHS_BPS",
    "AccountSummary",
    "Fill",
    "HyperliquidClient",
    "HyperliquidError",
    "OrderAck",
    "Position",
]

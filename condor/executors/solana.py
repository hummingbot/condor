"""Import shim — moved to ``condor/venues/solana/connector.py`` (§6.2b)."""

from condor.venues.solana.connector import (  # noqa: F401
    DEFAULT_RPC,
    LAMPORTS_PER_SOL,
    WSOL_MINT,
    SimResult,
    SolanaConnector,
    TxResult,
)

__all__ = [
    "DEFAULT_RPC",
    "LAMPORTS_PER_SOL",
    "WSOL_MINT",
    "SimResult",
    "SolanaConnector",
    "TxResult",
]

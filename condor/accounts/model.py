"""AccountRef — the canonical account identity (§6.2b).

``AccountRef = {venue_id, custody_address}``. The ``venue_id`` uniquely
identifies the venue AND its deployment (mainnet unsuffixed: ``hyperliquid``,
``solana``, ``polymarket``; a non-mainnet deployment appends a suffix —
``hyperliquid-testnet`` — and is a *different venue*). Network is derived
from ``venue_id``, never stored as mutable account data.

``custody_address`` is the address that HOLDS the inventory (for Polymarket
signature types 1/2: the funder/proxy, not the signer), normalized per the
venue's rules (EVM lowercased, Solana verbatim).
"""

from __future__ import annotations

from dataclasses import dataclass


def normalize_address(address: str, style: str) -> str:
    """Normalize a custody address for identity comparison.

    ``style``: ``"evm"`` (lowercased 0x…) or ``"verbatim"`` (case-sensitive,
    e.g. Solana base58).
    """
    address = address.strip()
    if not address:
        raise ValueError("empty custody address")
    if style == "evm":
        if not (address.startswith("0x") and len(address) == 42):
            raise ValueError(f"not an EVM address: {address!r}")
        return address.lower()
    if style == "verbatim":
        return address
    raise ValueError(f"unknown address normalization style: {style!r}")


@dataclass(frozen=True)
class AccountRef:
    """Canonical account identity. Hashable — leases, inventory, risk, and
    attribution all key on it."""

    venue_id: str
    custody_address: str

    def as_dict(self) -> dict[str, str]:
        return {"venue_id": self.venue_id, "custody_address": self.custody_address}

    @classmethod
    def from_dict(cls, d: dict) -> "AccountRef":
        try:
            return cls(venue_id=d["venue_id"], custody_address=d["custody_address"])
        except KeyError as e:
            raise ValueError(f"AccountRef dict missing key: {e}") from None

    def __str__(self) -> str:
        return f"{self.venue_id}:{self.custody_address}"

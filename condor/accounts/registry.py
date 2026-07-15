"""Minimal VenueRegistry (§6.2b, Phase 2 scope).

``venue_id → {deployment/network, address-normalization rules}``. Everything
— configs, specs, executor records, leases — resolves venues through it;
only registered ids are accepted, with no legacy-spelling translation layer.

Phase 3 extends registrations with adapter factories, custody derivation,
onboarding probes, and credential-field metadata, loaded from venue packages
under ``condor/venues/`` — this module deliberately carries only what Phase
2's spec resolution needs (identity + normalization).
"""

from __future__ import annotations

from dataclasses import dataclass

from condor.accounts.model import AccountRef, normalize_address


class UnknownVenueError(ValueError):
    pass


@dataclass(frozen=True)
class VenueDef:
    venue_id: str
    network: str  # derived deployment, e.g. "mainnet" / "testnet"
    address_style: str  # "evm" | "verbatim" (normalize_address styles)


class VenueRegistry:
    """Canonical venue ids and their normalization rules."""

    def __init__(self, venues: list[VenueDef]):
        self._venues = {v.venue_id: v for v in venues}

    def get(self, venue_id: str) -> VenueDef:
        v = self._venues.get(venue_id)
        if v is None:
            raise UnknownVenueError(
                f"unregistered venue_id: {venue_id!r} "
                f"(registered: {sorted(self._venues)})"
            )
        return v

    def is_registered(self, venue_id: str) -> bool:
        return venue_id in self._venues

    def venue_ids(self) -> list[str]:
        return sorted(self._venues)

    def account_ref(self, venue_id: str, custody_address: str) -> AccountRef:
        """Build the canonical AccountRef, applying the venue's normalization."""
        venue = self.get(venue_id)
        return AccountRef(
            venue_id=venue.venue_id,
            custody_address=normalize_address(custody_address, venue.address_style),
        )


def default_registry() -> VenueRegistry:
    """The built-in venues. Mainnet ids are unsuffixed; a suffixed id is a
    DIFFERENT venue (deployment encoded in the id, never a mutable field)."""
    return VenueRegistry(
        [
            VenueDef("hyperliquid", "mainnet", "evm"),
            VenueDef("hyperliquid-testnet", "testnet", "evm"),
            VenueDef("solana", "mainnet-beta", "verbatim"),
            VenueDef("polymarket", "mainnet", "evm"),
        ]
    )

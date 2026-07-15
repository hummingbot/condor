"""Minimal VenueRegistry (§6.2b, Phase 2 scope).

``venue_id → {deployment/network, address-normalization rules}``. Everything
— configs, specs, executor records, leases — resolves venues through it;
only registered ids are accepted, with no legacy-spelling translation layer.

Registrations are supplied by the venue packages under ``condor/venues/``
(Phase 3, §6.2b): each package's ``VENUE`` spec carries adapter factories,
custody derivation, the onboarding probe, and credential-field metadata —
this module keeps only what spec resolution needs (identity + normalization)
and builds ``default_registry()`` from the loaded packages.
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
    """The registry built from the loaded venue packages (§6.2b): every
    package under ``condor/venues/`` contributes its venue_ids — adding a
    venue is adding one folder, no edits here. Mainnet ids are unsuffixed; a
    suffixed id is a DIFFERENT venue (deployment encoded in the id, never a
    mutable field)."""
    from condor.venues.registry import load_venue_packages

    return VenueRegistry(
        [
            VenueDef(venue_id, spec.networks[venue_id], spec.address_style)
            for venue_id, spec in load_venue_packages().items()
        ]
    )

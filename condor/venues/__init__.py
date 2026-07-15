"""Venue packages (§6.2b): one folder per venue, each exporting one ``VENUE``.

A venue package owns everything venue-specific — client, instrument
adapters, custody derivation, onboarding probe, credential-field metadata,
address normalization style, and canonical instrument normalization — behind
the small contract in :mod:`condor.venues.spec`. The registry is built at
startup by :func:`condor.venues.registry.load_venue_packages`; adding a venue
is adding one conforming folder here, with zero core edits.
"""

from condor.venues.spec import CredField, VenueSpec
from condor.venues.registry import load_venue_packages, venue_spec

__all__ = ["CredField", "VenueSpec", "load_venue_packages", "venue_spec"]

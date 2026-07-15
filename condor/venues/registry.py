"""Venue-package loader (§6.2b): build the registry from ``condor/venues/*``.

Every subpackage of ``condor.venues`` must export a module-level ``VENUE``
:class:`~condor.venues.spec.VenueSpec` implementing the contract. The loader
imports each package once, validates the contract (fail loudly — a broken
venue package is a startup error, never a silent skip), and caches the
``venue_id → VenueSpec`` map that ``default_registry()``, ``make_adapter``
and ``ExecutorRuntime.connector_for_spec`` resolve through.

Adding a venue = dropping one conforming folder under ``condor/venues/`` —
zero core edits (§11 acceptance: the toy venue package in
``tests/test_venue_conformance.py``).
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Optional

from condor.accounts.registry import UnknownVenueError
from condor.venues.spec import INSTRUMENTS, CredField, VenueSpec


class VenueContractError(ValueError):
    """A venue package does not implement the VENUE contract."""


_SPECS: Optional[dict[str, VenueSpec]] = None  # venue_id -> spec (cached)


def _validate(package: str, venue: object) -> VenueSpec:
    def fail(msg: str) -> VenueContractError:
        return VenueContractError(f"venue package {package!r}: {msg}")

    if not isinstance(venue, VenueSpec):
        raise fail(f"VENUE must be a VenueSpec, got {type(venue).__name__}")
    if not venue.venue_ids or not all(
        isinstance(v, str) and v for v in venue.venue_ids
    ):
        raise fail("venue_ids must be a non-empty tuple of non-empty strings")
    if venue.address_style not in ("evm", "verbatim"):
        raise fail(f"address_style must be 'evm' or 'verbatim', got {venue.address_style!r}")
    missing = [v for v in venue.venue_ids if v not in venue.networks]
    if missing:
        raise fail(f"networks missing entries for venue_ids {missing}")
    for f in venue.credential_fields:
        if not isinstance(f, CredField) or not isinstance(f.sealed, bool):
            raise fail(f"credential_fields entries must be CredField with a bool sealed flag: {f!r}")
    if not venue.adapter_factories:
        raise fail("adapter_factories must declare at least one instrument")
    for instrument, factory in venue.adapter_factories.items():
        if instrument not in INSTRUMENTS:
            raise fail(f"unknown instrument {instrument!r} (known: {INSTRUMENTS})")
        if not callable(factory):
            raise fail(f"adapter factory for {instrument!r} is not callable")
    for name in ("make_connector", "derive_custody", "probe", "normalize_instrument"):
        if not callable(getattr(venue, name)):
            raise fail(f"{name} must be callable")
    return venue


def load_venue_packages(*, refresh: bool = False) -> dict[str, VenueSpec]:
    """Import every package under ``condor/venues/``, validate its ``VENUE``
    contract, and return the ``venue_id → VenueSpec`` map (cached)."""
    global _SPECS
    if _SPECS is not None and not refresh:
        return _SPECS
    import condor.venues as venues_pkg

    specs: dict[str, VenueSpec] = {}
    owner: dict[str, str] = {}  # venue_id -> package name (duplicate detection)
    for _finder, name, ispkg in sorted(
        pkgutil.iter_modules(venues_pkg.__path__), key=lambda m: m.name
    ):
        if not ispkg:
            continue  # spec.py / registry.py are contract modules, not venues
        module = importlib.import_module(f"condor.venues.{name}")
        venue = getattr(module, "VENUE", None)
        if venue is None:
            raise VenueContractError(
                f"venue package {name!r} exports no VENUE spec — every package "
                "under condor/venues/ must implement the contract (condor.venues.spec)"
            )
        spec = _validate(name, venue)
        for venue_id in spec.venue_ids:
            if venue_id in owner:
                raise VenueContractError(
                    f"venue_id {venue_id!r} registered by both "
                    f"{owner[venue_id]!r} and {name!r}"
                )
            owner[venue_id] = name
            specs[venue_id] = spec
    _SPECS = specs
    return specs


def venue_spec(venue_id: str) -> VenueSpec:
    """The loaded VenueSpec for ``venue_id``; unknown ids error (§6.2b)."""
    specs = load_venue_packages()
    spec = specs.get(venue_id)
    if spec is None:
        raise UnknownVenueError(
            f"unregistered venue_id: {venue_id!r} (registered: {sorted(specs)})"
        )
    return spec

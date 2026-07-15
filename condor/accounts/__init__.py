"""Account model foundation (simplification plan §6.2b) — DORMANT in Phase 2.

The structured, custody-address-keyed account store, the minimal
VenueRegistry, and selector→AccountRef resolution. In Phase 2 these exist as
code + fixture tests only: the live system keeps booting and trading on the
existing flat ``store/venues.json`` path (``condor/executors/wallets.py``).
Phase 3 activates this store as the sole credential source, together with
the onboarding probe and the operator cutover.
"""

from condor.accounts.model import AccountRef, normalize_address
from condor.accounts.registry import VenueDef, VenueRegistry, default_registry
from condor.accounts.store import (
    AccountResolutionError,
    AccountStore,
    AccountStoreError,
    PreV1FormatError,
)

__all__ = [
    "AccountRef",
    "AccountResolutionError",
    "AccountStore",
    "AccountStoreError",
    "PreV1FormatError",
    "VenueDef",
    "VenueRegistry",
    "default_registry",
    "normalize_address",
]

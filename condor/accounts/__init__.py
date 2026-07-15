"""Account model (simplification plan §6.2b) — ACTIVATED in Phase 3.

The structured, custody-address-keyed account store, the minimal
VenueRegistry, selector→AccountRef resolution, and onboarding (custody
derivation + read-only probe). Since Phase 3 the store is the SOLE credential
source: the loaders in ``condor/executors/wallets.py`` resolve accounts here,
env-var credential precedence is deleted (``condor account import-env`` is
the explicit one-shot path), and a flat pre-v1 ``store/venues.json`` fails
with ``PreV1FormatError`` (operator cutover, §12).
"""

from condor.accounts.model import AccountRef, normalize_address
from condor.accounts.onboarding import (
    CustodyMismatchError,
    OnboardingError,
    ProbeError,
    derive_custody,
    onboard_account,
)
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
    "CustodyMismatchError",
    "OnboardingError",
    "PreV1FormatError",
    "ProbeError",
    "VenueDef",
    "VenueRegistry",
    "default_registry",
    "derive_custody",
    "normalize_address",
    "onboard_account",
]

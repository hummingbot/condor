"""The venue-package contract (§6.2b): one ``VENUE`` spec per package.

Each package under ``condor/venues/`` exports a single module-level ``VENUE``
(a :class:`VenueSpec`). The registry loader (``condor.venues.registry``)
imports every package at startup, validates the contract, and builds the
VenueRegistry everything else resolves venues through — **adding a venue =
adding one folder that implements this contract**, no core edits.

Callable signatures (validated as callables; documented here):

- ``adapter_factories[instrument](connector, cfg) -> InstrumentAdapter``
  for each supported instrument (``spot`` | ``perp`` | ``pred``).
- ``make_connector(instrument, credentials) -> client`` — ``credentials`` is
  the decrypted account-field dict augmented with the resolved identity
  context (``custody_address``, ``venue_id``, ``network``) by
  ``condor.executors.wallets.account_credentials``.
- ``derive_custody(credentials) -> str`` — the custody address derived FROM
  submitted credentials (onboarding step 1; never trusts a typed address).
- ``probe(venue_id, ref, credentials) -> None`` — read-only venue probe
  (onboarding step 2); raises on failure.
- ``normalize_instrument(cfg_or_str) -> str | None`` — canonical
  InstrumentRef: venue aliases for one market collapse to ONE id (lease keys
  and attribution can never split across aliases). Accepts an executor
  config object, a raw config dict, or a plain string; returns ``None`` when
  it has no opinion (the caller keeps its generic fallback).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

INSTRUMENTS = ("spot", "perp", "pred")


@dataclass(frozen=True)
class CredField:
    """One onboarding credential field: what forms render and the store seals."""

    name: str
    sealed: bool
    description: str = ""


@dataclass(frozen=True)
class VenueSpec:
    # Mainnet unsuffixed + suffixed deployments, e.g.
    # ("hyperliquid", "hyperliquid-testnet"). A suffixed id is a DIFFERENT
    # venue (deployment encoded in the id, never a mutable account field).
    venue_ids: tuple[str, ...]
    # "evm" | "verbatim" (condor.accounts.model.normalize_address styles).
    address_style: str
    # Deployment/network name per venue_id (what VenueDef.network reports),
    # e.g. {"hyperliquid": "mainnet", "hyperliquid-testnet": "testnet"}.
    networks: Mapping[str, str]
    credential_fields: tuple[CredField, ...]
    # instrument ("spot" | "perp" | "pred") -> factory(connector, cfg).
    adapter_factories: Mapping[str, Callable[..., Any]]
    make_connector: Callable[..., Any]
    derive_custody: Callable[..., Any]
    probe: Callable[..., Any]
    normalize_instrument: Callable[..., Any]


def field_value(cfg: Any, name: str) -> Any:
    """Read ``name`` from a config object or raw dict (normalize_instrument
    helpers accept both — executor configs live, record dicts on rebuild)."""
    if isinstance(cfg, Mapping):
        return cfg.get(name)
    return getattr(cfg, name, None)

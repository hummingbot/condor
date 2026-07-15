"""Account onboarding (§6.2b, Phase 3 — ACTIVE): custody derivation + probe.

Account creation, in order:

1. **derive the custody identity FROM the submitted credentials** — a typed
   address is never trusted: if the submission carries an address that does
   not match the derivation, onboarding fails (this is the "check account A,
   execute account B" failure mode the account model exists to prevent);
2. run a **read-only venue probe** (balance/account query) proving the
   credentials work on that ``venue_id`` before the account becomes
   selectable — a probe failure refuses to enable the account;
3. enforce **display-name uniqueness** within the venue (the store's
   validator), seal the secret fields (``enc:v1:``), and persist via
   ``AccountStore.upsert_account`` (address-keyed, so re-onboarding the same
   custody address is an idempotent edit, never a duplicate).

Custody derivation and the probe are implemented by each venue package's
``VENUE`` spec (``condor/venues/*``, §6.2b) and dispatched through the loaded
registry — this module owns only the venue-agnostic flow. For the built-ins:

- ``solana``: the pubkey of the keypair built from the submitted secret.
- ``hyperliquid``: the submitted ``account_address`` (EVM shape, normalized
  lowercase) — the ``agent_private_key`` signs FOR it, so the address is a
  credential component, verified live by the read-only account probe.
- ``polymarket``: the ``funder`` (proxy) when ``signature_type`` is 1 or 2,
  else the signer address derived from ``private_key``.

Probes are dependency-injectable: pass ``prober=`` to ``onboard_account`` or
monkeypatch ``_PROBERS`` — tests never touch the network.
"""

from __future__ import annotations

from typing import Callable, Optional

from condor.accounts.model import AccountRef, normalize_address
from condor.accounts.registry import default_registry
from condor.accounts.store import AccountStore


class OnboardingError(ValueError):
    """Credential validation / derivation failure."""


class CustodyMismatchError(OnboardingError):
    """A typed address does not match the custody derived from credentials."""


class ProbeError(OnboardingError):
    """The read-only venue probe failed — the account is not enabled."""


# Which submitted fields are sealed at rest (enc:v1:) is declared by each
# venue package's credential_fields metadata (condor.venues.spec.CredField
# sealed flags) — decrypted by the loaders in condor/executors/wallets.py.
# Non-secret fields (funder, signature_type, rpc_url, host, keystore_path,
# name) stay plaintext so the file remains human-inspectable. Service-level
# (non-account) secrets — e.g. the Jupiter data-API key — are NOT here; see
# wallets.save_service.

# Fields that duplicate identity already carried elsewhere and are therefore
# never persisted on the account entry: the map key IS the custody address,
# and network is derived from venue_id (§6.2b — never mutable account data).
_DROPPED_FIELDS = {"address", "custody_address", "account_address", "network", "name"}


def _family(venue_id: str) -> str:
    """Venue family for derivation/probe dispatch: ``hyperliquid-testnet``
    shares hyperliquid's credential shape (the deployment lives in the id)."""
    return venue_id.split("-", 1)[0]


# -- custody derivation --------------------------------------------------------


def _check_typed_address(credentials: dict, derived: str, style: str) -> None:
    """A submitted address must EQUAL the derived custody, else reject."""
    for field in ("address", "custody_address", "account_address"):
        typed = credentials.get(field)
        if not typed:
            continue
        try:
            normalized = normalize_address(str(typed), style)
        except ValueError as e:
            raise OnboardingError(f"{field}: {e}") from None
        if normalized != derived:
            raise CustodyMismatchError(
                f"submitted {field} {typed!r} does not match the custody "
                f"address derived from the credentials ({derived!r}) — "
                "custody is always derived, never typed"
            )


def derive_custody(venue_id: str, credentials: dict) -> AccountRef:
    """Derive the canonical AccountRef from submitted credentials (step 1).

    The per-venue derivation is the venue package's ``derive_custody``
    (§6.2b) — one source of truth, dispatched through the loaded specs.
    """
    registry = default_registry()
    venue = registry.get(venue_id)  # raises UnknownVenueError

    network = credentials.get("network")
    if network and network != venue.network:
        raise OnboardingError(
            f"credentials say network={network!r} but venue {venue_id!r} is "
            f"{venue.network!r} — a different deployment is a different "
            "venue_id, not an account field"
        )

    from condor.venues.registry import venue_spec

    derived = venue_spec(venue_id).derive_custody(credentials)
    return AccountRef(venue_id=venue_id, custody_address=str(derived))


# -- read-only probes ------------------------------------------------------------

# A prober takes (venue_id, ref, decrypted credentials) and raises on failure.
Prober = Callable[[str, AccountRef, dict], None]


_PROBERS: dict[str, Prober] = {}
"""Per-family probe OVERRIDES (tests monkeypatch entries in here). The real
probes live in the venue packages (``VENUE.probe``, §6.2b) — an empty table
means every venue probes through its package."""


def default_probe(venue_id: str, ref: AccountRef, credentials: dict) -> None:
    """Dispatch the venue's read-only probe; wrap failures as ProbeError.

    Resolution order: a ``_PROBERS`` override for the venue family (test
    seam), else the venue package's ``VENUE.probe``.
    """
    prober = _PROBERS.get(_family(venue_id))
    if prober is None:
        from condor.venues.registry import venue_spec

        prober = venue_spec(venue_id).probe  # raises UnknownVenueError
    try:
        prober(venue_id, ref, credentials)
    except OnboardingError:
        raise
    except Exception as e:
        raise ProbeError(
            f"{venue_id} read-only probe failed for {ref.custody_address}: {e} "
            "— account NOT enabled (fix the credentials and retry)"
        ) from e


# -- onboarding ------------------------------------------------------------------


def _default_store() -> AccountStore:
    from condor.executors.wallets import account_store

    return account_store()


def _sealed_fields(venue_id: str, credentials: dict, name: str) -> dict:
    from condor.executors.secrets import encrypt_secret, is_encrypted

    from condor.venues.registry import venue_spec

    secret_fields = {
        f.name for f in venue_spec(venue_id).credential_fields if f.sealed
    }
    fields: dict = {}
    for k, v in credentials.items():
        if k in _DROPPED_FIELDS or v is None:
            continue
        fields[k] = (
            encrypt_secret(str(v))
            if (k in secret_fields and not is_encrypted(v))
            else v
        )
    fields["name"] = name or ""
    return fields


def onboard_account(
    venue_id: str,
    credentials: dict,
    *,
    name: str = "",
    probe: bool = True,
    prober: Optional[Prober] = None,
    store: Optional[AccountStore] = None,
    make_default: bool = False,
) -> AccountRef:
    """The COMPLETE account-creation path (§6.2b onboarding steps 1–3).

    Derives custody FROM the credentials, probes read-only (refusing to
    enable on failure), seals secrets, and upserts address-keyed (idempotent:
    same custody address = edit, never duplicate). Display-name uniqueness is
    enforced by the store's validator at save time.
    """
    credentials = {k: v for k, v in dict(credentials or {}).items() if v is not None}
    ref = derive_custody(venue_id, credentials)
    if probe:
        (prober or default_probe)(venue_id, ref, credentials)
    fields = _sealed_fields(venue_id, credentials, name)
    store = store or _default_store()
    return store.upsert_account(
        venue_id, ref.custody_address, fields, make_default=make_default
    )

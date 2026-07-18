"""Polymarket venue package (§6.2b): prediction-market outcome tokens on the
CLOB. Custody is the funder (proxy) when ``signature_type`` is 1 or 2, else
the signer address derived from ``private_key``."""

from __future__ import annotations

from condor.accounts.model import normalize_address
from condor.accounts.onboarding import (
    CustodyMismatchError,
    OnboardingError,
    ProbeError,
    _check_typed_address,
)
from condor.venues.spec import CredField, VenueSpec, field_value

_NETWORKS = {"polymarket": "mainnet"}
_DEFAULT_HOST = "https://clob.polymarket.com"


# -- contract callables --------------------------------------------------------


def derive_custody(credentials: dict) -> str:
    """Custody = the funder (proxy) for signature_type 1/2, else the signer."""
    from eth_account import Account

    key = credentials.get("private_key")
    if not key:
        raise OnboardingError("polymarket onboarding needs private_key")
    try:
        signer = normalize_address(Account.from_key(key).address, "evm")
    except OnboardingError:
        raise
    except Exception as e:
        raise OnboardingError(f"private_key does not parse: {e}") from None
    sig_type = int(credentials.get("signature_type") or 0)
    if sig_type not in (0, 1, 2):
        raise OnboardingError(f"signature_type must be 0|1|2, got {sig_type}")
    funder = credentials.get("funder")
    if sig_type in (1, 2):
        if not funder:
            raise OnboardingError(
                f"polymarket signature_type={sig_type} needs the funder "
                "(proxy) address — it is the custody address"
            )
        try:
            return normalize_address(str(funder), "evm")
        except ValueError as e:
            raise OnboardingError(f"funder: {e}") from None
    # signature_type 0: custody = the signer itself; a mismatched funder
    # would silently rebind custody, so it is rejected like a typed address.
    if funder and normalize_address(str(funder), "evm") != signer:
        raise CustodyMismatchError(
            f"signature_type=0 custody is the signer ({signer!r}) but a "
            f"different funder {funder!r} was submitted — use "
            "signature_type 1/2 for proxy custody"
        )
    _check_typed_address(credentials, signer, "evm")
    return signer


def probe(venue_id: str, ref, credentials: dict) -> None:
    """Verify the client's signer matches the credentials, then read the USDC
    balance. A balance/auth failure raises ProbeWarning (not an error): the
    credentials are consistent, but the CLOB API is unreachable from this
    network — Polymarket geo-blocks several regions."""
    import asyncio

    from eth_account import Account

    from condor.venues.polymarket.client import PolymarketClient

    signer = normalize_address(
        Account.from_key(credentials["private_key"]).address, "evm"
    )
    sig_type = int(credentials.get("signature_type") or 0)
    client = PolymarketClient(
        credentials["private_key"],
        funder=credentials.get("funder"),
        signature_type=sig_type,
        host=credentials.get("host") or _DEFAULT_HOST,
    )
    got = normalize_address(str(client.address), "evm")
    if got != signer:
        raise ProbeError(
            f"polymarket client address {got!r} != signer {signer!r} — "
            "credentials are inconsistent"
        )
    try:
        asyncio.run(client.usdc_balance())
    except Exception as e:
        from condor.accounts.onboarding import ProbeWarning

        raise ProbeWarning(
            f"signer/custody verified, but the CLOB API rejected access "
            f"({type(e).__name__}: {e}) — likely geo-restriction "
            "(Polymarket blocks several regions); trading calls will fail "
            "from this network"
        ) from e


def make_connector(instrument: str, credentials: dict):
    if instrument != "pred":
        raise ValueError(f"polymarket does not support instrument {instrument!r}")
    from condor.venues.polymarket.client import PolymarketClient

    key = credentials.get("private_key")
    if not key:
        raise RuntimeError(
            f"polymarket account {credentials.get('custody_address')} has no "
            "private_key — re-onboard it"
        )
    creds = None
    if credentials.get("api_key"):
        creds = {
            "api_key": credentials.get("api_key"),
            "api_secret": credentials.get("api_secret"),
            "api_passphrase": credentials.get("api_passphrase"),
        }
    return PolymarketClient(
        key,
        funder=credentials.get("funder"),
        signature_type=int(credentials.get("signature_type") or 0),
        creds=creds,
        host=credentials.get("host") or _DEFAULT_HOST,
    )


def normalize_instrument(cfg_or_str) -> str | None:
    """The CLOB token_id IS the canonical instrument id — passthrough."""
    if isinstance(cfg_or_str, str):
        return cfg_or_str
    market = field_value(cfg_or_str, "market")
    if market:
        return str(market)
    return None


def _pred_adapter(connector, cfg):
    from condor.venues.polymarket.adapters import PolymarketPredAdapter

    return PolymarketPredAdapter(connector, cfg)


VENUE = VenueSpec(
    venue_ids=("polymarket",),
    address_style="evm",
    networks=_NETWORKS,
    credential_fields=(
        CredField("private_key", sealed=True, description="Polygon signing key"),
        CredField("funder", sealed=False, description="Proxy address holding USDC (custody for signature_type 1/2)"),
        CredField("signature_type", sealed=False, description="0=EOA, 1=Magic/email proxy, 2=Gnosis-safe proxy"),
        CredField("api_key", sealed=False, description="L2 API key (derived from the signing key when absent)"),
        CredField("api_secret", sealed=True, description="L2 API secret"),
        CredField("api_passphrase", sealed=True, description="L2 API passphrase"),
        CredField("relayer_api_key", sealed=True, description="Relayer API key (optional)"),
        CredField("relayer_address", sealed=False, description="Relayer address (optional)"),
        CredField("host", sealed=False, description="CLOB host (default https://clob.polymarket.com)"),
    ),
    adapter_factories={"pred": _pred_adapter},
    make_connector=make_connector,
    derive_custody=derive_custody,
    probe=probe,
    normalize_instrument=normalize_instrument,
)

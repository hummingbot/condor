"""Solana venue package (§6.2b): spot swaps via Jupiter over the native
chain connector. Custody is the pubkey of the keypair built from the
submitted secret (base58 secret key, or a Gateway keystore + passphrase)."""

from __future__ import annotations

from condor.accounts.onboarding import OnboardingError, _check_typed_address
from condor.venues.spec import CredField, VenueSpec, field_value

_NETWORKS = {"solana": "mainnet-beta"}


def _keypair(credentials: dict):
    """Keypair from submitted credentials (secret_key_b58, or keystore_path +
    keystore_passphrase). Onboarding-time twin of wallets.keypair_from_fields
    — errors here are OnboardingError, not a broken stored account."""
    import json
    from pathlib import Path

    from condor.executors.wallets import decrypt_gateway_keystore, keypair_from_secret

    secret = credentials.get("secret_key_b58")
    if secret:
        return keypair_from_secret(secret)
    ks_path = credentials.get("keystore_path")
    passphrase = credentials.get("keystore_passphrase")
    if ks_path and passphrase is not None:
        keystore = json.loads(Path(ks_path).expanduser().read_text())
        return keypair_from_secret(decrypt_gateway_keystore(keystore, passphrase))
    raise OnboardingError(
        "solana onboarding needs secret_key_b58, or "
        "keystore_path + keystore_passphrase"
    )


# -- contract callables --------------------------------------------------------


def derive_custody(credentials: dict) -> str:
    """Custody = the pubkey of the keypair built from the submitted secret."""
    derived = str(_keypair(credentials).pubkey())
    _check_typed_address(credentials, derived, "verbatim")
    return derived


def probe(venue_id: str, ref, credentials: dict) -> None:
    """Read-only native SOL balance query proving the keypair works."""
    import asyncio

    from condor.venues.solana.connector import DEFAULT_RPC, SolanaConnector

    keypair = _keypair(credentials)

    async def _go() -> None:
        conn = SolanaConnector(
            keypair, rpc_url=credentials.get("rpc_url") or DEFAULT_RPC
        )
        try:
            await conn.balance()  # native SOL balance — read-only
        finally:
            await conn.close()

    asyncio.run(_go())


def make_connector(instrument: str, credentials: dict):
    """Jupiter swap connector over the native chain connector (spot only)."""
    if instrument != "spot":
        raise ValueError(f"solana does not support instrument {instrument!r}")
    from condor.executors.wallets import keypair_from_fields
    from condor.venues.solana.connector import DEFAULT_RPC, SolanaConnector
    from condor.venues.solana.jupiter import JupiterConnector

    keypair = keypair_from_fields(credentials)
    custody = credentials.get("custody_address")
    if custody and str(keypair.pubkey()) != custody:
        raise RuntimeError(
            f"solana account {custody}: stored credentials derive "
            f"{keypair.pubkey()} — custody mismatch, re-onboard the account"
        )
    return JupiterConnector(
        SolanaConnector(keypair, rpc_url=credentials.get("rpc_url") or DEFAULT_RPC)
    )


def _canonical_token(token: str) -> str:
    """Collapse symbol aliases to the on-chain mint (SOL/WSOL/sol → the WSOL
    mint); unknown symbols/mints pass through verbatim (base58 is case-
    sensitive — never case-folded)."""
    from condor.venues.solana.jupiter import _SYMBOL_MINTS

    token = str(token).strip()
    return _SYMBOL_MINTS.get(token.upper(), token)


def normalize_instrument(cfg_or_str) -> str | None:
    """Canonical instrument id ``{base_mint}-{quote_mint}``: every alias of a
    market (SOL vs WSOL vs the mint itself) collapses to ONE id, so lease keys
    and attribution can never split across aliases."""
    if isinstance(cfg_or_str, str):
        return "-".join(_canonical_token(t) for t in cfg_or_str.split("-"))
    base = field_value(cfg_or_str, "base_token")
    quote = field_value(cfg_or_str, "quote_token")
    if base and quote:
        return f"{_canonical_token(base)}-{_canonical_token(quote)}"
    return None


def _spot_adapter(connector, cfg):
    from condor.executors.adapters import SpotAdapter

    return SpotAdapter(connector, cfg)


VENUE = VenueSpec(
    venue_ids=("solana",),
    address_style="verbatim",
    networks=_NETWORKS,
    credential_fields=(
        CredField("secret_key_b58", sealed=True, description="Base58 secret key (or JSON int array)"),
        CredField("keystore_path", sealed=False, description="Path to a Gateway wallet keystore (alternative to secret_key_b58)"),
        CredField("keystore_passphrase", sealed=True, description="Passphrase decrypting the Gateway keystore"),
        CredField("rpc_url", sealed=False, description="RPC endpoint (public default when omitted)"),
    ),
    adapter_factories={"spot": _spot_adapter},
    make_connector=make_connector,
    derive_custody=derive_custody,
    probe=probe,
    normalize_instrument=normalize_instrument,
)

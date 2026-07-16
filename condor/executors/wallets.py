"""Venue credential loading — the STRUCTURED account store, ACTIVATED (§6.2b).

Condor holds the signing key for native execution. This module is the single
place keys are loaded, so the rest of the code never touches raw secrets.

The sealed, account-keyed ``store/venues.json`` (see ``condor.accounts``) is
the ONLY credential source for trading:

- **Environment variables are never read by these loaders** — the old
  env-over-config precedence is deleted, not inverted. ``condor account
  import-env`` (``python -m condor.cli account import-env``) is the explicit
  one-shot path from env credentials into the store, through the same
  onboarding validation (custody derivation + read-only probe) as the
  dashboard.
- Every loader takes an optional ``account`` selector (custody address or
  display name); ``None`` resolves the venue's ``default_account``.
- A flat pre-v1 ``venues.json`` raises ``PreV1FormatError`` with its clear
  "unsupported pre-v1 format / re-onboard" message (operator cutover, §12).

Service-level API keys that are NOT account credentials (today: the Jupiter
data-API key) live under a reserved top-level ``"_services"`` block that
``AccountStore.validate`` skips — read via :func:`venue_config`, written
sealed via :func:`save_service`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from solders.keypair import Keypair

from condor.accounts import (
    AccountRef,
    AccountResolutionError,
    AccountStore,
    default_registry,
)

_VENUES_PATH = Path(__file__).resolve().parents[2] / "store" / "venues.json"
_SCRYPT_MAXMEM = 512 * 1024 * 1024  # covers Gateway's n=131072,r=8 (~134 MB)

# Sealed fields for service-level (non-account) config under "_services".
# Account-credential sealing is declared by each venue package's
# credential_fields metadata (condor.venues.spec.CredField sealed flags).
_SERVICE_SECRET_FIELDS: dict[str, set[str]] = {
    "jupiter": {"api_key"},
}


def account_store() -> AccountStore:
    """The live structured store. One knob (``_VENUES_PATH``) for tests."""
    return AccountStore(path=_VENUES_PATH)


def _decrypted(fields: dict) -> dict:
    from condor.executors.secrets import decrypt_secret, is_encrypted

    return {k: (decrypt_secret(v) if is_encrypted(v) else v) for k, v in fields.items()}


def _account_fields(venue_id: str, account: Optional[str]) -> tuple[AccountRef, dict]:
    """Resolve (venue, selector) → (AccountRef, decrypted account fields).

    PreV1FormatError from AccountStore.load propagates unchanged (its message
    already says re-onboard); an unresolvable selector gets the onboarding
    hint appended because "I set the env var, why won't it trade" is the
    expected post-cutover question.
    """
    store = account_store()
    try:
        ref = store.resolve(venue_id, account)
    except AccountResolutionError as e:
        raise AccountResolutionError(
            f"{e} — onboard via the dashboard or `python -m condor.cli account "
            "import-env` (env credentials alone are no longer read)"
        ) from None
    return ref, _decrypted(store.account_fields(ref))


def account_credentials(venue_id: str, account: Optional[str] = None) -> dict:
    """Decrypted account fields + resolved identity context for a venue
    package's ``make_connector`` (§6.2b): the stored fields plus
    ``custody_address`` (the map key), ``venue_id``, and ``network`` (derived
    from the venue id — never a mutable account field). Venue-agnostic: any
    registered venue resolves through the same path."""
    ref, cfg = _account_fields(venue_id, account)
    cfg["custody_address"] = ref.custody_address
    cfg["venue_id"] = ref.venue_id
    cfg["network"] = default_registry().get(ref.venue_id).network
    return cfg


# -- service config (non-account) ---------------------------------------------


def venue_config(name: str) -> dict:
    """Service-level config with sealed fields decrypted; ``{}`` when absent.

    This is NOT account credentials — it reads the reserved ``"_services"``
    block (e.g. ``venue_config("jupiter")`` → the data-API key used by
    ``JupiterSwap``). Account credentials go through the ``load_*`` loaders.
    """
    services = account_store().load().get("_services", {})
    return _decrypted(dict(services.get(name, {})))


def save_service(name: str, fields: dict) -> dict:
    """Write service-level config under ``"_services"``, sealing its secret
    fields. Returns the stored (sealed) entry."""
    from condor.executors.secrets import encrypt_secret, is_encrypted

    if name not in _SERVICE_SECRET_FIELDS:
        raise ValueError(
            f"unknown service {name!r} (known: {sorted(_SERVICE_SECRET_FIELDS)})"
        )
    store = account_store()
    with store.transaction() as data:
        entry = dict(data.get("_services", {}).get(name, {}))
        secret_fields = _SERVICE_SECRET_FIELDS[name]
        for k, v in fields.items():
            if v is None:
                continue
            entry[k] = (
                encrypt_secret(str(v))
                if (k in secret_fields and not is_encrypted(v))
                else v
            )
        data.setdefault("_services", {})[name] = entry
    return entry


# -- Solana ------------------------------------------------------------------


def decrypt_gateway_keystore(keystore: dict, passphrase: str) -> str:
    """Decrypt a Gateway wallet keystore (scrypt KDF + AES-256-GCM) to its
    secret. Gateway stores the Solana secret key as a base58 string."""
    if keystore.get("kdf") != "scrypt" or keystore.get("cipher") != "aes-256-gcm":
        raise ValueError(
            f"unsupported keystore format: kdf={keystore.get('kdf')} cipher={keystore.get('cipher')}"
        )
    p = keystore["kdfparams"]
    key = hashlib.scrypt(
        passphrase.encode(),
        salt=bytes.fromhex(p["salt"]),
        n=p["n"],
        r=p["r"],
        p=p["p"],
        dklen=p["dklen"],
        maxmem=_SCRYPT_MAXMEM,
    )
    ct = bytes.fromhex(keystore["ciphertext"])
    iv = bytes.fromhex(keystore["cipherparams"]["iv"])
    mac = bytes.fromhex(keystore["mac"])  # AES-GCM auth tag
    return AESGCM(key).decrypt(iv, ct + mac, None).decode()


def keypair_from_secret(secret: str) -> Keypair:
    """Build a Keypair from a base58 secret key string or a JSON int array."""
    secret = secret.strip()
    if secret.startswith("["):
        return Keypair.from_bytes(bytes(json.loads(secret)))
    return Keypair.from_base58_string(secret)


def keypair_from_fields(fields: dict) -> Keypair:
    """Keypair from decrypted account fields: ``secret_key_b58``, or
    ``keystore_path`` + ``keystore_passphrase`` (Gateway keystore)."""
    secret = fields.get("secret_key_b58")
    if secret:
        return keypair_from_secret(secret)
    ks_path = fields.get("keystore_path")
    passphrase = fields.get("keystore_passphrase")
    if ks_path and passphrase is not None:
        keystore = json.loads(Path(ks_path).expanduser().read_text())
        return keypair_from_secret(decrypt_gateway_keystore(keystore, passphrase))
    raise RuntimeError(
        "solana account has no signing material (secret_key_b58, or "
        "keystore_path + keystore_passphrase) — re-onboard it"
    )


def load_solana_keypair(account: Optional[str] = None) -> Keypair:
    """Load a Solana signing key from the structured store.

    The derived pubkey must equal the account's custody address (the map
    key) — a mismatch means the entry was hand-edited past onboarding's
    derivation and is refused rather than trading the wrong wallet.
    """
    ref, cfg = _account_fields("solana", account)
    keypair = keypair_from_fields(cfg)
    derived = str(keypair.pubkey())
    if derived != ref.custody_address:
        raise RuntimeError(
            f"solana account {ref.custody_address}: stored credentials derive "
            f"{derived} — custody mismatch, re-onboard the account"
        )
    return keypair


def solana_rpc_url(account: Optional[str] = None) -> str:
    """The account's configured RPC url, else the public default. With no
    selector and no configured solana account, market-data reads still get
    the public RPC (signing loaders are what require an account)."""
    from condor.executors.solana import DEFAULT_RPC

    try:
        _, cfg = _account_fields("solana", account)
    except AccountResolutionError:
        if account is not None:
            raise
        return DEFAULT_RPC
    return cfg.get("rpc_url") or DEFAULT_RPC


def make_solana_connector(account: Optional[str] = None):
    from condor.executors.solana import DEFAULT_RPC, SolanaConnector

    ref, cfg = _account_fields("solana", account)
    keypair = keypair_from_fields(cfg)
    if str(keypair.pubkey()) != ref.custody_address:
        raise RuntimeError(
            f"solana account {ref.custody_address}: stored credentials derive "
            f"{keypair.pubkey()} — custody mismatch, re-onboard the account"
        )
    return SolanaConnector(keypair, rpc_url=cfg.get("rpc_url") or DEFAULT_RPC)


# -- Hyperliquid -------------------------------------------------------------


def load_hyperliquid_creds(
    account: Optional[str] = None, *, venue_id: str = "hyperliquid"
) -> dict:
    """Return {agent_private_key, account_address, network} from the store.

    ``account_address`` is the resolved custody address (the map key);
    ``network`` derives from ``venue_id`` (``hyperliquid-testnet`` is a
    different venue, never a mutable account field).
    """
    ref, cfg = _account_fields(venue_id, account)
    agent_key = cfg.get("agent_private_key")
    if not agent_key:
        raise RuntimeError(
            f"{venue_id} account {ref.custody_address} has no "
            "agent_private_key — re-onboard it"
        )
    network = default_registry().get(ref.venue_id).network
    return {
        "agent_private_key": agent_key,
        "account_address": ref.custody_address,
        "network": network,
    }


# -- Polymarket --------------------------------------------------------------


def load_polymarket_creds(account: Optional[str] = None) -> dict:
    """Return the Polymarket connector config from the store. Requires the
    Polygon signing key; API creds are optional (derived when absent)."""
    ref, cfg = _account_fields("polymarket", account)
    key = cfg.get("private_key")
    if not key:
        raise RuntimeError(
            f"polymarket account {ref.custody_address} has no private_key — "
            "re-onboard it"
        )
    out = {
        "private_key": key,
        "funder": cfg.get("funder"),
        "signature_type": int(cfg.get("signature_type") or 0),
        "host": cfg.get("host") or "https://clob.polymarket.com",
    }
    if cfg.get("api_key"):
        out["creds"] = {
            "api_key": cfg.get("api_key"),
            "api_secret": cfg.get("api_secret"),
            "api_passphrase": cfg.get("api_passphrase"),
        }
    if cfg.get("relayer_api_key"):
        out["relayer"] = {
            "api_key": cfg.get("relayer_api_key"),
            "address": cfg.get("relayer_address"),
        }
    return out


def make_polymarket_client(account: Optional[str] = None):
    from condor.executors.polymarket import PolymarketClient

    c = load_polymarket_creds(account)
    return PolymarketClient(
        c["private_key"],
        funder=c.get("funder"),
        signature_type=c["signature_type"],
        creds=c.get("creds"),
        host=c["host"],
    )

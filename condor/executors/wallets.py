"""Venue credential loading — Solana keypair + Hyperliquid agent key.

Condor holds the signing key for native execution. This module is the single
place keys are loaded, so the rest of the code never touches raw secrets.

Config sources, in priority order (first hit wins), per venue:
  1. environment variables (below) — for the daemon / CI
  2. ``store/venues.json`` (git-ignored) — the "import a wallet" MVP target,
     written by the dashboard; a JSON object keyed by venue name

No fallbacks: if a required field is missing the loader raises a clear error.

Solana env:
  CONDOR_SOLANA_SECRET_KEY   base58 secret key (64-byte), or JSON int array
  CONDOR_SOLANA_KEYSTORE     path to a Gateway keystore json (scrypt+aes-256-gcm)
  CONDOR_SOLANA_PASSPHRASE   passphrase for the keystore
  CONDOR_SOLANA_RPC          RPC url (else store/venues.json, else public)

Hyperliquid env:
  CONDOR_HL_AGENT_KEY        agent (API-wallet) private key, 0x…
  CONDOR_HL_ACCOUNT_ADDRESS  main account address the agent trades for, 0x…
  CONDOR_HL_NETWORK          "mainnet" | "testnet"
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from solders.keypair import Keypair

_VENUES_PATH = Path(__file__).resolve().parents[2] / "store" / "venues.json"
_SCRYPT_MAXMEM = 512 * 1024 * 1024  # covers Gateway's n=131072,r=8 (~134 MB)

# Secret fields per venue — sealed at rest by save_venue(), decrypted on read.
# Non-secret fields (addresses, funder, network, rpc_url, signature_type) stay
# plaintext so the file is still human-inspectable.
_SECRET_FIELDS: dict[str, set[str]] = {
    "solana": {"secret_key_b58", "keystore_passphrase"},
    "hyperliquid": {"agent_private_key"},
    "polymarket": {"private_key", "api_secret", "api_passphrase", "relayer_api_key"},
    "jupiter": {"api_key"},
}


def _venues_file() -> dict:
    if _VENUES_PATH.exists():
        return json.loads(_VENUES_PATH.read_text())
    return {}


def venue_config(name: str) -> dict:
    """A venue's stored config, with any sealed secret fields decrypted.

    Env vars still override these in each ``load_*`` (env wins) — this is the
    imported-wallet source the dashboard writes via :func:`save_venue`.
    """
    from condor.executors.secrets import decrypt_secret, is_encrypted

    cfg = dict(_venues_file().get(name, {}))
    return {k: (decrypt_secret(v) if is_encrypted(v) else v) for k, v in cfg.items()}


def save_venue(name: str, fields: dict) -> dict:
    """Import/update a venue's credentials in ``store/venues.json``, sealing its
    secret fields with AES-256-GCM at rest. The single writer behind the
    dashboard's "import a wallet" flow. Returns the stored (sealed) entry.

    Merges into any existing entry; ``None`` values are skipped. Already-sealed
    values pass through untouched (idempotent re-saves)."""
    from condor.executors.secrets import encrypt_secret, is_encrypted

    if name not in _SECRET_FIELDS:
        raise ValueError(f"unknown venue {name!r} (known: {sorted(_SECRET_FIELDS)})")
    data = _venues_file()
    entry = dict(data.get(name, {}))
    secret_fields = _SECRET_FIELDS[name]
    for k, v in fields.items():
        if v is None:
            continue
        entry[k] = encrypt_secret(str(v)) if (k in secret_fields and not is_encrypted(v)) else v
    data[name] = entry

    _VENUES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _VENUES_PATH.with_name(_VENUES_PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(_VENUES_PATH)
    return entry


def configured_venues() -> dict[str, list[str]]:
    """Which venues have an imported entry and which fields they carry (names
    only — never secret values). For the dashboard's status view."""
    return {name: sorted(entry.keys()) for name, entry in _venues_file().items()}


def import_env_to_store() -> dict[str, list[str]]:
    """One-shot migration: seal any credentials currently in the environment into
    the encrypted venues store, so they need not live in ``.env`` anymore.
    Idempotent; returns the fields written per venue. After running, the matching
    ``CONDOR_*`` lines can be removed from ``.env`` (env still wins when present)."""
    env = os.environ.get
    plan = {
        "solana": {
            "secret_key_b58": env("CONDOR_SOLANA_SECRET_KEY"),
            "keystore_path": env("CONDOR_SOLANA_KEYSTORE"),
            "keystore_passphrase": env("CONDOR_SOLANA_PASSPHRASE"),
            "rpc_url": env("CONDOR_SOLANA_RPC"),
        },
        "hyperliquid": {
            "agent_private_key": env("CONDOR_HL_AGENT_KEY"),
            "account_address": env("CONDOR_HL_ACCOUNT_ADDRESS"),
            "network": env("CONDOR_HL_NETWORK"),
        },
        "polymarket": {
            "private_key": env("CONDOR_POLYMARKET_KEY"),
            "funder": env("CONDOR_POLYMARKET_FUNDER"),
            "signature_type": env("CONDOR_POLYMARKET_SIG_TYPE"),
            "api_key": env("CONDOR_POLYMARKET_API_KEY"),
            "api_secret": env("CONDOR_POLYMARKET_API_SECRET"),
            "api_passphrase": env("CONDOR_POLYMARKET_API_PASSPHRASE"),
        },
        "jupiter": {"api_key": env("CONDOR_JUPITER_API_KEY")},
    }
    written: dict[str, list[str]] = {}
    for venue, fields in plan.items():
        present = {k: v for k, v in fields.items() if v}
        if present:
            entry = save_venue(venue, present)
            written[venue] = sorted(entry.keys())
    return written


# -- Solana ------------------------------------------------------------------

def decrypt_gateway_keystore(keystore: dict, passphrase: str) -> str:
    """Decrypt a Gateway wallet keystore (scrypt KDF + AES-256-GCM) to its
    secret. Gateway stores the Solana secret key as a base58 string."""
    if keystore.get("kdf") != "scrypt" or keystore.get("cipher") != "aes-256-gcm":
        raise ValueError(f"unsupported keystore format: kdf={keystore.get('kdf')} cipher={keystore.get('cipher')}")
    p = keystore["kdfparams"]
    key = hashlib.scrypt(
        passphrase.encode(), salt=bytes.fromhex(p["salt"]),
        n=p["n"], r=p["r"], p=p["p"], dklen=p["dklen"], maxmem=_SCRYPT_MAXMEM,
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


def load_solana_keypair(config: Optional[dict] = None) -> Keypair:
    """Load the Solana signing key from env / venues.json / an explicit config."""
    cfg = dict(config or venue_config("solana"))
    secret = os.environ.get("CONDOR_SOLANA_SECRET_KEY") or cfg.get("secret_key_b58")
    if secret:
        return keypair_from_secret(secret)

    ks_path = os.environ.get("CONDOR_SOLANA_KEYSTORE") or cfg.get("keystore_path")
    passphrase = os.environ.get("CONDOR_SOLANA_PASSPHRASE")
    if passphrase is None:
        passphrase = cfg.get("keystore_passphrase")
    if ks_path and passphrase is not None:
        keystore = json.loads(Path(ks_path).expanduser().read_text())
        return keypair_from_secret(decrypt_gateway_keystore(keystore, passphrase))

    raise RuntimeError(
        "no Solana key configured — set CONDOR_SOLANA_SECRET_KEY, or "
        "CONDOR_SOLANA_KEYSTORE + CONDOR_SOLANA_PASSPHRASE, or add a 'solana' "
        "entry to store/venues.json"
    )


def solana_rpc_url(config: Optional[dict] = None) -> str:
    from condor.executors.solana import DEFAULT_RPC

    cfg = dict(config or venue_config("solana"))
    return os.environ.get("CONDOR_SOLANA_RPC") or cfg.get("rpc_url") or DEFAULT_RPC


def make_solana_connector(config: Optional[dict] = None):
    from condor.executors.solana import SolanaConnector

    cfg = dict(config or venue_config("solana"))
    return SolanaConnector(load_solana_keypair(cfg), rpc_url=solana_rpc_url(cfg))


# -- Hyperliquid -------------------------------------------------------------

def load_hyperliquid_creds(config: Optional[dict] = None) -> dict:
    """Return {agent_private_key, account_address, network}. Raises if incomplete."""
    cfg = dict(config or venue_config("hyperliquid"))
    agent_key = os.environ.get("CONDOR_HL_AGENT_KEY") or cfg.get("agent_private_key")
    account = os.environ.get("CONDOR_HL_ACCOUNT_ADDRESS") or cfg.get("account_address")
    network = os.environ.get("CONDOR_HL_NETWORK") or cfg.get("network") or "mainnet"
    if not agent_key or not account:
        raise RuntimeError(
            "no Hyperliquid creds — set CONDOR_HL_AGENT_KEY + CONDOR_HL_ACCOUNT_ADDRESS, "
            "or add a 'hyperliquid' entry to store/venues.json"
        )
    if network not in ("mainnet", "testnet"):
        raise ValueError(f"CONDOR_HL_NETWORK must be mainnet|testnet, got {network!r}")
    return {"agent_private_key": agent_key, "account_address": account, "network": network}


# -- Polymarket --------------------------------------------------------------

def load_polymarket_creds(config: Optional[dict] = None) -> dict:
    """Return the Polymarket connector config. Requires a Polygon signing key;
    API creds are optional (derived from the key when absent).

    Env: CONDOR_POLYMARKET_KEY, _FUNDER, _SIG_TYPE (0|1|2), _HOST,
    and optionally _API_KEY / _API_SECRET / _API_PASSPHRASE.
    """
    cfg = dict(config or venue_config("polymarket"))
    key = os.environ.get("CONDOR_POLYMARKET_KEY") or cfg.get("private_key")
    if not key:
        raise RuntimeError(
            "no Polymarket key — set CONDOR_POLYMARKET_KEY, or add a 'polymarket' "
            "entry to store/venues.json"
        )
    sig_type = int(os.environ.get("CONDOR_POLYMARKET_SIG_TYPE") or cfg.get("signature_type") or 0)
    out = {
        "private_key": key,
        "funder": os.environ.get("CONDOR_POLYMARKET_FUNDER") or cfg.get("funder"),
        "signature_type": sig_type,
        "host": os.environ.get("CONDOR_POLYMARKET_HOST") or cfg.get("host") or "https://clob.polymarket.com",
    }
    api_key = os.environ.get("CONDOR_POLYMARKET_API_KEY") or cfg.get("api_key")
    if api_key:
        out["creds"] = {
            "api_key": api_key,
            "api_secret": os.environ.get("CONDOR_POLYMARKET_API_SECRET") or cfg.get("api_secret"),
            "api_passphrase": os.environ.get("CONDOR_POLYMARKET_API_PASSPHRASE") or cfg.get("api_passphrase"),
        }
    relayer_key = os.environ.get("CONDOR_POLYMARKET_RELAYER_API_KEY") or cfg.get("relayer_api_key")
    if relayer_key:
        out["relayer"] = {
            "api_key": relayer_key,
            "address": os.environ.get("CONDOR_POLYMARKET_RELAYER_ADDRESS") or cfg.get("relayer_address"),
        }
    return out


def make_polymarket_client(config: Optional[dict] = None):
    from condor.executors.polymarket import PolymarketClient

    c = load_polymarket_creds(config)
    return PolymarketClient(
        c["private_key"], funder=c.get("funder"), signature_type=c["signature_type"],
        creds=c.get("creds"), host=c["host"],
    )

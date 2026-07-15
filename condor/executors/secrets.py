"""Encryption at rest for imported venue credentials.

The dashboard's "import a wallet" flow writes secrets to ``store/venues.json``.
This module seals the secret fields with AES-256-GCM so the file never holds a
plaintext key. The master key is resolved once:

  1. ``CONDOR_SECRETS_KEY`` — base64 of 32 bytes (the daemon / CI / a KMS drop)
  2. else a locally generated keyfile ``store/.secrets_key`` (0600), created on
     first use so a fresh install works without configuration.

Sealed values are tagged ``enc:v1:<base64(nonce||ciphertext)>`` so the reader
tells ciphertext from plaintext and decrypts transparently — plaintext values
(e.g. a hand-edited entry) still load unchanged.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = "enc:v1:"
_KEYFILE = Path(__file__).resolve().parents[2] / "store" / ".secrets_key"


def _master_key() -> bytes:
    env = os.environ.get("CONDOR_SECRETS_KEY")
    if env:
        key = base64.b64decode(env)
        if len(key) != 32:
            raise ValueError("CONDOR_SECRETS_KEY must be base64 of exactly 32 bytes")
        return key
    if _KEYFILE.exists():
        key = base64.b64decode(_KEYFILE.read_text().strip())
        if len(key) != 32:
            raise ValueError(f"corrupt secrets keyfile {_KEYFILE} (expected 32 bytes)")
        return key
    # First run: generate + persist a fresh key, readable only by this user.
    key = AESGCM.generate_key(bit_length=256)
    _KEYFILE.parent.mkdir(parents=True, exist_ok=True)
    _KEYFILE.write_text(base64.b64encode(key).decode())
    os.chmod(_KEYFILE, 0o600)
    return key


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_secret(plaintext: str) -> str:
    """Seal a plaintext secret to an ``enc:v1:`` token."""
    nonce = os.urandom(12)
    ct = AESGCM(_master_key()).encrypt(nonce, plaintext.encode(), None)
    return _PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt_secret(token: str) -> str:
    """Open a sealed token; a plaintext (untagged) value is returned unchanged."""
    if not is_encrypted(token):
        return token
    raw = base64.b64decode(token[len(_PREFIX):])
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(_master_key()).decrypt(nonce, ct, None).decode()

"""Attaching a browser wallet, for tests that need one attached.

A real Ed25519 key and a real signature rather than a hand-written
``wallet.json``: what the store writes on attach is part of what these tests are
about, so a fixture that skipped the signature would be asserting a file format.

Condor itself has no base58 *encoder* — it only ever decodes a key someone else
produced (``condor/solana_keys.py``) — so one lives here. Leading zero bytes are
the part worth writing once: base58 drops them, and each has to come back as a
``1`` or the address is 31 bytes and ``is_pubkey`` refuses it. A generated key
starts with one about one time in 256, which is exactly often enough to look
like a flake rather than a bug.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from condor import wallet_store
from condor.solana_keys import ALPHABET


def b58encode(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    out = ""
    while number:
        number, digit = divmod(number, 58)
        out = ALPHABET[digit] + out
    leading_zeros = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * leading_zeros + out


def attach_wallet(user_id: int) -> str:
    """Attach a fresh wallet to ``user_id`` the way a browser does. Its address."""
    key = Ed25519PrivateKey.generate()
    address = b58encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    issued = wallet_store.issue_nonce(user_id, address)
    signature = key.sign(issued["message"].encode("utf-8"))
    wallet_store.attach(user_id, address, signature, issued["nonce"])
    return address

"""Base58 and Ed25519, the two things Condor needs to check a Solana signature.

Deliberately small and dependency-free. Verifying a sign-in message needs a
public key decoded from base58 and one Ed25519 check; ``cryptography`` (already
here, under ``python-jose[cryptography]``) does the second, and the first is
forty lines that will never change — Bitcoin's alphabet was fixed in 2009 and
Solana uses it unaltered.

No signing lives here and none ever should: Condor holds no user key (plan D2).
The only private keys in the system are Gateway's, and they stay in Gateway.
"""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Bitcoin's alphabet: no 0, O, I or l, so a key read aloud or retyped cannot
# land on a different one by way of a lookalike glyph.
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(ALPHABET)}

PUBKEY_LEN = 32


class InvalidBase58(ValueError):
    """The string is not base58, or not the length the caller wanted."""


def b58decode(value: str) -> bytes:
    """Decode base58. Raises :class:`InvalidBase58` on any character outside
    the alphabet, so a typo is an error rather than a different key."""
    if not value:
        raise InvalidBase58("empty string")
    num = 0
    for char in value:
        digit = _INDEX.get(char)
        if digit is None:
            raise InvalidBase58(f"{char!r} is not a base58 character")
        num = num * 58 + digit
    # Every leading '1' is a leading zero byte; the integer above cannot carry
    # them, so they are counted and put back.
    leading = len(value) - len(value.lstrip("1"))
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    return b"\0" * leading + body


def is_pubkey(value: str) -> bool:
    """Whether ``value`` decodes to 32 bytes — the shape of every Solana
    address. Not a claim that the point is on the curve: PDAs are off it by
    construction, and both are addresses."""
    try:
        return len(b58decode(value)) == PUBKEY_LEN
    except InvalidBase58:
        return False


def require_pubkey(value: str, *, label: str = "address") -> str:
    if not isinstance(value, str) or not is_pubkey(value):
        raise ValueError(f"{label} is not a base58 Solana address: {value!r}")
    return value


def verify_ed25519(pubkey_b58: str, message: bytes, signature: bytes) -> bool:
    """Whether ``signature`` is ``pubkey_b58``'s over ``message``.

    Returns False rather than raising for every kind of failure — a malformed
    key, a wrong-length signature, a mismatch — because the caller's answer is
    the same in each case and a distinguishing error is an oracle.
    """
    try:
        raw = b58decode(pubkey_b58)
    except InvalidBase58:
        return False
    if len(raw) != PUBKEY_LEN or len(signature) != 64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False

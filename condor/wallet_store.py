"""The wallet a Condor user has proved they control (plan D13, M2).

Condor never holds a user's key. What it holds is the *claim* that a browser
wallet belongs to a Condor login, established once by a signature over a
one-time message and kept here. Everything a runner is allowed to do — read a
vault's private config, be recorded as its runner, sign its builds — is gated on
that claim.

Two stores, deliberately different in lifetime:

* **Nonces** — in memory, five minutes, single use. They exist so a signature
  captured from one attach cannot be replayed into another (threat model, §1.6).
  Losing them on restart costs a user one click.
* **The attachment** — ``paths.user_dir(user_id)/wallet.json``, one wallet per
  user (D13). Not secret: an address is public. Per-user because the file *is*
  the answer to "who is this vault's runner", and a shared file is how that
  answer drifts.

Detaching is refused while the user still has a vault that is running — see
``routes/wallet.py``, which owns that rule because it is the vault store that
knows.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from typing import Any, Optional

from condor import paths
from condor.fsutil import atomic_write_json
from condor.solana_keys import is_pubkey, verify_ed25519

log = logging.getLogger(__name__)

NONCE_TTL_S = 300
# The domain that appears in the signed message. Not a URL: a wallet shows this
# to the person signing, and an address with a scheme reads like a link they can
# check, which it is not.
SIGN_IN_DOMAIN = "condor"

_lock = threading.Lock()
# nonce -> {user_id, issued_at, issued_iso}
_nonces: dict[str, dict[str, Any]] = {}


# ── the signed message ────────────────────────────────────────────────────────


def sign_in_message(address: str, user_id: int, nonce: str, issued_iso: str) -> str:
    """The exact bytes a wallet signs.

    Built on the server and handed to the browser verbatim, rather than
    assembled on both sides from parts. Two implementations of one message is
    how a wallet ends up signing something that no longer verifies — the same
    reason the canonical config encoding is one function (plan §4).
    """
    return (
        f"{SIGN_IN_DOMAIN} wants you to sign in with your Solana account:\n"
        f"{address}\n"
        "\n"
        f"Attach this wallet to Condor account {user_id}. It becomes the runner "
        "of the vaults you create. Condor never asks for your private key.\n"
        "\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_iso}"
    )


def _gc(now: float) -> None:
    for key in [k for k, v in _nonces.items() if now - v["issued_at"] > NONCE_TTL_S]:
        _nonces.pop(key, None)


def issue_nonce(user_id: int, address: str) -> dict[str, str]:
    """A fresh nonce for ``user_id`` and the message to sign with it."""
    if not is_pubkey(address):
        raise ValueError(f"not a Solana address: {address!r}")
    now = time.time()
    nonce = secrets.token_urlsafe(24)
    issued_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    with _lock:
        _gc(now)
        _nonces[nonce] = {
            "user_id": user_id,
            "address": address,
            "issued_at": now,
            "issued_iso": issued_iso,
        }
    return {
        "nonce": nonce,
        "issued_at": issued_iso,
        "message": sign_in_message(address, user_id, nonce, issued_iso),
    }


class AttachRefused(Exception):
    """The proof did not hold. The message says which part."""


def redeem_nonce(nonce: str, user_id: int, address: str) -> str:
    """Spend ``nonce`` and return the message it was issued for.

    Single use: popped before anything else is checked, so a signature that
    fails verification still burns its nonce rather than leaving one to retry
    against.
    """
    now = time.time()
    with _lock:
        _gc(now)
        record = _nonces.pop(nonce, None)
    if record is None:
        raise AttachRefused("that sign-in request has expired — ask for a new one")
    if record["user_id"] != user_id:
        raise AttachRefused("that sign-in request belongs to another account")
    if record["address"] != address:
        raise AttachRefused("that sign-in request was issued for a different address")
    return sign_in_message(address, user_id, nonce, record["issued_iso"])


# ── the attachment ────────────────────────────────────────────────────────────


def _path(user_id: int):
    return paths.user_dir(user_id) / "wallet.json"


def get_wallet(user_id: int) -> Optional[dict[str, Any]]:
    path = _path(user_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("wallet.json for user %s is unreadable (%s)", user_id, exc)
        return None
    return data if isinstance(data, dict) and data.get("address") else None


def attach(user_id: int, address: str, signature: bytes, nonce: str) -> dict[str, Any]:
    """Verify the signature over the nonce's message and record the wallet."""
    if not is_pubkey(address):
        raise AttachRefused(f"not a Solana address: {address!r}")
    message = redeem_nonce(nonce, user_id, address)
    if not verify_ed25519(address, message.encode("utf-8"), signature):
        raise AttachRefused("the signature does not match that address")
    record = {"address": address, "attached_at": int(time.time())}
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, record)
    log.info("user %s attached wallet %s", user_id, address)
    return record


def detach(user_id: int) -> bool:
    """Forget the wallet. True if there was one."""
    path = _path(user_id)
    if not path.exists():
        return False
    path.unlink()
    log.info("user %s detached their wallet", user_id)
    return True

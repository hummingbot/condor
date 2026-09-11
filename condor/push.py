"""Which browsers this install may ring, and the key it rings them with (FEAT-083).

The notification bell (``condor/notifications.py``) already stores every finished
routine, delegation and agent notice, and ``chat_ws.py`` already pushes them to
whatever tabs are open. This module is the half that survives *nobody being
there*: a browser hands over a Web Push subscription once, it is kept here, and
the sink in ``condor/web/routes/push.py`` uses it to reach the operating system
with no Condor window open at all.

Two things live here, and they are deliberately different in kind:

* **Subscriptions** -- ``push_subscriptions.json``, the third store to follow
  :func:`condor.paths.notifications_path`'s shape: a dict keyed by user id, read
  tolerantly, written with :func:`condor.fsutil.atomic_write_json` under a
  module-level lock. Not secret (the endpoint is a URL the push service handed
  the browser, and the keys are the *browser's* public key and auth secret --
  they let us encrypt *to* it, they do not let us read anything), but they are
  per-user and the store is keyed that way so no route can accidentally hand one
  person's devices to another.

* **The VAPID keypair** -- ``push_keys/vapid.json``, which *is* secret. It is
  this install's identity to Google's, Mozilla's and Apple's push services.
  Generated once, on first use, and **never rewritten**: rotating it silently
  invalidates every subscription that exists, and the failure looks like "push
  stopped working" with no error anywhere. It is never logged, and only its
  *public* half ever leaves the process.

Nothing here talks to a push service. Sending is the surface's job, because
``condor.notifications`` must not import web code -- the same rule
``chat_ws.py`` states at its ``register_push_sink`` call.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from condor import paths
from condor.fsutil import atomic_write_json

log = logging.getLogger(__name__)

# One person does not have unbounded browsers. A cap means a store that only
# ever grows -- a browser that re-subscribes under a new endpoint on every
# permission reset -- cannot turn one notification into an unbounded fan-out.
_MAX_PER_USER = 20

# Serialises the read-modify-write below, exactly like ``notifications._write_lock``:
# a subscribe landing while the sink is pruning a dead endpoint would otherwise
# have one of the two writes drop the other.
_write_lock = asyncio.Lock()

# Env overrides for a deployment that manages its own keypair (a fleet behind
# one push identity, or keys held in a secret manager). Both must be set, or
# neither: half a keypair cannot sign.
PRIVATE_KEY_ENV = "VAPID_PRIVATE_KEY"
PUBLIC_KEY_ENV = "VAPID_PUBLIC_KEY"
# RFC 8292 wants a contact for the push service to reach if this install
# misbehaves. It must be a ``mailto:`` or an ``https:`` URL or py_vapid refuses
# to sign, so there is a default rather than an optional field to forget.
SUBJECT_ENV = "VAPID_SUBJECT"
_DEFAULT_SUBJECT = "mailto:condor@localhost"


@dataclass(frozen=True)
class Subscription:
    """One browser on one device, and how to encrypt a notification to it."""

    endpoint: str  # the push service URL; also the identity of the row
    p256dh: str  # the browser's public key (PushSubscription.getKey)
    auth: str  # the browser's auth secret
    label: str  # "Chrome on macOS" -- so a user can tell their devices apart
    created: float

    def to_wire(self) -> dict[str, Any]:
        """What Settings needs to list and revoke a device.

        The two key fields are deliberately absent. Settings shows devices and
        removes them; handing the browser's own key material back out of the
        store would be a second copy of it for no reader.
        """
        return {
            "endpoint": self.endpoint,
            "label": self.label,
            "created": self.created,
        }

    def to_subscription_info(self) -> dict[str, Any]:
        """The shape ``pywebpush`` takes, straight from ``PushSubscription``."""
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }


# ── The subscription store ──


def _read_all() -> dict[str, list[dict]]:
    path = paths.push_subscriptions_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, list)}
    except Exception:  # noqa: BLE001 - a junk file is an empty store, not an outage
        log.warning("Failed to read %s; treating as empty", path)
        return {}


def _write_all(data: dict[str, list[dict]]) -> None:
    atomic_write_json(paths.push_subscriptions_path(), data, indent=2)


def _hydrate(raw: dict) -> Subscription | None:
    """One stored dict back into a :class:`Subscription`, or None if it is junk.

    Tolerant for the same reason ``notifications._hydrate`` is: a row written by
    an older build must cost that one device, not the whole store.
    """
    try:
        endpoint = str(raw["endpoint"])
        p256dh = str(raw["p256dh"])
        auth = str(raw["auth"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (endpoint and p256dh and auth):
        return None
    return Subscription(
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        label=str(raw.get("label") or ""),
        created=float(raw.get("created") or 0.0),
    )


def list_for(user_id: int) -> list[Subscription]:
    """This user's subscribed devices. Never anyone else's."""
    if not user_id:
        return []
    raw = _read_all().get(str(int(user_id))) or []
    return [s for s in (_hydrate(r) for r in raw if isinstance(r, dict)) if s]


async def save(user_id: int, subscription: Subscription) -> None:
    """Upsert ``subscription`` under ``user_id``, keyed on its endpoint.

    Upsert and not append: a browser that re-subscribes after a permission
    reset, an app reinstall or a key rotation hands back the *same* endpoint,
    and appending would mean one device buzzing twice for one notification and
    the list in Settings filling with rows the user cannot tell apart.
    """
    if not user_id:
        return
    key = str(int(user_id))
    async with _write_lock:
        data = _read_all()
        rows = [
            r
            for r in (data.get(key) or [])
            if isinstance(r, dict) and r.get("endpoint") != subscription.endpoint
        ]
        rows.insert(
            0,
            {
                "endpoint": subscription.endpoint,
                "p256dh": subscription.p256dh,
                "auth": subscription.auth,
                "label": subscription.label,
                "created": subscription.created,
            },
        )
        data[key] = rows[:_MAX_PER_USER]
        _write_all(data)


async def remove(user_id: int, endpoint: str) -> bool:
    """Drop one of ``user_id``'s own devices. Returns whether it was there.

    Scoped to this user's bucket, so an endpoint belonging to someone else is
    simply not found -- there is no way to reach across users from here.
    """
    if not user_id or not endpoint:
        return False
    key = str(int(user_id))
    async with _write_lock:
        data = _read_all()
        rows = data.get(key) or []
        kept = [
            r
            for r in rows
            if not (isinstance(r, dict) and r.get("endpoint") == endpoint)
        ]
        if len(kept) == len(rows):
            return False
        if kept:
            data[key] = kept
        else:
            data.pop(key, None)
        _write_all(data)
        return True


async def prune(endpoint: str) -> int:
    """Forget ``endpoint`` wherever it is stored. Returns how many rows went.

    The only place a subscription dies without the user asking, and it has to
    exist: a push service answering 404/410 has told us the browser threw the
    registration away (site data cleared, app uninstalled), and a dead endpoint
    that is never pruned is retried for every notification, forever.

    Endpoints are unique per browser registration, so "wherever it is" is one
    row in practice; the store is walked across users rather than taking a user
    id because the answer must not depend on the sink having guessed right.
    """
    if not endpoint:
        return 0
    async with _write_lock:
        data = _read_all()
        removed = 0
        for key in list(data.keys()):
            rows = data.get(key) or []
            kept = [
                r
                for r in rows
                if not (isinstance(r, dict) and r.get("endpoint") == endpoint)
            ]
            removed += len(rows) - len(kept)
            if kept:
                data[key] = kept
            else:
                data.pop(key, None)
        if removed:
            _write_all(data)
        return removed


# ── The VAPID keypair ──


def _b64url(raw: bytes) -> str:
    """Unpadded base64url -- what both the browser and py_vapid speak."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _generate() -> dict[str, str]:
    """A fresh P-256 keypair, in the two encodings this feature needs.

    ``private`` is the raw 32-octet scalar, which is what
    :meth:`py_vapid.Vapid01.from_string` reads back. ``public`` is the
    uncompressed point, which is what the browser wants verbatim as
    ``applicationServerKey``.
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    key = ec.generate_private_key(ec.SECP256R1())
    private_raw = key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    return {"private_key": _b64url(private_raw), "public_key": _b64url(public_raw)}


def _write_keys(keys: dict[str, str]) -> None:
    """Persist the keypair, once.

    The directory carries the protection (``0700``) and the file does not
    (``0644``). A ``0600`` file is the tempting shape and it is the one that
    breaks: bind-mounted into a container it reads fine on macOS and fails with
    ``Permission denied`` on every Linux deploy, because the uid inside is not
    the uid that wrote it. A private directory keeps the same readers out
    without the file's own mode being load-bearing.
    """
    directory = paths.vapid_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:  # pragma: no cover - a filesystem that has no modes
        log.debug("Could not set mode on %s", directory)

    path = paths.vapid_key_path()
    atomic_write_json(path, keys, indent=2)
    try:
        os.chmod(path, 0o644)
    except OSError:  # pragma: no cover
        pass


def _read_keys() -> dict[str, str] | None:
    path: Path = paths.vapid_key_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        # Deliberately *not* regenerated here. "Just make a new one" is the
        # obvious wrong fix: a new keypair invalidates every subscription
        # silently, and every device would have to be re-added by hand.
        log.error("VAPID key file at %s is unreadable; push is off", path)
        return None
    if not isinstance(data, dict):
        return None
    private = str(data.get("private_key") or "")
    public = str(data.get("public_key") or "")
    return (
        {"private_key": private, "public_key": public} if private and public else None
    )


def vapid_keys() -> dict[str, str]:
    """This install's keypair, generating and persisting it on first use.

    The environment wins if it carries both halves, so a deployment can hold
    the key in a secret manager and never let it reach the disk. Otherwise the
    file is read, and only if there is nothing there at all is a keypair made.
    """
    env_private = os.environ.get(PRIVATE_KEY_ENV, "").strip()
    env_public = os.environ.get(PUBLIC_KEY_ENV, "").strip()
    if env_private and env_public:
        return {"private_key": env_private, "public_key": env_public}

    existing = _read_keys()
    if existing:
        return existing

    keys = _generate()
    _write_keys(keys)
    # The public half only. The private one is never logged, here or anywhere.
    log.info("Generated a VAPID keypair for Web Push at %s", paths.vapid_key_path())
    return keys


def configured_private_key() -> str:
    """The signing half *if this install already has one*, and never a new one.

    :func:`vapid_keys` generates on first use, which is right for a push and
    wrong for anyone merely asking whether a key exists -- the sharing
    scrubber's known-value table, for one. An install that has never turned
    notifications on must not mint a keypair because somebody shared a
    conversation.
    """
    env_private = os.environ.get(PRIVATE_KEY_ENV, "").strip()
    if env_private and os.environ.get(PUBLIC_KEY_ENV, "").strip():
        return env_private
    existing = _read_keys()
    return existing["private_key"] if existing else ""


def public_key() -> str:
    """The ``applicationServerKey`` a browser needs in order to subscribe."""
    return vapid_keys()["public_key"]


def private_key() -> str:
    """The signing half. Callers hand it to ``pywebpush`` and nothing else."""
    return vapid_keys()["private_key"]


def vapid_claims() -> dict[str, str]:
    """The JWT claims for a push. ``aud`` is filled in per endpoint by pywebpush."""
    return {"sub": os.environ.get(SUBJECT_ENV, "").strip() or _DEFAULT_SUBJECT}


def new_subscription(
    *, endpoint: str, p256dh: str, auth: str, label: str = ""
) -> Subscription:
    """A :class:`Subscription` stamped with now. One constructor, one clock."""
    return Subscription(
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        label=label,
        created=time.time(),
    )

"""Web Push: the bell rings with the window closed (FEAT-083).

``chat_ws._push_notification`` is the sibling of the sink below, and it is the
one that cannot serve the case this module exists for. It writes to *attached
sockets*, so "a user with no tab open simply has no entry here" -- its own
words. A routine that finishes at 3am waits, unread, until somebody looks.

This registers a second sink on the same bus (``notifications.register_push_sink``)
that reaches the operating system instead, through the browser vendor's push
service, so the notice arrives with no Condor window open and no Telegram.

Three properties are load-bearing:

* **The payload never leaves the box in the clear.** RFC 8291 encrypts it to
  the subscribing browser's own key, which only that browser holds, so the relay
  carries ciphertext. That is the same posture ``app.py`` records for SEC-112 --
  notification text is PnL, agent output and position detail, and it is not a
  third party's to read.
* **The subscription is addressed by the token, never by the body.** Every route
  here keys on ``user.id`` from the JWT; an endpoint in a request body is data
  to store, not an authorization to act on someone else's row. This is the rule
  ``routes/notifications.py`` states in its module docstring.
* **A sink failure is never the producer's problem.** The bus guarantees it
  (``notifications._push``), and this sink keeps its side of the bargain: a
  refusing push service, a hung request, a missing key -- all logged and
  swallowed. The bell entry is already stored either way.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from pywebpush import WebPushException, webpush_async

from condor import push
from condor.notifications import Notification, register_push_sink
from condor.web.auth import get_current_user
from condor.web.models import WebUser

log = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])

# A push payload is capped by the protocol at ~4KB *after* encryption, and the
# service worker renders what it is given rather than fetching more. Notification
# text is occasionally a whole report, so it is cut here: the bell holds the
# full text, and the notification's job is to get the user to it.
_MAX_BODY = 400

# Seconds. pywebpush's own default is ``10000`` -- passed straight to aiohttp as
# seconds, so a push service that accepts the connection and then says nothing
# would hold the task for nearly three hours.
_PUSH_TIMEOUT = 10.0

# How long the push service holds an undelivered notice for a machine that is
# asleep. A day: the point of the feature is the notification raised while
# nobody was there, and the default TTL of 0 would drop exactly those.
_TTL = 86400

# What a push service says when the browser has thrown the registration away:
# uninstalled, site data cleared, permission revoked at the OS.
_GONE = {404, 410}


# ── Routes ──


class SubscribeRequest(BaseModel):
    """A ``PushSubscription``, flattened, as the browser reports it."""

    endpoint: str
    p256dh: str
    auth: str
    # Best-effort, from the user agent: its only job is letting someone
    # recognise which row is the laptop they no longer have.
    label: str = Field(default="", max_length=120)


class UnsubscribeRequest(BaseModel):
    endpoint: str


@router.get("/vapid")
async def get_vapid_key(user: WebUser = Depends(get_current_user)):
    """The ``applicationServerKey`` the browser needs in order to subscribe.

    The public half only, and generated on first request. Behind auth like
    everything else here -- it is not a secret, but there is no reason for an
    unauthenticated caller to make this install mint a keypair.
    """
    return {"public_key": push.public_key()}


@router.get("/subscriptions")
async def list_subscriptions(user: WebUser = Depends(get_current_user)):
    """This user's subscribed devices, for the list in Settings."""
    return {"items": [s.to_wire() for s in push.list_for(user.id)]}


@router.post("/subscribe")
async def subscribe(body: SubscribeRequest, user: WebUser = Depends(get_current_user)):
    """Register (or re-register) this browser under the caller's own id."""
    subscription = push.new_subscription(
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        label=body.label,
    )
    await push.save(user.id, subscription)
    return {"subscribed": True, "items": [s.to_wire() for s in push.list_for(user.id)]}


@router.post("/unsubscribe")
async def unsubscribe(
    body: UnsubscribeRequest, user: WebUser = Depends(get_current_user)
):
    """Forget one of the caller's own devices.

    Scoped to ``user.id``: an endpoint that belongs to somebody else is not
    found, and nothing happens. ``removed`` is false in that case rather than a
    404, because "you have no such device" is the honest answer and a 404 would
    tell a caller whether the row exists for someone.
    """
    removed = await push.remove(user.id, body.endpoint)
    return {"removed": removed, "items": [s.to_wire() for s in push.list_for(user.id)]}


# ── The sink ──


def _payload(notification: Notification) -> str:
    """The bytes the service worker renders. Small on purpose."""
    text = notification.text or ""
    if len(text) > _MAX_BODY:
        text = text[: _MAX_BODY - 1].rstrip() + "…"
    return json.dumps(
        {
            "id": notification.id,
            "title": notification.title or "Condor",
            "body": text,
            "link": notification.link or "/",
            "kind": notification.kind,
        }
    )


async def _deliver(
    subscription: push.Subscription, payload: str, private_key: str
) -> None:
    """One device. Never raises: the caller is a notification, not a request."""
    try:
        await webpush_async(
            subscription_info=subscription.to_subscription_info(),
            data=payload,
            vapid_private_key=private_key,
            vapid_claims=push.vapid_claims(),
            timeout=_PUSH_TIMEOUT,
            ttl=_TTL,
        )
    except WebPushException as exc:
        if exc.status_code in _GONE:
            # The browser is gone, not the push. Drop the row here or this
            # endpoint is retried for every notification, forever.
            await push.prune(subscription.endpoint)
            log.info("Pruned a Web Push subscription the browser had discarded")
            return
        # The endpoint is a capability URL: it is the address *and* the
        # authorization, so it is deliberately kept out of the log line.
        log.warning("Web Push failed (%s)", exc.status_code)
    except Exception:  # noqa: BLE001 - a dead device is not a dead notification
        log.warning("Web Push raised", exc_info=True)


async def _web_push(notification: Notification) -> None:
    """Deliver one bell entry to this user's subscribed devices (FEAT-083).

    The sibling of ``chat_ws._push_notification``, for the case that one cannot
    serve: nobody is watching. That sink writes to attached sockets and a user
    with no tab open has no entry there; this one reaches the OS instead, so a
    routine that finished at 3am is a notification and not a surprise in the
    morning.

    Fires on *every* bell entry, including ones Telegram also delivered. A user
    with both gets two buzzes for one finished routine, and that is deliberate:
    the alternative is a durable "was this already sent" concept that every
    producer would have to set correctly, bought for one buzz. The escape hatch
    is exact and costs no code -- unsubscribe that device.
    """
    subscriptions = push.list_for(notification.user_id)
    if not subscriptions:
        return
    try:
        private_key = push.private_key()
    except Exception:  # noqa: BLE001 - no key means no push, not a failed task
        log.warning("Web Push has no VAPID key; skipping", exc_info=True)
        return

    payload = _payload(notification)
    await asyncio.gather(
        *(_deliver(s, payload, private_key) for s in subscriptions),
        return_exceptions=True,
    )


# Registered here, at import, rather than by the runtime: ``condor.runtime`` and
# ``condor.notifications`` must not import web code, so the surface registers
# itself. ``chat_ws.py`` is the worked example and says the same thing.
register_push_sink(_web_push)

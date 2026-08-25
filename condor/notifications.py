"""Per-user task notifications, and the surfaces they reach (FEAT-048).

Everything Condor tells a user when a background task finishes — a delegation
completing, a routine run ending, an agent's ``send_notification``, the boot
notice — used to be pushed to Telegram and nowhere else, so a user who only had
the dashboard open learned nothing. This module is the other half: a small
per-user store plus a push bus, so a finished task lights the dashboard's bell
live *and* is still there, unread, an hour later.

Two rules shape it:

* **The notice must survive nobody watching.** So a store, not just a push:
  :func:`condor.paths.notifications_path`, newest first, capped per user,
  written with :func:`condor.fsutil.atomic_write_json` exactly like
  ``routine_hooks.json``.
* **Core must not import the web layer.** The surface registers itself at
  import time, the same shape ``condor/runtime/wake.py`` uses for wake and note
  sinks — see ``condor/web/routes/chat_ws.py``.

:class:`NotifyBot` is the adapter that gets this to every *existing* producer
without touching their call sites: it implements the four methods the codebase's
bot surface actually uses and records instead of sending, so it can sit at the
bottom of ``resolve_bot()``'s ladder and catch anything that has no Telegram to
go to.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, NamedTuple

from condor import paths
from condor.fsutil import atomic_write_json

log = logging.getLogger(__name__)

# Per user. A bell shows the last few dozen; anything older is history nobody
# scrolls to, and an unbounded file would be read and rewritten on every record.
_MAX_PER_USER = 100

# Serialises the read-modify-write in :func:`record`. Two producers finishing at
# once would otherwise each read the same file and the second write would drop
# the first's notification. The write rate is a handful per hour, so the lock
# costs nothing and removes the whole class of lost updates.
_write_lock = asyncio.Lock()


@dataclass(frozen=True)
class Notification:
    """One thing worth telling a user about, addressed to the user."""

    id: str
    user_id: int
    ts: float
    kind: str  # "delegation" | "routine" | "agent" | "system"
    text: str
    title: str | None = None
    # A dashboard route, e.g. "/routines?tab=reports". Relative on purpose: the
    # bell navigates within the SPA, it does not open the world.
    link: str | None = None
    read: bool = False

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


# ── The push bus ──

PushSink = Callable[[Notification], Awaitable[None]]

_push_sinks: list[PushSink] = []


def register_push_sink(sink: PushSink) -> None:
    """Bind a live surface's renderer. Called by the surface, at import time."""
    if sink not in _push_sinks:
        _push_sinks.append(sink)


async def _push(notification: Notification) -> None:
    """Fan out to whoever is watching. A dead sink is not a dead notification.

    Mirrors ``wake._guard``: the store is the record, so a socket that blew up
    must never propagate into the producer that was only announcing something.
    """
    for sink in list(_push_sinks):
        try:
            await sink(notification)
        except Exception:  # noqa: BLE001 - a dead sink is not a dead notification
            log.warning("Notification push sink failed", exc_info=True)


# ── The store ──


def _read_all() -> dict[str, list[dict]]:
    path = paths.notifications_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, list)}
    except Exception:  # noqa: BLE001
        log.warning("Failed to read %s; treating as empty", path)
        return {}


def _write_all(data: dict[str, list[dict]]) -> None:
    atomic_write_json(paths.notifications_path(), data, indent=2)


def _hydrate(raw: dict) -> Notification | None:
    """One stored dict back into a :class:`Notification`, or None if it is junk.

    Tolerant on purpose: a file written by an older build (or by hand) must not
    take the whole bell down with it.
    """
    try:
        return Notification(
            id=str(raw["id"]),
            user_id=int(raw["user_id"]),
            ts=float(raw.get("ts") or 0.0),
            kind=str(raw.get("kind") or "system"),
            text=str(raw.get("text") or ""),
            title=raw.get("title") or None,
            link=raw.get("link") or None,
            read=bool(raw.get("read")),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def record(
    user_id: int | None,
    text: str,
    *,
    kind: str = "system",
    title: str | None = None,
    link: str | None = None,
) -> Notification | None:
    """Store a notification for ``user_id`` and push it to live surfaces.

    Returns the stored notification, or ``None`` when there is nobody to
    address it to (a falsy ``user_id``) or nothing to say — a producer with no
    user behind it must not be an error, it simply has no bell to ring.
    """
    if not user_id or not text:
        return None

    notification = Notification(
        id=uuid.uuid4().hex,
        user_id=int(user_id),
        ts=time.time(),
        kind=kind,
        text=text,
        title=title,
        link=link,
    )

    async with _write_lock:
        data = _read_all()
        key = str(notification.user_id)
        items = [notification.to_wire()] + (data.get(key) or [])
        data[key] = items[:_MAX_PER_USER]
        try:
            _write_all(data)
        except Exception:  # noqa: BLE001 - an unwritable store still gets a push
            log.warning("Could not persist notification for %s", user_id, exc_info=True)

    await _push(notification)
    return notification


def list_for(user_id: int, limit: int = 50) -> list[Notification]:
    """This user's notifications, newest first. Never anyone else's."""
    if not user_id:
        return []
    raw = _read_all().get(str(int(user_id))) or []
    items = [n for n in (_hydrate(r) for r in raw if isinstance(r, dict)) if n]
    return items[: max(0, limit)]


def unread_count(user_id: int) -> int:
    """How many of this user's notifications are still unread."""
    return sum(1 for n in list_for(user_id, limit=_MAX_PER_USER) if not n.read)


def mark_read(user_id: int, ids: list[str] | None = None) -> int:
    """Mark ``ids`` read (``None`` = all of them). Returns the unread count left.

    Scoped to ``user_id``'s own bucket, so an id belonging to someone else is
    simply not found — there is no way to reach across users from here.
    """
    if not user_id:
        return 0
    key = str(int(user_id))
    data = _read_all()
    items = data.get(key) or []
    wanted = set(ids) if ids is not None else None
    changed = False
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if wanted is not None and str(raw.get("id")) not in wanted:
            continue
        if not raw.get("read"):
            raw["read"] = True
            changed = True
    if changed:
        data[key] = items
        try:
            _write_all(data)
        except Exception:  # noqa: BLE001
            log.warning("Could not persist read state for %s", user_id, exc_info=True)
    return sum(1 for raw in items if isinstance(raw, dict) and not raw.get("read"))


def user_for_chat(chat_id: Any) -> int | None:
    """The dashboard user behind a Telegram chat id, or None if there is none.

    A private chat's id *is* the user id — the rule ``_check_chat_access``
    already relies on. A group chat id (negative) has no dashboard owner, and
    neither does a private chat with someone this install has never seen, so
    both return None: guessing would file one person's message in another
    person's bell.
    """
    try:
        user_id = int(chat_id)
    except (TypeError, ValueError):
        return None
    if user_id <= 0:  # Telegram group/channel ids are negative.
        return None
    try:
        from config_manager import get_config_manager

        if get_config_manager().get_user(user_id) is None:
            log.debug("No dashboard user for chat %s; not recording", chat_id)
            return None
    except Exception:  # noqa: BLE001 - config unavailable: do not guess
        log.debug("Could not resolve chat %s to a user", chat_id, exc_info=True)
        return None
    return user_id


# ── The adapter every existing producer already talks to ──


class NotifyBot:
    """A bot-shaped object that records notifications instead of sending them.

    The four methods are the ones the codebase's bot surface actually uses —
    the same set ``_HttpBot`` implements (``condor/routine_store.py``). Sitting
    at the bottom of ``resolve_bot()``'s ladder, this is what makes a routine's
    ``context.bot.send_message(...)`` land on the dashboard in an install with
    no Telegram token, with no routine and no call-site changes.

    Addressing goes through :func:`user_for_chat`, so nothing is recorded for a
    chat id that is not a known user.

    Binary payloads are **not** re-hosted. ``send_photo``/``send_document``
    record the caption (or the filename) and link to the reports browser, which
    already serves routine reports through ``GET /reports/{id}/html``.
    """

    def __init__(self, kind: str = "system"):
        self._kind = kind

    # -- helpers --

    @staticmethod
    def _arg(a: tuple, kw: dict, name: str, index: int) -> Any:
        return kw.get(name) if name in kw else (a[index] if len(a) > index else None)

    async def _record(self, chat_id: Any, text: str, link: str | None = None):
        user_id = user_for_chat(chat_id)
        if user_id is None:
            return None
        return await record(user_id, text, kind=self._kind, link=link)

    # -- the bot surface --

    async def send_message(self, *a, **kw):
        chat_id = self._arg(a, kw, "chat_id", 0)
        text = self._arg(a, kw, "text", 1) or ""
        return await self._record(chat_id, text)

    async def send_photo(self, *a, **kw):
        chat_id = self._arg(a, kw, "chat_id", 0)
        text = kw.get("caption") or "Chart"
        return await self._record(chat_id, text, link="/routines?tab=reports")

    async def send_document(self, *a, **kw):
        chat_id = self._arg(a, kw, "chat_id", 0)
        document = self._arg(a, kw, "document", 1)
        text = (
            kw.get("caption")
            or kw.get("filename")
            or getattr(document, "name", None)
            or "Report"
        )
        return await self._record(chat_id, text, link="/routines?tab=reports")

    async def edit_message_text(self, *a, **kw):
        """A live-updating message has no bell equivalent: nothing to record.

        The bell is a log of things that happened, not a surface that redraws —
        recording every tick of a ``LiveReport`` would bury the notices that
        matter under a routine's own progress updates.
        """
        return None


# ── The one dual-delivery policy ──


class Delivery(NamedTuple):
    """What actually became of one announcement.

    ``sent`` is a real Telegram delivery and nothing else, so a caller can tell
    the user the truth on an install with no Telegram; ``recorded`` means the
    bell has it, by whichever of the two paths below filed it.
    """

    sent: bool
    recorded: bool


def _reached_telegram(result: Any) -> bool:
    """Did a ``send_message`` on the resolved bot actually deliver?

    The ladder returns three different things: a live python-telegram-bot
    raises on failure and returns a ``Message``, ``_HttpBot`` never raises and
    hands back Telegram's raw envelope (or ``None`` when it has no token), and
    :class:`NotifyBot` hands back the :class:`Notification` it filed — which is
    a delivery to the bell, never to Telegram.
    """
    if result is None or isinstance(result, Notification):
        return False
    if isinstance(result, dict):
        return bool(result.get("ok"))
    return True


async def _send(target: Any, chat_id: Any, text: str, parse_mode: str) -> Any:
    """Push ``text`` at ``chat_id``, retrying once without ``parse_mode``.

    The overwhelmingly common failure is an unescaped ``_`` or ``*`` in text a
    model wrote: the user must get the message, ugly, rather than not get it at
    all. The retry is skipped when the first attempt reached the bell — that
    rung cannot fail on markup, and calling it twice would file it twice.
    """
    if parse_mode:
        try:
            result = await target.send_message(
                chat_id=chat_id, text=text, parse_mode=parse_mode
            )
            if isinstance(result, Notification) or _reached_telegram(result):
                return result
        except Exception:  # noqa: BLE001 - bad markup is retried as plain text
            log.debug("Notification rejected with parse_mode=%s", parse_mode)
    try:
        return await target.send_message(chat_id=chat_id, text=text)
    except Exception:  # noqa: BLE001 - an undeliverable push still rings the bell
        log.warning("Could not deliver notification to chat %s", chat_id)
        return None


async def announce(
    user_id: int | None,
    chat_id: Any,
    text: str,
    *,
    kind: str = "system",
    bot: Any = None,
    parse_mode: str = "",
    title: str | None = None,
    link: str | None = None,
) -> Delivery:
    """Tell ``user_id`` something, on Telegram *and* on the bell, once each.

    Every producer that announces a finished task wants the same two things and
    the same single subtlety, so it lives here instead of being hand-copied at
    each call site (ARCH-212): the bottom rung of ``resolve_bot()``'s ladder is
    :class:`NotifyBot`, which files the message on the bell itself, so a
    producer that then also calls :func:`record` files it twice on every
    install with no Telegram.

    The push is what decides: it hands back the :class:`Notification` it filed,
    and only when that is addressed to this same ``user_id`` is the explicit
    record skipped. A group ``chat_id`` has no dashboard owner, so the bell
    push files nothing and the caller's own entry is still written — the two
    addresses are genuinely different questions.
    """
    if not text:
        return Delivery(False, False)

    filed: Notification | None = None
    sent = False
    if chat_id:
        from condor.agents.delegate import resolve_bot

        target = resolve_bot(bot)
        if isinstance(target, NotifyBot):
            # That rung files the message itself, so hand it this producer's
            # own kind: the single entry is then the one :func:`record` below
            # would have written, not a generic ``system`` one.
            target = NotifyBot(kind)
        result = await _send(target, chat_id, text, parse_mode)
        if isinstance(result, Notification):
            filed = result
        else:
            sent = _reached_telegram(result)

    if filed is not None and (not user_id or filed.user_id == int(user_id)):
        return Delivery(sent, True)

    entry = await record(user_id, text, kind=kind, title=title, link=link)
    return Delivery(sent, entry is not None)

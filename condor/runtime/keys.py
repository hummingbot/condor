"""Canonical session keys. One keyspace for every frontend.

Before this module every frontend invented its own key: Telegram used the raw
integer ``chat_id`` and the web dashboard used ``f"web_{user_id}_{slot_id}"``.
The registry then had to type-sniff (``isinstance(chat_id, int)``) to tell them
apart. ``SessionKey`` makes the surface explicit and gives every key a single
stable string form, which is also what the HTTP transport will put on the wire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Known surfaces. A surface identifies which frontend owns the session.
TELEGRAM = "tg"
WEB = "web"
MCP = "mcp"

SEPARATOR = ":"

# Legacy web key produced by condor/web/routes/chat_ws.py before this feature.
_LEGACY_WEB_RE = re.compile(r"^web_(?P<owner>[^_]+)_(?P<slot>.+)$")


@dataclass(frozen=True)
class SessionKey:
    """Immutable, hashable identifier for one chat session.

    ``owner`` is the Telegram ``chat_id`` for the ``tg`` surface and the Condor
    ``user_id`` for ``web``/``mcp``. It is always stored as a string so the key
    has one canonical text form.
    """

    surface: str
    owner: str
    slot: str = ""

    def __str__(self) -> str:
        if self.slot:
            return f"{self.surface}{SEPARATOR}{self.owner}{SEPARATOR}{self.slot}"
        return f"{self.surface}{SEPARATOR}{self.owner}"

    @classmethod
    def parse(cls, raw: str) -> "SessionKey":
        """Parse the canonical ``surface:owner[:slot]`` form."""
        parts = str(raw).split(SEPARATOR, 2)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Not a canonical session key: {raw!r}")
        surface, owner = parts[0], parts[1]
        slot = parts[2] if len(parts) == 3 else ""
        return cls(surface=surface, owner=owner, slot=slot)

    @classmethod
    def telegram(cls, chat_id: int | str) -> "SessionKey":
        return cls(surface=TELEGRAM, owner=str(chat_id))

    @classmethod
    def web(cls, user_id: int | str, slot_id: str) -> "SessionKey":
        return cls(surface=WEB, owner=str(user_id), slot=str(slot_id))

    @classmethod
    def mcp(cls, user_id: int | str) -> "SessionKey":
        return cls(surface=MCP, owner=str(user_id))

    @classmethod
    def legacy_from(cls, raw: int | str) -> "SessionKey":
        """Build a key from a pre-facade identifier.

        Accepts the two shapes that existed before this feature (an ``int``
        Telegram chat id and ``web_{user_id}_{slot_id}``) as well as the
        canonical form, so persisted blobs and half-migrated call sites keep
        resolving to the same session.
        """
        if isinstance(raw, int):
            return cls.telegram(raw)

        text = str(raw)
        legacy_web = _LEGACY_WEB_RE.match(text)
        if legacy_web:
            return cls.web(legacy_web.group("owner"), legacy_web.group("slot"))
        if SEPARATOR in text:
            return cls.parse(text)
        if text.lstrip("-").isdigit():
            return cls.telegram(text)
        raise ValueError(f"Cannot derive a session key from {raw!r}")

    @property
    def is_telegram(self) -> bool:
        return self.surface == TELEGRAM

    @property
    def telegram_chat_id(self) -> int | None:
        """The Telegram chat id, or None when this is not a Telegram session."""
        if not self.is_telegram:
            return None
        try:
            return int(self.owner)
        except ValueError:
            return None

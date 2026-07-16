"""Bare WebSocket connection manager for the dashboard ``/ws`` route.

The Hummingbot-era stream registry (portfolio/bots/prices/candles/trades/
orderbook/executors/positions/performance/controller_perf, all fed by the
deleted server-data polling service) is gone (simplification plan §9.2).
What remains
is connection lifecycle + channel bookkeeping so clients can subscribe/
unsubscribe, and a generic ``broadcast`` for whatever topics come later
(e.g. notifications — deliberately not built yet).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from fastapi import WebSocket

from condor.web.auth import decode_jwt

logger = logging.getLogger(__name__)


class _Connection:
    __slots__ = ("ws", "user_id", "channels")

    def __init__(self, ws: WebSocket, user_id: int):
        self.ws = ws
        self.user_id = user_id
        self.channels: set[str] = set()


class WebSocketManager:
    """Manages WebSocket connections and channel subscriptions."""

    def __init__(self):
        self._connections: list[_Connection] = []

    # -- Connection handling --

    async def connect(
        self, ws: WebSocket, token: Optional[str], subprotocol: Optional[str] = None
    ) -> Optional[_Connection]:
        payload = decode_jwt(token) if token else None
        if payload is None:
            await ws.close(code=4001, reason="Invalid token")
            return None

        from config_manager import UserRole, get_config_manager

        user_id = int(payload["sub"])
        cm = get_config_manager()
        role = cm.get_user_role(user_id)
        if role not in (UserRole.USER, UserRole.ADMIN):
            await ws.close(code=4003, reason="Forbidden")
            return None

        await ws.accept(subprotocol=subprotocol)
        conn = _Connection(ws, user_id)
        self._connections.append(conn)
        logger.info("WS connected: user %s", user_id)
        return conn

    def disconnect(self, conn: _Connection) -> None:
        if conn in self._connections:
            self._connections.remove(conn)
            logger.info("WS disconnected: user %s", conn.user_id)

    # -- Message handling --

    async def handle_message(self, conn: _Connection, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        action = msg.get("action")
        channel = msg.get("channel", "")

        if action == "subscribe" and channel:
            conn.channels.add(channel)
            logger.debug("WS subscribe: user=%s channel=%s", conn.user_id, channel)
        elif action == "unsubscribe" and channel:
            conn.channels.discard(channel)

    # -- Broadcasting --

    async def broadcast(self, channel: str, data: Any) -> None:
        subscribers = [
            conn for conn in list(self._connections) if channel in conn.channels
        ]
        for conn in subscribers:
            try:
                await self._send(conn, channel, data)
            except Exception as e:
                logger.warning(
                    "Broadcast send failed: channel=%s user=%s: %s",
                    channel,
                    conn.user_id,
                    e,
                )
                self.disconnect(conn)

    async def _send(self, conn: _Connection, channel: str, data: Any) -> None:
        await conn.ws.send_json({"channel": channel, "data": data, "ts": time.time()})


# -- Singleton --

_instance: Optional[WebSocketManager] = None


def get_ws_manager() -> WebSocketManager:
    global _instance
    if _instance is None:
        _instance = WebSocketManager()
    return _instance

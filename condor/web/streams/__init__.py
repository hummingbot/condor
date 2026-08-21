"""Upstream stream implementations behind the ``WebSocketManager``.

Each module in this package owns one stream family:

- ``candles``: the candle subsystem — per-channel buffer, backend WS stream,
  REST poll fallback, historical backfill and idle-buffer cleanup.
- ``hummingbot_ws``: the streams bridged from the Hummingbot backend WS
  endpoints (trades, order book, executors, bots, positions, performance)
  plus the controller-performance REST poll.

The stream code is packaged as mixins over a small host surface
(:class:`StreamHost`) that ``WebSocketManager`` implements: broadcasting,
subscriber checks, per-connection sends and the shared one-shot task tracker.
A unit test can drive a mixin with a stub host instead of the full manager,
and ``WebSocketManager`` itself keeps only connection bookkeeping, the SDS
bridge and the stream lifecycle registry.
"""

from __future__ import annotations

from typing import Any, Protocol

from condor.asyncutil import TaskSet


class StreamHost(Protocol):
    """What the stream mixins require of the class they are mixed into.

    ``WebSocketManager`` is the one production implementation; the protocol
    exists so each stream family can be unit-tested against a stub.
    """

    _last_data: dict[str, Any]
    _oneshot_tasks: TaskSet

    async def broadcast(self, channel: str, data: Any) -> None: ...

    async def _broadcast_update(self, channel: str, data: Any) -> None: ...

    async def _send(self, conn: Any, channel: str, data: Any) -> None: ...

    def _has_subscribers(self, channel: str) -> bool: ...

    def _ensure_stream(self, prefix: str, channel: str) -> None: ...


__all__ = ["StreamHost"]

"""Upstream stream implementations behind the ``WebSocketManager``.

Each module in this package owns one stream family:

- ``candles``: the candle subsystem — per-channel buffer, backend WS stream,
  REST poll fallback, historical backfill and idle-buffer cleanup.
- ``hummingbot_ws``: the streams bridged from the Hummingbot backend WS
  endpoints (trades, order book, executors, bots, positions, performance)
  plus the controller-performance REST poll.

The stream code is packaged as mixins over a small host surface that
``WebSocketManager`` implements: ``broadcast``, ``_broadcast_update``,
``_send``, ``_has_subscribers``, ``_ensure_stream``, ``_last_data`` and
``_oneshot_tasks``. ``WebSocketManager`` itself keeps only connection
bookkeeping, the SDS bridge and the stream lifecycle registry.
"""

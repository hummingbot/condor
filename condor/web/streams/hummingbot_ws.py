"""Streams bridged from the Hummingbot backend WS endpoints, plus the
controller-performance REST poll.

``HummingbotStreamsMixin`` is mixed into ``WebSocketManager``, which provides
the host surface. It owns the shared
connect/reconnect skeleton (``_run_ws_stream``) and every per-protocol
handler built on it: trades and order book (``/ws/market-data``), executors,
bots, positions and performance (``/ws/executors``), and the 30s
controller-performance poll. Channel names and message shapes are part of the
dashboard's wire contract — the frontend depends on them byte-for-byte.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class HummingbotStreamsMixin:
    """Upstream Hummingbot WS stream family and its shared reconnect skeleton."""

    if TYPE_CHECKING:
        # Stream task state, initialized by WebSocketManager.__init__.
        _controller_perf_tasks: dict[str, asyncio.Task]
        # Host surface, provided by WebSocketManager.
        _last_data: dict[str, Any]

        async def broadcast(self, channel: str, data: Any) -> None: ...

        async def _broadcast_update(self, channel: str, data: Any) -> None: ...

        def _has_subscribers(self, channel: str) -> bool: ...

    # -- Transforms (REST-shape parity for WS broadcasts) --

    @staticmethod
    def _transform_executors(raw_data: Any) -> list[dict]:
        """Transform raw executor data to ExecutorInfo-compatible dicts for WS broadcast."""
        from condor.fetchers.executors import extract_executors_list
        from condor.web.models import ExecutorInfo

        executors_list = extract_executors_list(raw_data)
        result = []
        for ex in executors_list:
            info = ExecutorInfo.from_raw(ex)
            if info:
                result.append(info.model_dump())
        return result

    @staticmethod
    def _transform_bots(raw_data: Any) -> dict:
        """Transform raw BOTS_STATUS data to BotsPageResponse-compatible dict for WS broadcast."""
        from condor.fetchers.bots import build_bots_page

        return build_bots_page(raw_data)

    @staticmethod
    def _overlay_stopping_state(server_name: str, data: dict) -> None:
        """Apply transitional 'stopping' state to WS broadcast data."""
        from condor.web.routes.bots import overlay_stopping_state

        overlay_stopping_state(
            server_name, data.get("controllers", []), data.get("bots", [])
        )

    # -- Shared skeleton --

    @staticmethod
    def _is_permanent_ws_error(error_str: str) -> bool:
        """True if a stream error will never succeed on retry.

        Besides auth/not-found (401/403/404), an invalid trading pair (wrong
        symbol for this connector) will never become valid, so retrying
        forever just spams logs and hammers the exchange until Condor
        restarts (issue #134).
        """
        lowered = error_str.lower()
        return (
            any(code in error_str for code in ("401", "403", "404"))
            or "appears to be invalid" in lowered
            or "invalid symbol" in lowered
        )

    async def _run_ws_stream(
        self,
        channel: str,
        server_name: str,
        *,
        label: str,
        open_ws: Callable[[Any], Any],
        subscribe: Callable[[Any], Awaitable[None]],
        on_message: Callable[[dict], Awaitable[None]],
        backoff_on_empty_close: bool = False,
    ) -> None:
        """Shared connect/reconnect skeleton for all Hummingbot WS streams.

        Owns the while-True loop, subscriber check, heartbeat/error message
        handling, permanent-error detection (``_is_permanent_ws_error``) and
        exponential backoff capped at 60s. Each stream supplies only:

        - ``open_ws``: client -> the WS async context manager to enter
          (e.g. ``lambda c: c.ws.market_data()``).
        - ``subscribe``: performs the subscription on the open socket.
        - ``on_message``: handles data messages (heartbeat/error are
          handled here).

        ``backoff_on_empty_close`` reproduces the executor stream's
        behavior: don't reset backoff on subscribe; after a clean close,
        reset it only if at least one message arrived, otherwise back off
        (guards against a server that accepts then immediately drops).
        """
        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5
        lower_label = label[0].lower() + label[1:]
        subscribed_label = label if label.endswith("WS") else f"{label} WS"

        while True:
            try:
                client = await cm.get_client(server_name)
                async with open_ws(client) as ws:
                    await subscribe(ws)
                    logger.info("%s subscribed: %s", subscribed_label, channel)
                    if not backoff_on_empty_close:
                        backoff = 5
                    got_message = False
                    async for msg in ws:
                        if not self._has_subscribers(channel):
                            logger.info(
                                "No subscribers for %s, closing %s stream",
                                channel,
                                lower_label,
                            )
                            return

                        got_message = True
                        msg_type = msg.get("type")
                        if msg_type == "heartbeat":
                            continue
                        if msg_type == "error":
                            logger.warning(
                                "%s stream error for %s: %s",
                                label,
                                channel,
                                msg.get("message", "unknown error"),
                            )
                            break
                        await on_message(msg)

                    if backoff_on_empty_close:
                        # Connection closed cleanly — back off if short-lived
                        if got_message:
                            backoff = 5
                        else:
                            logger.warning(
                                "%s stream closed immediately for %s, "
                                "reconnecting in %ds...",
                                label,
                                channel,
                                backoff,
                            )
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2, 60)

            except asyncio.CancelledError:
                return
            except Exception as e:
                if self._is_permanent_ws_error(str(e)):
                    logger.warning(
                        "%s stream permanent error for %s: %s — giving up",
                        label,
                        channel,
                        e,
                    )
                    return

                logger.warning(
                    "%s stream error for %s: %s, reconnecting in %ds...",
                    label,
                    channel,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # -- Trade streaming --

    async def _trade_stream(self, channel: str) -> None:
        parts = channel.split(":")
        if len(parts) < 4:
            return
        _, server_name, connector, pair = parts[:4]

        async def subscribe(ws: Any) -> None:
            await ws.subscribe_trades(
                connector,
                pair,
                update_interval=1.0,
            )

        async def on_message(msg: dict) -> None:
            if msg.get("type") != "trades":
                return
            trade_data = msg.get("data", [])
            trades = []
            for t in trade_data:
                if isinstance(t, dict):
                    trades.append(
                        {
                            "price": float(t.get("price", 0)),
                            "amount": float(t.get("amount", t.get("quantity", 0))),
                            "side": t.get("side", t.get("trade_type", "buy")).lower(),
                            "timestamp": float(t.get("timestamp", 0)),
                        }
                    )
            if trades:
                await self.broadcast(channel, {"type": "trades", "data": trades})

        await self._run_ws_stream(
            channel,
            server_name,
            label="Trade",
            open_ws=lambda client: client.ws.market_data(),
            subscribe=subscribe,
            on_message=on_message,
        )

    # -- Order book streaming --

    async def _order_book_stream(self, channel: str) -> None:
        parts = channel.split(":")
        if len(parts) < 4:
            return
        _, server_name, connector, pair = parts[:4]

        async def subscribe(ws: Any) -> None:
            await ws.subscribe_order_book(
                connector,
                pair,
                depth=20,
                update_interval=1.0,
            )

        async def on_message(msg: dict) -> None:
            if msg.get("type") != "order_book":
                return
            raw_data = msg.get("data", {})
            bids = []
            asks = []
            for b in raw_data.get("bids") or []:
                if isinstance(b, dict):
                    bids.append(
                        {
                            "price": float(b.get("price", 0)),
                            "amount": float(b.get("amount", b.get("quantity", 0))),
                        }
                    )
                elif isinstance(b, (list, tuple)) and len(b) >= 2:
                    bids.append({"price": float(b[0]), "amount": float(b[1])})
            for a in raw_data.get("asks") or []:
                if isinstance(a, dict):
                    asks.append(
                        {
                            "price": float(a.get("price", 0)),
                            "amount": float(a.get("amount", a.get("quantity", 0))),
                        }
                    )
                elif isinstance(a, (list, tuple)) and len(a) >= 2:
                    asks.append({"price": float(a[0]), "amount": float(a[1])})
            await self.broadcast(channel, {"bids": bids, "asks": asks})

        await self._run_ws_stream(
            channel,
            server_name,
            label="Order book",
            open_ws=lambda client: client.ws.market_data(),
            subscribe=subscribe,
            on_message=on_message,
        )

    # -- Executor streaming (via Hummingbot WS) --

    async def _executor_stream(self, channel: str) -> None:
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        from config_manager import get_config_manager

        cm = get_config_manager()

        # Try SDS cache first (pre-warmed by auto_subscribe_servers or REST prefetch)
        from condor.server_data_service import ServerDataType, get_server_data_service

        if channel not in self._last_data:
            sds = get_server_data_service()
            cached = sds.get(server_name, ServerDataType.EXECUTORS)
            if cached is not None:
                executors = self._transform_executors(cached)
                if executors:
                    await self.broadcast(channel, executors)
                    logger.info(
                        "Executor SDS cache hit: %d executors for %s",
                        len(executors),
                        channel,
                    )

        # Wait briefly for SDS to be populated by a concurrent REST request
        # (usePrefetchData fires getExecutors which calls get_or_fetch on SDS)
        if channel not in self._last_data:
            sds = get_server_data_service()
            for _ in range(6):  # up to 3 seconds
                await asyncio.sleep(0.5)
                cached = sds.get(server_name, ServerDataType.EXECUTORS)
                if cached is not None:
                    executors = self._transform_executors(cached)
                    if executors:
                        await self.broadcast(channel, executors)
                        logger.info(
                            "Executor SDS cache populated during wait: %d executors for %s",
                            len(executors),
                            channel,
                        )
                    break

        # Progressive pre-fetch only if we still have no data
        if channel not in self._last_data:
            try:
                from condor.fetchers._pagination import walk_pages
                from condor.fetchers.executors import (
                    extract_executors_list as _extract_executors_list,
                )

                sds = get_server_data_service()
                client = await cm.get_client(server_name)
                all_raw: list[dict] = []
                # Rows already through ExecutorInfo, accumulated page by page.
                # Transforming the *whole* accumulated list on every page made
                # this quadratic: with FIRST_PAGE/NEXT_PAGE/MAX_PREFETCH below,
                # a 5k history cost ~27k pydantic validations instead of 5k, all
                # on the event loop the candle and bots broadcasts share.
                # ``_transform_executors`` is a pure per-item map over a list
                # (``extract_executors_list`` passes a list through untouched),
                # so transforming each page once yields the identical snapshot.
                transformed: list[dict] = []
                page_num = 0
                FIRST_PAGE = 50
                NEXT_PAGE = 500
                MAX_PREFETCH = 5000

                # A small first page so the tab paints before the long pages land.
                async for page in walk_pages(
                    client.executors.search_executors,
                    _extract_executors_list,
                    page_size=NEXT_PAGE,
                    first_page_size=FIRST_PAGE,
                    max_items=MAX_PREFETCH,
                ):
                    all_raw.extend(page)
                    transformed.extend(self._transform_executors(page))

                    # Broadcast the accumulated snapshot after each page. It is
                    # copied because ``broadcast`` retains what it is handed as
                    # the channel's last-known payload, which the next page
                    # would otherwise keep mutating underneath it.
                    if transformed:
                        await self.broadcast(channel, list(transformed))
                        logger.info(
                            "Executor pre-fetch page %d: %d executors (total %d) for %s",
                            page_num,
                            len(page),
                            len(transformed),
                            channel,
                        )
                    page_num += 1

                # Cache in SDS so other consumers benefit
                sds.put(server_name, ServerDataType.EXECUTORS, all_raw)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("Executor pre-fetch failed for %s: %s", channel, e)

        async def subscribe(ws: Any) -> None:
            await ws.subscribe_executors(update_interval=2.0)

        async def on_message(msg: dict) -> None:
            if msg.get("type") != "executors":
                return
            executors = self._transform_executors(msg.get("data", []))
            await self._broadcast_update(channel, executors)

        await self._run_ws_stream(
            channel,
            server_name,
            label="Executor",
            open_ws=lambda client: client.ws.executors(),
            subscribe=subscribe,
            on_message=on_message,
            backoff_on_empty_close=True,
        )

    # -- Bots WS streaming (via Hummingbot /ws/executors all_bots_status) --

    async def _bots_ws_stream(self, channel: str) -> None:
        """Stream all_bots_status from Hummingbot /ws/executors and update SDS cache."""
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        # Send SDS-cached bots data as initial snapshot
        if channel not in self._last_data:
            from condor.server_data_service import (
                ServerDataType,
                get_server_data_service,
            )

            sds = get_server_data_service()
            cached = sds.get(server_name, ServerDataType.BOTS_STATUS)
            if cached is not None:
                try:
                    data = self._transform_bots(cached)
                    await self.broadcast(channel, data)
                except Exception as e:
                    logger.debug("Failed to send initial bots snapshot: %s", e)

        async def subscribe(ws: Any) -> None:
            # all_bots_status is not in the client library, send raw
            await ws._send(
                {
                    "action": "subscribe",
                    "type": "all_bots_status",
                    "update_interval": 5.0,
                }
            )
            resp = await ws._receive()
            if resp.get("type") == "error":
                raise RuntimeError(f"Subscribe failed: {resp.get('message')}")

        async def on_message(msg: dict) -> None:
            if msg.get("type") != "all_bots_status":
                return
            raw_data = msg.get("data", {})
            # Update SDS cache so REST and Telegram benefit
            from condor.server_data_service import (
                ServerDataType,
                get_server_data_service,
            )

            get_server_data_service().put(
                server_name, ServerDataType.BOTS_STATUS, raw_data
            )
            try:
                data = self._transform_bots(raw_data)
                self._overlay_stopping_state(server_name, data)
                await self._broadcast_update(channel, data)
            except Exception as e:
                logger.debug("Failed to transform bots WS data: %s", e)

        await self._run_ws_stream(
            channel,
            server_name,
            label="Bots WS",
            open_ws=lambda client: client.ws.executors(),
            subscribe=subscribe,
            on_message=on_message,
        )

    # -- Positions WS streaming (via Hummingbot /ws/executors positions) --

    async def _positions_ws_stream(self, channel: str) -> None:
        """Stream positions from Hummingbot /ws/executors and update SDS cache."""
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        async def subscribe(ws: Any) -> None:
            await ws.subscribe_positions(update_interval=5.0)

        async def on_message(msg: dict) -> None:
            if msg.get("type") != "positions":
                return
            raw_data = msg.get("data", [])
            # Update SDS cache
            from condor.server_data_service import (
                ServerDataType,
                get_server_data_service,
            )

            get_server_data_service().put(
                server_name, ServerDataType.POSITIONS, raw_data
            )
            await self._broadcast_update(channel, raw_data)

        await self._run_ws_stream(
            channel,
            server_name,
            label="Positions WS",
            open_ws=lambda client: client.ws.executors(),
            subscribe=subscribe,
            on_message=on_message,
        )

    # -- Performance WS streaming (via Hummingbot /ws/executors performance) --

    async def _performance_ws_stream(self, channel: str) -> None:
        """Stream performance from Hummingbot /ws/executors and update SDS cache."""
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        async def subscribe(ws: Any) -> None:
            await ws.subscribe_performance(update_interval=5.0)

        async def on_message(msg: dict) -> None:
            if msg.get("type") != "performance":
                return
            await self._broadcast_update(channel, msg.get("data", {}))

        await self._run_ws_stream(
            channel,
            server_name,
            label="Performance WS",
            open_ws=lambda client: client.ws.executors(),
            subscribe=subscribe,
            on_message=on_message,
        )

    # -- Controller Performance polling stream --

    async def _controller_perf_stream(self, channel: str) -> None:
        """Poll latest controller performance every 30s and broadcast snapshots."""
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        while True:
            try:
                if not self._has_subscribers(channel):
                    logger.info(
                        "No subscribers for %s, stopping controller perf stream",
                        channel,
                    )
                    self._controller_perf_tasks.pop(channel, None)
                    return

                client = await cm.get_client(server_name)
                result = (
                    await client.bot_orchestration.get_latest_controller_performance()
                )

                # Normalize to list of snapshots
                snapshots = []
                if isinstance(result, list):
                    snapshots = result
                elif isinstance(result, dict):
                    data = result.get(
                        "data", result.get("snapshots", result.get("records", []))
                    )
                    if isinstance(data, list):
                        snapshots = data
                    elif isinstance(data, dict):
                        for key, val in data.items():
                            if isinstance(val, dict):
                                val.setdefault("controller_id", key)
                                snapshots.append(val)

                if snapshots:
                    await self._broadcast_update(channel, {"snapshots": snapshots})
                    backoff = 5

                await asyncio.sleep(30)

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(
                    "Controller perf stream error for %s: %s, retrying in %ds...",
                    channel,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

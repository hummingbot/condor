"""JSON-RPC 2.0 peer for bidirectional communication over subprocess stdio."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

log = logging.getLogger(__name__)


class JSONRPCError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[{code}] {message}")


# Standard JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JSONRPCPeer:
    """Bidirectional JSON-RPC 2.0 peer over stdin/stdout of a subprocess."""

    def __init__(self):
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: dict[str, Callable] = {}

    def register_handler(self, method: str, handler: Callable) -> None:
        self._handlers[method] = handler

    async def send_request(
        self, method: str, params: dict[str, Any], writer: asyncio.StreamWriter
    ) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        req_id = self._next_id
        self._next_id += 1

        msg = {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}
        line = json.dumps(msg) + "\n"
        writer.write(line.encode())
        await writer.drain()
        log.debug("-> %s (id=%d)", method, req_id)

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        return await future

    async def send_notification(
        self, method: str, params: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        line = json.dumps(msg) + "\n"
        writer.write(line.encode())
        await writer.drain()
        log.debug("-> %s (notification)", method)

    async def handle_line(self, line: str, writer: asyncio.StreamWriter) -> None:
        """Process one line of JSON from the subprocess stdout."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            log.warning("Invalid JSON from subprocess: %s", line[:200])
            return

        if not isinstance(data, dict):
            return

        # Response to one of our requests
        if "result" in data or "error" in data:
            req_id = data.get("id")
            future = self._pending.pop(req_id, None)
            if future and not future.done():
                if "error" in data:
                    err = data["error"]
                    future.set_exception(
                        JSONRPCError(err.get("code", -1), err.get("message", ""), err.get("data"))
                    )
                else:
                    future.set_result(data.get("result"))
            return

        # Incoming request/notification from the agent
        method = data.get("method")
        params = data.get("params", {})
        msg_id = data.get("id")  # None for notifications

        handler = self._handlers.get(method)
        if handler is None:
            log.warning("No handler for reverse-RPC method: %s", method)
            if msg_id is not None:
                resp = {
                    "jsonrpc": "2.0",
                    "error": {"code": METHOD_NOT_FOUND, "message": f"Method not found: {method}"},
                    "id": msg_id,
                }
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
            return

        try:
            result = handler(**params) if not asyncio.iscoroutinefunction(handler) else await handler(**params)
        except Exception as e:
            log.exception("Handler error for %s", method)
            if msg_id is not None:
                resp = {
                    "jsonrpc": "2.0",
                    "error": {"code": INTERNAL_ERROR, "message": str(e)},
                    "id": msg_id,
                }
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
            return

        # Send response only for requests (not notifications)
        if msg_id is not None:
            resp = {"jsonrpc": "2.0", "result": result, "id": msg_id}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()

    def cancel_all(self) -> None:
        """Cancel all pending futures (used during shutdown)."""
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

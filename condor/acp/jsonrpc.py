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
        # The `data` payload is where an ACP bridge puts the actual cause — a
        # bare "[-32603] Internal error" reaches the user with no way to tell a
        # bad handshake from a refused subprocess. Keep it in the string.
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("details") or data.get("message") or "") or str(data)
        elif data:
            detail = str(data)
        super().__init__(
            f"[{code}] {message}" + (f": {detail[:300]}" if detail else "")
        )


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
        # Set by :meth:`fail_all` when the connection dies for good. Sticky, so
        # a request that races the EOF -- registered a moment *after* the read
        # loop swept the pending table -- fails with the same real error
        # instead of parking on a future nobody will ever settle (CORR-329).
        self._failure: BaseException | None = None

    def register_handler(self, method: str, handler: Callable) -> None:
        self._handlers[method] = handler

    async def send_request(
        self,
        method: str,
        params: dict[str, Any],
        writer: asyncio.StreamWriter,
        timeout: float | None = None,
    ) -> Any:
        """Send a JSON-RPC request and wait for the response.

        ``timeout`` bounds that wait: nothing else in the peer does, so a child
        that reads our line and never answers parks the caller forever
        (CORR-333). On expiry the pending entry is dropped -- an abandoned
        request must not leak a future that only ``cancel_all`` would ever
        clear -- and :class:`asyncio.TimeoutError` propagates to the caller.
        """
        if self._failure is not None:
            raise self._failure

        req_id = self._next_id
        self._next_id += 1

        msg = {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}
        line = json.dumps(msg) + "\n"
        writer.write(line.encode())
        await writer.drain()
        log.debug("-> %s (id=%d)", method, req_id)

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        # Checked again after the drain above: the peer can die while we are
        # writing, and a future registered after that sweep would wait out the
        # whole timeout for an answer that can never come.
        if self._failure is not None:
            raise self._failure
        self._pending[req_id] = future
        if timeout is None:
            return await future
        try:
            return await asyncio.wait_for(future, timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._pending.pop(req_id, None)
            raise

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
                    if not isinstance(err, dict):
                        # The spec says an object; a peer that sends a bare
                        # string still has to settle the future, not blow up
                        # the caller's read loop (CORR-328).
                        err = {"message": str(err)}
                    future.set_exception(
                        JSONRPCError(
                            err.get("code", -1), err.get("message", ""), err.get("data")
                        )
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
                    "error": {
                        "code": METHOD_NOT_FOUND,
                        "message": f"Method not found: {method}",
                    },
                    "id": msg_id,
                }
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
            return

        try:
            result = (
                handler(**params)
                if not asyncio.iscoroutinefunction(handler)
                else await handler(**params)
            )
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
        """Cancel all pending futures (used during our own shutdown)."""
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    def fail_all(self, exc: BaseException) -> None:
        """Settle every pending future with ``exc``: the connection is gone.

        Not :meth:`cancel_all`. A cancelled future raises ``CancelledError``
        into whoever awaits it, and that is a ``BaseException`` that every
        ``except Exception`` between here and the user walks straight past --
        asyncio and the callers alike read it as "this task was cancelled"
        rather than "the agent died", so a launch that failed surfaced as a
        silent cancellation and the caller's cleanup never ran (CORR-329).
        An exception says what happened and is catchable.

        Use it when the peer stopped being able to answer (EOF on stdout);
        ``cancel_all`` stays for the shutdown *we* initiate, where a
        cancellation is the truth.
        """
        self._failure = exc
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
                # Retrieve it here so a future nobody awaits any more -- a
                # stale prompt settled only by its done-callback -- does not
                # log "exception was never retrieved" when it is collected.
                # A real awaiter still gets it raised.
                future.exception()
        self._pending.clear()

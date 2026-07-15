"""Unix-socket JSON-RPC control server.

Transport only: one newline-delimited JSON request per connection
``{"id", "method", "params"}`` -> one response ``{"id", "result"}`` or
``{"id", "error": {"status", "message"}}``. Handlers (see ``handlers.py``) are
plain (async or sync) callables that raise exceptions carrying a ``.status`` +
``.message`` (e.g. ExecutorOpError) which are surfaced verbatim.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

from condor.control import CONTROL_SOCKET_PATH

logger = logging.getLogger(__name__)

Handler = Callable[..., object]


class ControlServer:
    def __init__(
        self,
        handlers: dict[str, Handler],
        socket_path: str | None = None,
    ):
        self._handlers = handlers
        # Resolve at construction (not import) so tests/deployments that set
        # CONDOR_CONTROL_SOCKET after import still get their own socket —
        # a test app must never contend for the live process's socket.
        self._socket_path = (
            socket_path
            or os.environ.get("CONDOR_CONTROL_SOCKET")
            or CONTROL_SOCKET_PATH
        )
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        p = Path(self._socket_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            # Ping before unlink: blindly clearing the path lets a second
            # process steal the socket from a healthy server (this happened —
            # two main.py processes, one shutdown removed the shared socket).
            if await self._socket_alive(p):
                raise RuntimeError(
                    f"another control server is already live on {p} — "
                    "refusing to steal the socket (is a second process running?)"
                )
            p.unlink()  # proven stale (connect refused)
        self._server = await asyncio.start_unix_server(
            self._handle_conn, path=self._socket_path
        )
        # Owner-only: the handlers include executor create/lifecycle/delegation
        # — any local user who can connect can trade. 0600 restricts connect()
        # to this uid (enforced on both Linux and macOS).
        os.chmod(self._socket_path, 0o600)
        logger.info("control server listening on %s", self._socket_path)

    @staticmethod
    async def _socket_alive(p: Path) -> bool:
        """True if something is accepting connections on the socket path."""
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(p)), timeout=2
            )
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        p = Path(self._socket_path)
        if p.exists():
            p.unlink()

    async def _handle_conn(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            resp = await self._dispatch(line)
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
        except Exception:
            logger.exception("control server: connection error")
        finally:
            writer.close()

    async def _dispatch(self, line: bytes) -> dict:
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            return {"id": None, "error": {"status": 400, "message": f"bad request: {e}"}}
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        handler = self._handlers.get(method)
        if handler is None:
            return {"id": rid, "error": {"status": 404, "message": f"unknown method: {method}"}}
        try:
            result = handler(**params)
            if asyncio.iscoroutine(result):
                result = await result
            return {"id": rid, "result": result}
        except Exception as e:
            status = getattr(e, "status", 500)
            message = getattr(e, "message", str(e))
            if status >= 500:
                logger.exception("control handler %s failed", method)
            return {"id": rid, "error": {"status": status, "message": message}}

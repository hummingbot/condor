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
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

from condor.control import CONTROL_SOCKET_PATH

logger = logging.getLogger(__name__)

Handler = Callable[..., object]


class ControlServer:
    # Methods that receive the server-assigned connection id as a parameter —
    # the condor-direct registration binds a capability's lifetime to its
    # persistent connection (§6.2).
    CONNECTION_SCOPED = {"direct.register"}

    def __init__(
        self,
        handlers: dict[str, Handler],
        socket_path: str | None = None,
        on_conn_close: Optional[Callable[[str], None]] = None,
    ):
        self._handlers = handlers
        self._on_conn_close = on_conn_close
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
        # Owner-only: the handlers include executor create/lifecycle
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
        """Serve requests on one connection until it closes.

        One-shot clients (send a line, read a line, close) behave exactly as
        before; a PERSISTENT connection may keep sending requests — that is
        what binds a condor-direct capability's lifetime (§6.2): the moment
        the connection drops (including SIGKILL of the wrapper), the on-close
        hook revokes anything registered on it.
        """
        conn_id = uuid.uuid4().hex
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                resp = await self._dispatch(line, conn_id)
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
        except Exception:
            logger.exception("control server: connection error")
        finally:
            if self._on_conn_close is not None:
                try:
                    self._on_conn_close(conn_id)
                except Exception:
                    logger.exception("control server: on_conn_close hook failed")
            writer.close()

    async def _dispatch(self, line: bytes, conn_id: str = "") -> dict:
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
            if method in self.CONNECTION_SCOPED:
                params = {**params, "_connection_id": conn_id}
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

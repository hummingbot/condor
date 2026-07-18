"""Client for the unix-socket control server. One request per connection."""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Optional

from condor.control import CONTROL_SOCKET_PATH

_ids = itertools.count(1)


class ControlError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message


async def call_control(
    method: str,
    params: Optional[dict] = None,
    *,
    socket_path: str = CONTROL_SOCKET_PATH,
    timeout: float = 60.0,
) -> object:
    """Send one JSON-RPC request to the control server; return its result.

    Raises ControlError on transport failure or a server-reported error.
    """
    req = {"id": next(_ids), "method": method, "params": params or {}}
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path), timeout=timeout
        )
    except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
        raise ControlError(
            503, f"control socket unavailable at {socket_path}: {e}"
        ) from e
    try:
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    finally:
        writer.close()
    if not line:
        raise ControlError(502, "control server closed connection with no response")
    resp = json.loads(line)
    err = resp.get("error")
    if err:
        raise ControlError(err.get("status", 500), err.get("message", "unknown error"))
    return resp.get("result")


class PersistentControlConnection:
    """A long-lived control connection.

    Used for condor-direct capability registration (§6.2): the capability's
    lifetime is bound to THIS connection — the server revokes it the moment
    the connection closes (including SIGKILL of the client process). Requests
    are serialized over the one connection.
    """

    def __init__(self, socket_path: str = CONTROL_SOCKET_PATH):
        self._socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self._socket_path), timeout=10
            )
        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            raise ControlError(
                503, f"control socket unavailable at {self._socket_path}: {e}"
            ) from e

    async def call(self, method: str, params: Optional[dict] = None, timeout: float = 60.0) -> object:
        if not self.connected:
            raise ControlError(503, "persistent control connection is closed")
        req = {"id": next(_ids), "method": method, "params": params or {}}
        async with self._lock:
            self._writer.write((json.dumps(req) + "\n").encode())
            await self._writer.drain()
            line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
        if not line:
            raise ControlError(502, "control server closed the persistent connection")
        resp = json.loads(line)
        err = resp.get("error")
        if err:
            raise ControlError(err.get("status", 500), err.get("message", "unknown error"))
        return resp.get("result")

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

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
        raise ControlError(503, f"control socket unavailable at {socket_path}: {e}")
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

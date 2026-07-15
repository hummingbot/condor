"""Local control channel — a unix-domain-socket JSON-RPC surface the persistent
Condor process serves so a harness-spawned MCP subprocess can drive the live
executor runtime (and, later, agent sessions) without the web REST app.

Replaces ``call_main_api`` (HTTP to the FastAPI app) as the web/telegram UI is
retired. Local only, no port, no auth surface — the socket file's filesystem
permissions are the boundary.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_SOCKET = str(Path(__file__).resolve().parents[2] / "store" / "condor-control.sock")

# Env override lets tests and custom deployments relocate the socket.
CONTROL_SOCKET_PATH = os.environ.get("CONDOR_CONTROL_SOCKET", _DEFAULT_SOCKET)

"""WalletConnect session registry for the Hyperliquid agent-wallet connect flow (spike).

Sessions are spawned and tracked here (in the main process) rather than in the MCP
subprocess, for the same reason delegations are (see
``mcp_servers/condor/condor_client.py``): state must survive beyond a single MCP
tool call. All WalletConnect protocol / EIP-712 signing logic lives in the Node
sidecar script (``walletconnect_bridge/bridge.mjs``); this module just spawns it,
reads its newline-delimited JSON events off stdout, and -- once both signatures
land -- saves the resulting agent-wallet credential the same way the browser
"Connect Hyperliquid" flow does (see ``frontend/src/lib/wallet/hyperliquid.ts``).

See docs/architecture/hyperliquid-walletconnect-spike.md for the design writeup.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

BRIDGE_SCRIPT = Path(__file__).resolve().parent.parent / "walletconnect_bridge" / "bridge.mjs"

_URI_TIMEOUT = 20  # seconds to wait for the bridge to hand back a pairing URI
_SESSION_TTL = 15 * 60  # seconds a finished/errored session's status is kept around


@dataclass
class _Session:
    session_id: str
    server_name: str
    user_id: int
    process: asyncio.subprocess.Process
    status: dict = field(default_factory=lambda: {"status": "starting"})


_sessions: dict[str, _Session] = {}


def _build_hyperliquid_credentials(main_address: str, agent_private_key: str) -> dict[str, dict[str, str]]:
    """Mirrors ``buildHyperliquidCredentials`` in frontend/src/lib/wallet/hyperliquid.ts."""
    return {
        "hyperliquid_perpetual": {
            "hyperliquid_perpetual_mode": "api_wallet",
            "use_vault": "false",
            "hyperliquid_perpetual_address": main_address,
            "hyperliquid_perpetual_secret_key": agent_private_key,
        },
        "hyperliquid": {
            "hyperliquid_mode": "api_wallet",
            "use_vault": "false",
            "hyperliquid_address": main_address,
            "hyperliquid_secret_key": agent_private_key,
        },
    }


async def _save_credentials(session: _Session, main_address: str, agent_private_key: str) -> dict:
    """Save both connectors, tolerating a partial failure (mirrors the browser
    flow's Promise.allSettled behavior in ConnectHyperliquid.tsx)."""
    from config_manager import get_config_manager
    from condor.server_data_service import ServerDataType, get_server_data_service

    cm = get_config_manager()
    client = await cm.get_client(session.server_name)
    creds = _build_hyperliquid_credentials(main_address, agent_private_key)

    async def _add(connector_name: str, credentials: dict) -> tuple[str, str | None]:
        try:
            await client.accounts.add_credential(
                account_name="master_account",
                connector_name=connector_name,
                credentials=credentials,
            )
            return connector_name, None
        except Exception as e:
            return connector_name, str(e)

    results = await asyncio.gather(*[_add(name, c) for name, c in creds.items()])
    get_server_data_service().invalidate(session.server_name, ServerDataType.CONNECTORS)

    saved = [name for name, err in results if err is None]
    failed = {name: err for name, err in results if err is not None}
    return {"saved_connectors": saved, "failed_connectors": failed}


_EVENT_PREFIX = "CONDOR_WC_EVENT "  # must match walletconnect_bridge/bridge.mjs's EVENT_PREFIX


async def _drain(session: _Session) -> None:
    """Background task: read the bridge's stdout line by line and update status.

    The bridge's own dependencies (e.g. @walletconnect/sign-client's internal
    logger) can write unrelated lines to stdout, so only lines carrying our
    marker prefix are treated as protocol events -- everything else is quietly
    ignored rather than logged as a warning, since it's expected noise, not a
    sign anything is wrong.
    """
    assert session.process.stdout is not None
    try:
        async for raw in session.process.stdout:
            line = raw.decode().strip()
            if not line.startswith(_EVENT_PREFIX):
                continue
            try:
                evt = json.loads(line[len(_EVENT_PREFIX) :])
            except json.JSONDecodeError:
                logger.warning("walletconnect bridge %s: unparseable event line: %s", session.session_id, line)
                continue

            kind = evt.get("event")
            if kind == "uri":
                session.status = {"status": "pending_approval", "uri": evt["uri"]}
            elif kind == "approved":
                session.status = {"status": "pending_signatures", "main_address": evt["mainAddress"]}
            elif kind == "done":
                try:
                    save_result = await _save_credentials(session, evt["mainAddress"], evt["agentPrivateKey"])
                    session.status = {
                        "status": "done",
                        "main_address": evt["mainAddress"],
                        "agent_address": evt["agentAddress"],
                        **save_result,
                    }
                except Exception as e:
                    # The signature already happened and can't be replayed -- surface the
                    # real cause (e.g. the hummingbot-api server being unreachable) instead
                    # of letting it fall through to the generic "process exited
                    # unexpectedly" message below, which would hide what actually failed.
                    session.status = {
                        "status": "error",
                        "message": (
                            f"Signed successfully (agent {evt['agentAddress']} authorized "
                            f"for {evt['mainAddress']}) but failed to save the credential "
                            f"to '{session.server_name}': {e}. The signature can't be "
                            "reused -- reconnect to try again."
                        ),
                    }
            elif kind == "error":
                session.status = {"status": "error", "message": evt.get("message", "Unknown bridge error.")}
    finally:
        await session.process.wait()
        if session.status.get("status") not in ("done", "error"):
            stderr = b""
            if session.process.stderr is not None:
                stderr = await session.process.stderr.read()
            session.status = {
                "status": "error",
                "message": (
                    f"Bridge process exited unexpectedly (code {session.process.returncode}): "
                    f"{stderr.decode(errors='replace')[-2000:]}"
                ),
            }
        # Keep the terminal status around for _SESSION_TTL so a slow poller still sees it,
        # then drop the session so the in-memory registry doesn't grow unbounded.
        await asyncio.sleep(_SESSION_TTL)
        _sessions.pop(session.session_id, None)


async def start_walletconnect_session(server_name: str, user_id: int) -> dict:
    if not os.environ.get("WALLETCONNECT_PROJECT_ID"):
        raise RuntimeError(
            "WALLETCONNECT_PROJECT_ID is not set. Get a free project id at "
            "https://cloud.reown.com and set it in the environment."
        )
    if not BRIDGE_SCRIPT.exists():
        raise RuntimeError(f"WalletConnect bridge script not found at {BRIDGE_SCRIPT}")

    session_id = uuid.uuid4().hex[:12]
    process = await asyncio.create_subprocess_exec(
        "node",
        str(BRIDGE_SCRIPT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BRIDGE_SCRIPT.parent),
    )
    session = _Session(session_id=session_id, server_name=server_name, user_id=user_id, process=process)
    _sessions[session_id] = session
    asyncio.create_task(_drain(session))

    for _ in range(_URI_TIMEOUT * 10):
        if session.status.get("status") != "starting":
            break
        await asyncio.sleep(0.1)

    if session.status.get("status") == "pending_approval":
        return {"session_id": session_id, "uri": session.status["uri"]}
    if session.status.get("status") == "error":
        raise RuntimeError(session.status["message"])

    # Neither a uri nor an error arrived in time -- the bridge has its own internal
    # timeout on the relay handshake (see walletconnect_bridge/bridge.mjs), so this
    # shouldn't happen, but don't leave a stuck subprocess (and the _drain task
    # reading its stdout) running forever if it somehow does.
    session.process.kill()
    _sessions.pop(session_id, None)
    raise RuntimeError("Timed out waiting for the WalletConnect bridge to produce a pairing URI.")


async def get_session_status(session_id: str, user_id: int) -> dict | None:
    session = _sessions.get(session_id)
    if session is None or session.user_id != user_id:
        return None
    return {"session_id": session_id, **session.status}

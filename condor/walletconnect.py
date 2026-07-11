"""WalletConnect session registry for the Hyperliquid agent-wallet connect flow (spike).

Sessions are spawned and tracked here (in the main process) rather than in the MCP
subprocess, for the same reason delegations are (see
``mcp_servers/condor/condor_client.py``): state must survive beyond a single MCP
tool call. All WalletConnect protocol / EIP-712 signing logic lives in the Node
sidecar script (``walletconnect_bridge/bridge.mjs``); this module just spawns it,
reads its newline-delimited JSON events off stdout, and -- once a signature lands
-- either saves the resulting agent-wallet credential (agent step) or just
confirms (builder step), the same way the browser "Connect Hyperliquid" flow does
(see ``frontend/src/lib/wallet/hyperliquid.ts``).

The flow is two independent steps, each its own fresh WalletConnect pairing
(fresh QR / fresh MetaMask deep link) rather than one session driving both
signatures back to back -- a fresh pairing reliably foregrounds the wallet app;
a second request sent to an already-paired-but-backgrounded session doesn't
always prompt it:

  1. "agent"   -- pair, confirm the wallet address, sign ApproveAgent, save the
                  resulting credential.
  2. "builder" -- pair again (same wallet -- verified, see
                  walletconnect_bridge/bridge.mjs), sign ApproveBuilderFee.
                  Skipped entirely if the connected address already approved
                  at least Condor's builder fee on-chain (checked via
                  Hyperliquid's public info endpoint, no signature needed for
                  the check itself).

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
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


def generate_qr_png(uri: str) -> bytes:
    """PNG bytes for a scannable QR of `uri`. Shared by every surface that
    delivers a WalletConnect pairing (Telegram's /keys button, the MCP tool)
    so QR generation lives in exactly one place."""
    import io

    import qrcode

    buf = io.BytesIO()
    qrcode.make(uri).save(buf, format="PNG")
    return buf.getvalue()


# MetaMask is the only wallet Condor currently builds a deep-link button for: it's
# the only one of the wallets we support with a `mobile.universal` (https://) link
# in the WalletConnect registry, which Telegram's InlineKeyboardButton requires
# (its `url` field only accepts http(s)/tg://, not native app schemes like
# `rabby://`).
_DEEP_LINK_WALLET_SEARCH_TERMS = ["metamask"]

_wallet_universal_links: dict[str, str] | None = None


async def _get_wallet_universal_links() -> dict[str, str]:
    """{"MetaMask": "https://metamask.app.link", ...} fetched once from the
    WalletConnect Explorer registry and cached for the process lifetime -- this is
    wallet-directory metadata, not per-session state, and essentially never changes,
    so there's no reason to refetch it on every connect. Fetching (rather than
    hardcoding these URLs) means a wallet renaming/changing its universal link
    domain doesn't silently break the button."""
    global _wallet_universal_links
    if _wallet_universal_links is not None:
        return _wallet_universal_links

    project_id = os.environ["WALLETCONNECT_PROJECT_ID"]
    links: dict[str, str] = {}

    async def _fetch_one(client: httpx.AsyncClient, term: str) -> None:
        try:
            resp = await client.get(
                "https://explorer-api.walletconnect.com/v3/wallets",
                params={"projectId": project_id, "search": term, "entries": 1},
            )
            resp.raise_for_status()
            for entry in resp.json().get("listings", {}).values():
                universal = (entry.get("mobile") or {}).get("universal")
                if universal:
                    links[entry["name"]] = universal
                break
        except Exception:
            logger.warning("Failed to fetch WalletConnect mobile link for %r", term, exc_info=True)

    async with httpx.AsyncClient(timeout=5) as client:
        await asyncio.gather(*[_fetch_one(client, term) for term in _DEEP_LINK_WALLET_SEARCH_TERMS])

    _wallet_universal_links = links
    return links


# Mirrors walletconnect_bridge/bridge.mjs's BUILDER_ADDRESS / BUILDER_MAX_FEE_RATE.
# Used only to check whether the connected address already approved Condor's
# builder fee on-chain (a plain, unsigned info-endpoint read), so the "approve
# builder fee" step can be skipped for a returning user -- no need to spawn Node
# for this, it's a single unauthenticated HTTP call.
_BUILDER_ADDRESS = "0x10ba451e6439efc6a17dc20d21121aa838100705"
_BUILDER_MAX_FEE_RATE_TENTHS_BPS = 10  # "0.01%" == 1 bps == 10 tenths-of-a-bp
_HL_INFO_URL = "https://api.hyperliquid.xyz/info"


async def _get_builder_approval_tenths_bps(main_address: str) -> int:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _HL_INFO_URL,
            json={"type": "maxBuilderFee", "user": main_address, "builder": _BUILDER_ADDRESS},
        )
        resp.raise_for_status()
        return int(resp.json())


BRIDGE_SCRIPT = Path(__file__).resolve().parent.parent / "walletconnect_bridge" / "bridge.mjs"

_URI_TIMEOUT = 20  # seconds to wait for the bridge to hand back a pairing URI
_SESSION_TTL = 15 * 60  # seconds a finished/errored session's status is kept around
# "step_done" (agent step finished, builder step optionally pending) is kept around
# much longer -- the user may come back and tap "Continue" well after the fact,
# and the agent credential is already saved by that point regardless.
_STEP_DONE_TTL = 24 * 60 * 60


@dataclass
class _Session:
    session_id: str
    server_name: str
    user_id: int
    step: str = "agent"  # "agent" | "builder" -- which step the current/last subprocess ran
    process: asyncio.subprocess.Process | None = None
    main_address: str | None = None
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
    """Background task: read the current step's bridge subprocess stdout line by
    line and update status. A new one of these is started every time a step's
    subprocess is (re)spawned -- see ``_spawn_step``.

    The bridge's own dependencies (e.g. @walletconnect/sign-client's internal
    logger) can write unrelated lines to stdout, so only lines carrying our
    marker prefix are treated as protocol events -- everything else is quietly
    ignored rather than logged as a warning, since it's expected noise, not a
    sign anything is wrong.
    """
    process = session.process
    assert process is not None and process.stdout is not None
    try:
        async for raw in process.stdout:
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
                session.status = {"status": "pending_approval", "uri": evt["uri"], "step": session.step}
            elif kind == "approved":
                session.main_address = evt["mainAddress"]
                session.status = {"status": "pending_signature", "step": session.step, "main_address": evt["mainAddress"]}
            elif kind == "done":
                if session.step == "agent":
                    await _handle_agent_done(session, evt)
                else:
                    session.status = {"status": "done", "step": "builder", "main_address": evt["mainAddress"]}
            elif kind == "error":
                session.status = {"status": "error", "message": evt.get("message", "Unknown bridge error.")}
    finally:
        await process.wait()
        state = session.status.get("status")
        if state not in ("done", "error", "step_done"):
            # The process exited without ever emitting a terminal event -- a crash.
            stderr = b""
            if process.stderr is not None:
                stderr = await process.stderr.read()
            session.status = {
                "status": "error",
                "message": (
                    f"Bridge process exited unexpectedly (code {process.returncode}): "
                    f"{stderr.decode(errors='replace')[-2000:]}"
                ),
            }
            state = "error"

        ttl = _SESSION_TTL if state in ("done", "error") else _STEP_DONE_TTL
        await asyncio.sleep(ttl)
        # Only clean up if nothing has advanced the session in the meantime (e.g.
        # the user tapped "Continue" and a new step's subprocess/_drain task took
        # over) -- otherwise this stale task would rip out a still-live session.
        if session.status.get("status") == state:
            _sessions.pop(session.session_id, None)


async def _handle_agent_done(session: _Session, evt: dict) -> None:
    try:
        save_result = await _save_credentials(session, evt["mainAddress"], evt["agentPrivateKey"])
    except Exception as e:
        # The signature already happened and can't be replayed -- surface the
        # real cause (e.g. the hummingbot-api server being unreachable) instead
        # of letting it fall through to the generic "process exited
        # unexpectedly" message, which would hide what actually failed.
        session.status = {
            "status": "error",
            "message": (
                f"Signed successfully (agent {evt['agentAddress']} authorized "
                f"for {evt['mainAddress']}) but failed to save the credential "
                f"to '{session.server_name}': {e}. The signature can't be "
                "reused -- reconnect to try again."
            ),
        }
        return

    needs_builder_step = True
    try:
        approved = await _get_builder_approval_tenths_bps(evt["mainAddress"])
        needs_builder_step = approved < _BUILDER_MAX_FEE_RATE_TENTHS_BPS
    except Exception:
        # Can't confirm either way -- default to offering the step rather than
        # silently skipping a builder-fee approval the user might actually need.
        logger.warning("Failed to check existing Hyperliquid builder-fee approval for %s", evt["mainAddress"], exc_info=True)

    session.status = {
        "status": "step_done",
        "step": "agent",
        "main_address": evt["mainAddress"],
        "agent_address": evt["agentAddress"],
        "needs_builder_step": needs_builder_step,
        **save_result,
    }


async def _spawn_step(session: _Session, step: str, extra_argv: list[str]) -> dict:
    if not os.environ.get("WALLETCONNECT_PROJECT_ID"):
        raise RuntimeError(
            "WALLETCONNECT_PROJECT_ID is not set. Get a free project id at "
            "https://cloud.reown.com and set it in the environment."
        )
    if not BRIDGE_SCRIPT.exists():
        raise RuntimeError(f"WalletConnect bridge script not found at {BRIDGE_SCRIPT}")

    session.step = step
    session.status = {"status": "starting", "step": step}
    session.process = await asyncio.create_subprocess_exec(
        "node",
        str(BRIDGE_SCRIPT),
        step,
        *extra_argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BRIDGE_SCRIPT.parent),
    )
    asyncio.create_task(_drain(session))

    # Runs concurrently with the relay handshake below so it adds no latency to the
    # common case (only the very first session per process pays the registry fetch --
    # every session after that hits the cache).
    wallet_links_task = asyncio.create_task(_get_wallet_universal_links())

    for _ in range(_URI_TIMEOUT * 10):
        if session.status.get("status") != "starting":
            break
        await asyncio.sleep(0.1)

    if session.status.get("status") == "pending_approval":
        uri = session.status["uri"]
        links = await wallet_links_task
        encoded = quote(uri, safe="")
        deep_links = {name: f"{base}/wc?uri={encoded}" for name, base in links.items()}
        return {"session_id": session.session_id, "uri": uri, "deep_links": deep_links, "step": step}
    if session.status.get("status") == "error":
        wallet_links_task.cancel()
        raise RuntimeError(session.status["message"])

    # Neither a uri nor an error arrived in time -- the bridge has its own internal
    # timeout on the relay handshake (see walletconnect_bridge/bridge.mjs), so this
    # shouldn't happen, but don't leave a stuck subprocess (and the _drain task
    # reading its stdout) running forever if it somehow does.
    session.process.kill()
    _sessions.pop(session.session_id, None)
    raise RuntimeError("Timed out waiting for the WalletConnect bridge to produce a pairing URI.")


async def start_walletconnect_session(server_name: str, user_id: int) -> dict:
    """Begins the flow: pairs, confirms the wallet address, and signs ApproveAgent
    (step "agent"). Returns {session_id, uri, deep_links, step}."""
    session_id = uuid.uuid4().hex[:12]
    session = _Session(session_id=session_id, server_name=server_name, user_id=user_id)
    _sessions[session_id] = session
    return await _spawn_step(session, "agent", [])


async def advance_walletconnect_session(session_id: str, user_id: int) -> dict:
    """Continues a session whose agent step finished and needs the builder-fee
    approval step: pairs again (fresh QR/link) and signs ApproveBuilderFee."""
    session = _sessions.get(session_id)
    if session is None or session.user_id != user_id:
        raise RuntimeError("Unknown or expired WalletConnect session -- start again from /keys.")
    status = session.status
    if status.get("status") != "step_done" or not status.get("needs_builder_step"):
        raise RuntimeError("This session has nothing left to approve.")
    if not session.main_address:
        raise RuntimeError("Missing the connected wallet address -- reconnect from /keys.")
    return await _spawn_step(session, "builder", [session.main_address])


async def get_session_status(session_id: str, user_id: int) -> dict | None:
    session = _sessions.get(session_id)
    if session is None or session.user_id != user_id:
        return None
    return {"session_id": session_id, **session.status}

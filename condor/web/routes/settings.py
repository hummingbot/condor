from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query

from condor.web.auth import (
    get_current_user,
    require_owner,
    require_server_access_query,
)
from condor.web.models import (
    AddCredentialRequest,
    AddServerRequest,
    CredentialInfo,
    GatewayNetworkUpdateRequest,
    GatewayPullRequest,
    GatewayStartRequest,
    GatewayWalletAddRequest,
    GatewayWalletDefaultRequest,
    ServerInfo,
    UpdateServerRequest,
    WebUser,
)
from condor.web.routes._errors import upstream_error
from config_manager import ServerPermission, get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


# ── Helpers ──


# The OWNER ceiling this module drew now lives in ``condor.web.auth`` beside the
# TRADER floor it sits on: ``routes/dex.py`` needs the same line for the gateway
# token list (SEC-207), and one shared helper is the only way that line stays in
# one place. Kept under its original private name so this module reads unchanged.
_require_owner = require_owner


async def _get_client(cm, server_name: str):
    try:
        return await cm.get_client(server_name)
    except ValueError as e:
        # The config manager's own rejection ("no such server"). Its text names
        # nothing the caller did not already type, so it can be shown as-is.
        raise HTTPException(status_code=502, detail=f"Cannot connect to server: {e}")
    except Exception as e:
        # Anything else came off the wire, and its string carries the backend
        # address — log it, show the caller only the safe description.
        logger.exception("Cannot connect to server '%s'", server_name)
        raise upstream_error("Cannot connect to server", e)


# ── Servers ──


@router.get("/servers", response_model=list[ServerInfo])
async def list_settings_servers(user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    accessible = cm.list_accessible_servers(user.id)

    from condor.server_data_service import ServerDataType, get_server_data_service

    sds = get_server_data_service()

    # Fetch status for all servers concurrently (uses SDS cache, instant if warm)
    async def _get_status(name: str) -> dict:
        result = await sds.get_or_fetch(name, ServerDataType.SERVER_STATUS)
        return result if isinstance(result, dict) else {}

    statuses = await asyncio.gather(*[_get_status(name) for name in accessible])

    # Which one the star should be filled on. Read once: the config manager falls
    # back to the global default and then to the first server, so this is the
    # server that actually answers, not only an explicit `chat_defaults` entry.
    default_server = cm.get_chat_default_server(user.id)

    results = []
    for (name, cfg), status in zip(accessible.items(), statuses):
        perm = cm.get_server_permission(user.id, name)
        online = status.get("status") == "online"
        results.append(
            ServerInfo(
                name=name,
                host=cfg.get("host", ""),
                port=cfg.get("port", 0),
                online=online,
                permission=perm.value if perm else "trader",
                is_default=name == default_server,
                shared_with=(
                    cm.get_server_members(name)
                    if perm == ServerPermission.OWNER
                    else []
                ),
            )
        )

    return sorted(results, key=lambda s: (not s.online, s.name))


@router.post("/servers")
async def add_server(req: AddServerRequest, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    ok = cm.add_server(
        name=req.name,
        host=req.host,
        port=req.port,
        username=req.username,
        password=req.password,
        owner_id=user.id,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Server name already exists")
    return {"added": True, "name": req.name}


@router.put("/servers/{name}")
async def update_server(
    name: str,
    req: UpdateServerRequest,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=404, detail="Server not found")
    _require_owner(cm, user.id, name)
    ok = cm.modify_server(
        name=name,
        host=req.host,
        port=req.port,
        username=req.username,
        password=req.password,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Server not found")
    return {"updated": True}


@router.delete("/servers/{name}")
async def delete_server(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=404, detail="Server not found")
    _require_owner(cm, user.id, name)
    ok = cm.delete_server(name, actor_id=user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Server not found")
    return {"deleted": True}


@router.post("/servers/{name}/default")
async def set_default_server(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=404, detail="Server not found")
    cm.set_chat_default_server(user.id, name)
    # Both records Telegram's own "set default" writes, or the two disagree:
    # `chat_defaults` is what `get_effective_server` resolves, while the general
    # preference is what a routine, an agent or MCP reads as the active server.
    # Writing only the first left those surfaces pinned to the previous choice.
    from condor.preferences import load_user_data_for, set_active_server

    set_active_server(load_user_data_for(user.id), name)
    return {"default": True, "name": name}


# ── Gateway ──


@router.get("/gateway/status")
async def gateway_status(
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    client = await _get_client(cm, server)
    try:
        info = await client.gateway.get_status()
        # The inner "running" field from the API is the actual status
        is_running = info.get("running", False) if isinstance(info, dict) else False
        result = {"running": is_running, "info": info}
        # Extract container details if available
        if isinstance(info, dict):
            result["image"] = info.get("image", None)
            result["created_at"] = info.get("created", info.get("created_at", None))
            result["container_status"] = info.get("status", None)
        return result
    except Exception:
        return {"running": False, "info": None}


@router.post("/gateway/pull")
async def gateway_pull(
    req: GatewayPullRequest,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # Pulling an image rewrites what code will run on the owner's host next
    # restart — infrastructure, not trading.
    _require_owner(cm, user.id, server)
    client = await _get_client(cm, server)
    try:
        # Parse "image:tag" format (e.g. "hummingbot/gateway:latest")
        parts = req.image.rsplit(":", 1)
        image_name = parts[0]
        tag = parts[1] if len(parts) > 1 else "latest"
        result = await client.docker.pull_image(image_name, tag)
        return {"pulled": True, "image": req.image, "result": result}
    except Exception as e:
        logger.exception("Failed to pull gateway image '%s' on '%s'", req.image, server)
        raise upstream_error("Failed to pull gateway image", e)


@router.get("/gateway/pull-status")
async def gateway_pull_status(
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    client = await _get_client(cm, server)
    try:
        result = await client.docker.get_pull_status()
        return result
    except Exception as e:
        logger.exception("Failed to fetch gateway pull status from '%s'", server)
        raise upstream_error("Failed to fetch pull status", e)


@router.post("/gateway/start")
async def gateway_start(
    req: GatewayStartRequest,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # The caller picks the image and port of a container started on the owner's
    # host. That is the owner's decision, not a shared trader's.
    _require_owner(cm, user.id, server)
    client = await _get_client(cm, server)
    try:
        result = await client.gateway.start(
            {
                "image": req.image,
                "port": req.port,
            }
        )
        return {"started": True, "result": result}
    except Exception as e:
        logger.exception("Failed to start gateway on '%s'", server)
        raise upstream_error("Failed to start gateway", e)


@router.post("/gateway/stop")
async def gateway_stop(
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # Stopping the gateway cuts DEX connectivity for every bot on the server,
    # including the owner's. Same line delete_credential draws.
    _require_owner(cm, user.id, server)
    client = await _get_client(cm, server)
    try:
        result = await client.gateway.stop()
        return {"stopped": True, "result": result}
    except Exception as e:
        logger.exception("Failed to stop gateway on '%s'", server)
        raise upstream_error("Failed to stop gateway", e)


@router.post("/gateway/restart")
async def gateway_restart(
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # A restart drops in-flight DEX orders for everyone on the server.
    _require_owner(cm, user.id, server)
    client = await _get_client(cm, server)
    try:
        result = await client.gateway.restart()
        return {"restarted": True, "result": result}
    except Exception as e:
        logger.exception("Failed to restart gateway on '%s'", server)
        raise upstream_error("Failed to restart gateway", e)


@router.get("/gateway/logs")
async def gateway_logs(
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    client = await _get_client(cm, server)
    try:
        logs = await client.gateway.get_logs()
        return {"logs": logs}
    except Exception as e:
        logger.exception("Failed to fetch gateway logs from '%s'", server)
        raise upstream_error("Failed to fetch gateway logs", e)


# ── Gateway Networks (RPC) ──

_URL_VALUE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


def _redact_url(value: str) -> str:
    """Collapse a URL to scheme://host, hiding every part that can carry a secret.

    Paid RPC providers embed the API key either in the query string
    (Helius: ``?api-key=<secret>``) or in the path (Infura ``/v3/<key>``,
    Alchemy ``/v2/<key>``), and a URL can also carry userinfo. Rather than
    pattern-match key shapes, keep only the parts that cannot hold a
    credential — scheme, host, port — and mask everything else whenever any of
    it is present. A bare endpoint stays readable so a trader can still tell
    which node they are trading against.
    """
    parts = urlsplit(value)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    has_secret_bearing_part = (
        parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    )
    if has_secret_bearing_part:
        return f"{parts.scheme}://{host}/…"
    return value


def _redact_network_config(value):
    """Recursively mask URL-shaped strings in a network config (SEC-197)."""
    if isinstance(value, dict):
        return {k: _redact_network_config(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_network_config(v) for v in value]
    if isinstance(value, str) and _URL_VALUE.match(value):
        return _redact_url(value)
    return value


@router.get("/gateway/networks")
async def gateway_networks(
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    client = await _get_client(cm, server)
    try:
        result = await client.gateway.list_networks()
        networks = (
            result.get("networks", result) if isinstance(result, dict) else result
        )
        return {"networks": networks}
    except Exception as e:
        logger.exception("Failed to list gateway networks from '%s'", server)
        raise upstream_error("Failed to list gateway networks", e)


@router.get("/gateway/networks/{network_id}")
async def gateway_network_config(
    network_id: str,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # Reading gateway state stays at TRADER (see _require_owner's docstring),
    # but the RPC URL in a network config embeds the owner's paid provider API
    # key. Redact it server-side for anyone below the OWNER line — same admin
    # bypass as _require_owner — instead of shipping the secret and hiding it
    # in the UI (SEC-197). Redaction cannot corrupt a save: the POST below is
    # owner-gated, and owners always receive the full values.
    perm = cm.get_server_permission(user.id, server)
    is_owner = perm == ServerPermission.OWNER or cm.is_admin(user.id)
    client = await _get_client(cm, server)
    try:
        config = await client.gateway.get_network_config(network_id)
        if not is_owner:
            config = _redact_network_config(config)
        return {"network_id": network_id, "config": config, "redacted": not is_owner}
    except Exception as e:
        logger.exception(
            "Failed to fetch gateway network config '%s' from '%s'", network_id, server
        )
        raise upstream_error("Failed to fetch network config", e)


@router.post("/gateway/networks/{network_id}")
async def gateway_network_update(
    network_id: str,
    req: GatewayNetworkUpdateRequest,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # Network config carries the RPC endpoint every transaction from this server
    # is signed and broadcast through — repointing it is a server-wide change.
    _require_owner(cm, user.id, server)
    client = await _get_client(cm, server)
    try:
        result = await client.gateway.update_network_config(network_id, req.config)
        return {"updated": True, "network_id": network_id, "result": result}
    except Exception as e:
        logger.exception(
            "Failed to update gateway network config '%s' on '%s'", network_id, server
        )
        raise upstream_error("Failed to update network config", e)


# ── Gateway Wallets ──


@router.get("/gateway/wallets")
async def gateway_wallets(
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    client = await _get_client(cm, server)
    try:
        wallets = await client.accounts.list_gateway_wallets()
        return {"wallets": wallets}
    except Exception as e:
        logger.exception("Failed to list gateway wallets from '%s'", server)
        raise upstream_error("Failed to list gateway wallets", e)


async def _gateway_default_address(client, chain: str) -> str | None:
    """Current default wallet address for a chain, or None if there is none."""
    try:
        groups = await client.accounts.list_gateway_wallets()
    except Exception:
        logger.warning(
            "Could not read gateway wallets to preserve default for '%s'", chain
        )
        return None
    for group in groups or []:
        if isinstance(group, dict) and group.get("chain") == chain:
            return group.get("default_address") or None
    return None


@router.post("/gateway/wallets")
async def gateway_wallet_add(
    req: GatewayWalletAddRequest,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # Importing a private key into the owner's gateway makes that key usable by
    # everyone on the server. Whose keys live on the host is the owner's call.
    _require_owner(cm, user.id, server)
    client = await _get_client(cm, server)
    # Gateway's add-wallet always promotes the new key to the chain default, so when the user
    # opted out we capture the existing default first and restore it after the import.
    previous_default = (
        None if req.set_default else await _gateway_default_address(client, req.chain)
    )
    try:
        result = await client.accounts.add_gateway_wallet(
            chain=req.chain, private_key=req.private_key
        )
        address = result.get("address") if isinstance(result, dict) else None
        is_default = True
        if req.set_default and address:
            await client.accounts.set_default_gateway_wallet(
                chain=req.chain, address=address
            )
        elif previous_default and previous_default != address:
            try:
                await client.accounts.set_default_gateway_wallet(
                    chain=req.chain, address=previous_default
                )
                is_default = False
            except Exception:
                logger.exception(
                    "Imported wallet on '%s' but failed to restore previous default on chain '%s'",
                    server,
                    req.chain,
                )
        return {
            "added": True,
            "chain": req.chain,
            "address": address,
            "is_default": is_default,
        }
    except Exception as e:
        # Never include the request payload here — it carries the private key.
        logger.exception(
            "Failed to add gateway wallet on chain '%s' via '%s'", req.chain, server
        )
        raise upstream_error("Failed to add wallet", e)


@router.post("/gateway/wallets/default")
async def gateway_wallet_set_default(
    req: GatewayWalletDefaultRequest,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # The default wallet is the one every subsequent DEX trade signs with, so
    # switching it silently redirects the owner's flow to another address.
    _require_owner(cm, user.id, server)
    client = await _get_client(cm, server)
    try:
        result = await client.accounts.set_default_gateway_wallet(
            chain=req.chain, address=req.address
        )
        return {
            "default": True,
            "chain": req.chain,
            "address": req.address,
            "result": result,
        }
    except Exception as e:
        logger.exception(
            "Failed to set default gateway wallet on '%s' via '%s'", req.chain, server
        )
        raise upstream_error("Failed to set default wallet", e)


@router.delete("/gateway/wallets/{chain}/{address}")
async def gateway_wallet_remove(
    chain: str,
    address: str,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # Removing a key strands any bot trading from that address — owner only.
    _require_owner(cm, user.id, server)
    client = await _get_client(cm, server)
    try:
        result = await client.accounts.remove_gateway_wallet(
            chain=chain, address=address
        )
        return {"removed": True, "chain": chain, "address": address, "result": result}
    except Exception as e:
        logger.exception(
            "Failed to remove gateway wallet %s on '%s' via '%s'",
            address,
            chain,
            server,
        )
        raise upstream_error("Failed to remove wallet", e)


# ── Voice Preferences ──


@router.get("/voice")
async def get_voice_settings(user: WebUser = Depends(get_current_user)):
    """Get voice/transcription preferences for the current user."""
    cm = get_config_manager()
    prefs = cm.get_user_preferences(user.id)
    voice = prefs.get(
        "voice",
        {
            "whisper_model": "small",
            "language": None,
            "auto_send": True,
        },
    )
    from condor.preferences import VOICE_LANGUAGES, WHISPER_MODELS

    return {
        "voice": voice,
        "available_models": WHISPER_MODELS,
        "available_languages": VOICE_LANGUAGES,
    }


@router.put("/voice")
async def update_voice_settings(
    body: dict,
    user: WebUser = Depends(get_current_user),
):
    """Update voice/transcription preferences."""
    cm = get_config_manager()
    prefs = cm.get_user_preferences(user.id)
    voice = prefs.get(
        "voice",
        {
            "whisper_model": "small",
            "language": None,
            "auto_send": True,
        },
    )
    allowed_keys = {"whisper_model", "language", "auto_send"}
    for key in allowed_keys:
        if key in body:
            voice[key] = body[key]

    # Validate whisper_model
    from condor.preferences import WHISPER_MODELS

    if voice.get("whisper_model") not in WHISPER_MODELS:
        voice["whisper_model"] = "base"

    # Normalize empty language to None (auto-detect)
    if not voice.get("language"):
        voice["language"] = None

    cm.set_user_preference(user.id, "voice", voice)
    return {"voice": voice}


# ── API Keys / Credentials ──


@router.get("/credentials")
async def list_credentials(
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    client = await _get_client(cm, server)
    try:
        creds_raw = await client.accounts.list_account_credentials("master_account")
        # API may return a list of strings or a list of dicts — normalize
        credentials = []
        if isinstance(creds_raw, list):
            for item in creds_raw:
                if isinstance(item, str):
                    credentials.append({"connector_name": item, "connector_type": ""})
                elif isinstance(item, dict):
                    credentials.append(
                        {
                            "connector_name": item.get(
                                "connector_name", item.get("name", "")
                            ),
                            "connector_type": item.get(
                                "connector_type", item.get("type", "")
                            ),
                        }
                    )
        return {"credentials": credentials}
    except Exception as e:
        logger.exception("Failed to list credentials on '%s'", server)
        raise upstream_error("Failed to list credentials", e)


@router.get("/connectors")
async def list_connectors(
    server: str = Query(...),
    type: str = Query(None),
    user: WebUser = Depends(require_server_access_query),
):

    from condor.server_data_service import ServerDataType, get_server_data_service

    sds = get_server_data_service()
    raw = await sds.get_or_fetch(server, ServerDataType.ALL_CONNECTORS)
    if raw is None:
        raise HTTPException(
            status_code=502, detail="Cannot fetch connectors from server"
        )

    # API returns plain strings — filter out testnet/gateway connectors
    names = [
        c
        for c in raw
        if isinstance(c, str)
        and "testnet" not in c.lower()
        and "sandbox" not in c.lower()
        and "/" not in c
    ]
    if type:
        if type.lower() == "perpetual":
            names = [c for c in names if "perpetual" in c.lower()]
        else:
            names = [c for c in names if "perpetual" not in c.lower()]
    connectors = [
        {"name": c, "type": "perpetual" if "perpetual" in c.lower() else "spot"}
        for c in names
    ]
    return {"connectors": connectors}


@router.get("/connectors/{name}/config-map")
async def connector_config_map(
    name: str,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    client = await _get_client(cm, server)
    try:
        config_map = await client.connectors.get_config_map(name)
        return {"config_map": config_map}
    except Exception as e:
        logger.exception(
            "Failed to fetch config map for connector '%s' on '%s'", name, server
        )
        raise upstream_error("Failed to fetch connector config map", e)


@router.post("/credentials")
async def add_credential(
    req: AddCredentialRequest,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # Credentials are server configuration, not trading: a shared trader must not
    # overwrite the owner's exchange keys. Same line update_server/delete_server draw.
    _require_owner(cm, user.id, server)
    client = await _get_client(cm, server)
    try:
        result = await client.accounts.add_credential(
            account_name="master_account",
            connector_name=req.connector_name,
            credentials=req.credentials,
        )
        # Invalidate configured connectors cache
        from condor.server_data_service import ServerDataType, get_server_data_service

        get_server_data_service().invalidate(server, ServerDataType.CONNECTORS)
        return {"added": True, "result": result}
    except Exception as e:
        logger.exception(
            "Failed to add credentials for '%s' on '%s'", req.connector_name, server
        )
        raise upstream_error("Failed to add credentials", e)


@router.delete("/credentials/{connector}")
async def delete_credential(
    connector: str,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    cm = get_config_manager()
    # Deleting a key kills the owner's running bots' connectivity — owner only.
    _require_owner(cm, user.id, server)
    client = await _get_client(cm, server)
    try:
        result = await client.accounts.delete_credential(
            account_name="master_account",
            connector_name=connector,
        )
        # Invalidate configured connectors + portfolio caches so the removed key disappears immediately
        from condor.server_data_service import ServerDataType, get_server_data_service

        sds = get_server_data_service()
        sds.invalidate(server, ServerDataType.CONNECTORS)
        sds.invalidate(server, ServerDataType.PORTFOLIO)
        return {"deleted": True, "result": result}
    except Exception as e:
        logger.exception(
            "Failed to delete credentials for '%s' on '%s'", connector, server
        )
        raise upstream_error("Failed to delete credentials", e)


# ── Custom OpenAI-compatible LLM endpoints ──
#
# These read and write the same preference records the Telegram
# /agent → Change LLM → Custom endpoint flow uses (condor/preferences.py),
# so an endpoint added on either surface immediately shows up on the other.


def _provider_public(provider: dict) -> dict:
    """Strip the API key before sending an endpoint record to the browser."""
    return {
        "name": provider.get("name", ""),
        "base_url": provider.get("base_url", ""),
        "has_key": bool(provider.get("api_key")),
    }


@router.get("/custom-providers")
async def list_custom_providers(user: WebUser = Depends(get_current_user)):
    """List the user's saved OpenAI-compatible endpoints."""
    from condor.preferences import get_custom_providers, load_user_data_for

    providers = get_custom_providers(load_user_data_for(user.id))
    return {"providers": [_provider_public(p) for p in providers]}


@router.post("/custom-providers")
async def add_custom_provider(
    body: dict,
    user: WebUser = Depends(get_current_user),
):
    """Validate an endpoint and save it.

    Validation is a GET {base_url}/models, which proves the URL is reachable,
    the key is accepted, and the response is OpenAI-shaped — all in one round
    trip — and returns the model list the caller needs anyway.
    """
    from condor.llm.custom_models import (
        CustomProviderError,
        fetch_models,
        normalize_base_url,
    )
    from condor.preferences import (
        load_user_data_for,
        save_custom_provider,
        suggest_provider_name,
        unique_provider_name,
    )

    raw_url = (body.get("base_url") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    if not raw_url:
        raise HTTPException(status_code=400, detail="base_url is required")

    try:
        base_url = normalize_base_url(raw_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        resolved_url, models = await fetch_models(base_url, api_key)
    except CustomProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))

    user_data = load_user_data_for(user.id)
    requested_name = (body.get("name") or "").strip()
    name = requested_name or unique_provider_name(
        user_data, suggest_provider_name(resolved_url)
    )

    try:
        saved = save_custom_provider(user_data, name, resolved_url, api_key)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {"provider": _provider_public(saved), "models": models}


@router.get("/custom-providers/{name}/models")
async def get_custom_provider_models(
    name: str,
    user: WebUser = Depends(get_current_user),
):
    """Fetch the current chat model list for a saved endpoint."""
    from condor.llm.custom_models import CustomProviderError, fetch_models
    from condor.preferences import find_custom_provider, load_user_data_for

    provider = find_custom_provider(load_user_data_for(user.id), name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"No saved endpoint '{name}'")

    try:
        _, models = await fetch_models(
            provider["base_url"], provider.get("api_key", "")
        )
    except CustomProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"provider": _provider_public(provider), "models": models}


@router.delete("/custom-providers/{name}")
async def delete_custom_provider(
    name: str,
    user: WebUser = Depends(get_current_user),
):
    """Forget an endpoint, including its stored API key."""
    from condor.preferences import load_user_data_for, remove_custom_provider

    if not remove_custom_provider(load_user_data_for(user.id), name):
        raise HTTPException(status_code=404, detail=f"No saved endpoint '{name}'")
    return {"deleted": True, "name": name}


# ── Telemetry (FEAT-023) ──


@router.get("/telemetry")
async def get_telemetry_settings(user: WebUser = Depends(get_current_user)):
    """What this install has agreed to, and whether it could send anything.

    The collector address is fixed in the source, so consent is the only thing
    that decides whether anything is transmitted: at level ``off`` no event is
    recorded in the first place.

    Readable by every seat on purpose — anyone using an install should be able
    to see what it shares — while ``can_change`` reports who may actually
    answer. That is the admin, because consent is install-wide, and it is also
    the discriminator the dashboard consent card uses, so asking costs no
    second request. ``disclosure`` is the same copy the Telegram prompt sends.
    """
    from condor.telemetry import consent, emitter, outbox
    from condor.telemetry.prompt import DISCLOSURE

    cm = get_config_manager()
    return {
        "consent": consent.state(),
        "level": consent.level(),
        "env_overridden": consent.env_overridden(),
        "endpoint_configured": bool(outbox.endpoint()),
        "pending_events": emitter.buffered(),
        "privacy_doc": "PRIVACY.md",
        "can_change": cm.is_admin(user.id),
        "disclosure": DISCLOSURE,
    }


@router.put("/telemetry")
async def set_telemetry_settings(
    level: str = Query(..., description="ping | usage | off"),
    user: WebUser = Depends(get_current_user),
):
    """Change the install's telemetry level. Admin only, and reversible.

    ``ping`` is the floor for an install that has never answered — silence is
    not refusal — but ``off`` is a real answer here, because an admin who wants
    this install to report nothing must have a way to say so in the product
    rather than only by editing ``.env``. It is recorded as a refusal in
    ``config.yml``, so it survives upgrades; ``ping``/``usage`` re-enable.

    Turning off, like downgrading from ``usage``, is a withdrawal rather than a
    pause: the buffer and the outbox are deleted, so nothing already recorded
    can be sent later.
    """
    from condor.telemetry import consent

    cm = get_config_manager()
    if not cm.is_admin(user.id):
        raise HTTPException(
            status_code=403,
            detail="Telemetry is an install-wide setting; only the admin can change it",
        )
    if level not in consent.LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"level must be one of {', '.join(consent.LEVELS)}",
        )
    if consent.env_overridden():
        raise HTTPException(
            status_code=409,
            detail="CONDOR_TELEMETRY is set in the environment and overrides this setting",
        )

    applied = consent.deny() if level == consent.OFF else consent.set_level(level)
    return {"level": applied, "consent": consent.state()}

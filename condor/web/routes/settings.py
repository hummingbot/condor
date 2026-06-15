from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from config_manager import ServerPermission, get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import (
    AddCredentialRequest,
    AddServerRequest,
    CredentialInfo,
    GatewayPullRequest,
    GatewayStartRequest,
    ServerInfo,
    UpdateServerRequest,
    WebUser,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


# ── Helpers ──


def _require_owner(cm, user_id: int, server_name: str):
    perm = cm.get_server_permission(user_id, server_name)
    if perm != ServerPermission.OWNER and not cm.is_admin(user_id):
        raise HTTPException(status_code=403, detail="Owner access required")


async def _get_client(cm, server_name: str):
    try:
        return await cm.get_client(server_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Cannot connect to server: {e}")


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

    results = []
    for (name, cfg), status in zip(accessible.items(), statuses):
        perm = cm.get_server_permission(user.id, name)
        online = status.get("status") == "online"
        results.append(ServerInfo(
            name=name,
            host=cfg.get("host", ""),
            port=cfg.get("port", 0),
            online=online,
            permission=perm.value if perm else "trader",
        ))

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
    return {"default": True, "name": name}


# ── Gateway ──


@router.get("/gateway/status")
async def gateway_status(
    server: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
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
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
    client = await _get_client(cm, server)
    try:
        # Parse "image:tag" format (e.g. "hummingbot/gateway:latest")
        parts = req.image.rsplit(":", 1)
        image_name = parts[0]
        tag = parts[1] if len(parts) > 1 else "latest"
        result = await client.docker._post("/docker/pull-image/", json={"image_name": image_name, "tag": tag})
        return {"pulled": True, "image": req.image, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gateway/pull-status")
async def gateway_pull_status(
    server: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
    client = await _get_client(cm, server)
    try:
        result = await client.docker.get_pull_status()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gateway/start")
async def gateway_start(
    req: GatewayStartRequest,
    server: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
    client = await _get_client(cm, server)
    try:
        result = await client.gateway.start({
            "image": req.image,
            "passphrase": req.passphrase,
            "port": req.port,
            "dev_mode": req.dev_mode,
        })
        return {"started": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gateway/stop")
async def gateway_stop(
    server: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
    client = await _get_client(cm, server)
    try:
        result = await client.gateway.stop()
        return {"stopped": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gateway/restart")
async def gateway_restart(
    server: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
    client = await _get_client(cm, server)
    try:
        result = await client.gateway.restart()
        return {"restarted": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gateway/logs")
async def gateway_logs(
    server: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
    client = await _get_client(cm, server)
    try:
        logs = await client.gateway.get_logs()
        return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Voice Preferences ──


@router.get("/voice")
async def get_voice_settings(user: WebUser = Depends(get_current_user)):
    """Get voice/transcription preferences for the current user."""
    cm = get_config_manager()
    prefs = cm.get_user_preferences(user.id)
    voice = prefs.get("voice", {
        "whisper_model": "small",
        "language": None,
        "auto_send": True,
    })
    from condor.preferences import WHISPER_MODELS, VOICE_LANGUAGES
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
    voice = prefs.get("voice", {
        "whisper_model": "small",
        "language": None,
        "auto_send": True,
    })
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
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
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
                    credentials.append({
                        "connector_name": item.get("connector_name", item.get("name", "")),
                        "connector_type": item.get("connector_type", item.get("type", "")),
                    })
        return {"credentials": credentials}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/connectors")
async def list_connectors(
    server: str = Query(...),
    type: str = Query(None),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    sds = get_server_data_service()
    raw = await sds.get_or_fetch(server, ServerDataType.ALL_CONNECTORS)
    if raw is None:
        raise HTTPException(status_code=502, detail="Cannot fetch connectors from server")

    # API returns plain strings — filter out testnet/gateway connectors
    names = [c for c in raw if isinstance(c, str) and "testnet" not in c.lower() and "sandbox" not in c.lower() and "/" not in c]
    if type:
        if type.lower() == "perpetual":
            names = [c for c in names if "perpetual" in c.lower()]
        else:
            names = [c for c in names if "perpetual" not in c.lower()]
    connectors = [{"name": c, "type": "perpetual" if "perpetual" in c.lower() else "spot"} for c in names]
    return {"connectors": connectors}


@router.get("/connectors/{name}/config-map")
async def connector_config_map(
    name: str,
    server: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
    client = await _get_client(cm, server)
    try:
        config_map = await client.connectors.get_config_map(name)
        return {"config_map": config_map}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/credentials")
async def add_credential(
    req: AddCredentialRequest,
    server: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
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
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/credentials/{connector}")
async def delete_credential(
    connector: str,
    server: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, server):
        raise HTTPException(status_code=403, detail="No access")
    client = await _get_client(cm, server)
    try:
        result = await client.accounts.delete_credential(
            account_name="master_account",
            connector_name=connector,
        )
        # Invalidate configured connectors cache
        from condor.server_data_service import ServerDataType, get_server_data_service
        get_server_data_service().invalidate(server, ServerDataType.CONNECTORS)
        return {"deleted": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

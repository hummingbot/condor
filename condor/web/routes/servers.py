from __future__ import annotations

from fastapi import APIRouter, Depends

from config_manager import get_config_manager
from condor.server_data_service import ServerDataType, get_server_data_service
from condor.web.auth import get_current_user
from condor.web.models import ServerInfo, WebUser

router = APIRouter(tags=["servers"])


@router.get("/servers", response_model=list[ServerInfo])
async def list_servers(user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    accessible = cm.list_accessible_servers(user.id)
    sds = get_server_data_service()

    results = []
    for name, cfg in accessible.items():
        perm = cm.get_server_permission(user.id, name)
        # Read from SDS cache — instant, no network calls
        status = sds.get(name, ServerDataType.SERVER_STATUS)
        online = status.get("status") == "online" if isinstance(status, dict) else False
        results.append(ServerInfo(
            name=name,
            host=cfg.get("host", ""),
            port=cfg.get("port", 0),
            online=online,
            permission=perm.value if perm else "trader",
        ))

    return sorted(results, key=lambda s: (not s.online, s.name))


@router.get("/servers/{name}/status")
async def server_status(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        return {"online": False, "error": "No access"}
    sds = get_server_data_service()
    status = sds.get(name, ServerDataType.SERVER_STATUS)
    if isinstance(status, dict):
        return status
    return {"status": "unknown"}

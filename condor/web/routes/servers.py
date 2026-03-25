from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from config_manager import get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import ServerInfo, WebUser

router = APIRouter(tags=["servers"])


@router.get("/servers", response_model=list[ServerInfo])
async def list_servers(user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    accessible = cm.list_accessible_servers(user.id)

    async def _build(name: str, cfg: dict) -> ServerInfo:
        perm = cm.get_server_permission(user.id, name)
        try:
            status = await cm.check_server_status(name)
            online = status.get("status") == "online"
        except Exception:
            online = False
        return ServerInfo(
            name=name,
            host=cfg.get("host", ""),
            port=cfg.get("port", 0),
            online=online,
            permission=perm.value if perm else "trader",
        )

    results = await asyncio.gather(
        *(_build(name, cfg) for name, cfg in accessible.items())
    )
    # Return online servers first
    return sorted(results, key=lambda s: (not s.online, s.name))


@router.get("/servers/{name}/status")
async def server_status(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        return {"online": False, "error": "No access"}
    try:
        status = await cm.check_server_status(name)
        return status
    except Exception as e:
        return {"online": False, "error": str(e)}

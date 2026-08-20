from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from condor.server_data_service import (
    ServerDataService,
    ServerDataType,
    get_server_data_service,
)
from condor.web.auth import get_current_user
from condor.web.models import ServerInfo, WebUser
from config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["servers"])


async def _resolve_online(sds: ServerDataService, name: str) -> bool:
    """Resolve one server's online flag, never raising.

    An unreachable server caches an error-only entry (value=None), so ``get()``
    misses on every request and the fetch pays a full connect timeout. Callers
    run these concurrently, so a single failure must not sink the whole batch.
    """
    try:
        status = sds.get(name, ServerDataType.SERVER_STATUS)
        if status is None:
            status = await sds.get_or_fetch(name, ServerDataType.SERVER_STATUS)
    except Exception as e:  # noqa: BLE001 - a broken server is offline, not a 500
        logger.debug("servers: status fetch failed for %s: %s", name, e)
        return False
    return status.get("status") == "online" if isinstance(status, dict) else False


@router.get("/servers", response_model=list[ServerInfo])
async def list_servers(user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    accessible = cm.list_accessible_servers(user.id)
    sds = get_server_data_service()

    # Per-server status resolution is independent and each miss can cost a full
    # connect timeout, so fan it out: the endpoint waits once, not N times.
    names = list(accessible)
    flags = await asyncio.gather(*(_resolve_online(sds, name) for name in names))

    # The selector seeds itself from this on a browser that has not picked a
    # server yet, so a default set in Telegram is the one the dashboard opens on.
    default_server = cm.get_chat_default_server(user.id)

    results = []
    for name, online in zip(names, flags):
        cfg = accessible[name]
        perm = cm.get_server_permission(user.id, name)
        results.append(
            ServerInfo(
                name=name,
                host=cfg.get("host", ""),
                port=cfg.get("port", 0),
                online=online,
                permission=perm.value if perm else "trader",
                is_default=name == default_server,
            )
        )

    return sorted(results, key=lambda s: (not s.online, s.name))


@router.get("/servers/{name}/status")
async def server_status(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    # Return 404 both when the server does not exist and when it exists but the
    # user lacks access, so the two cases are indistinguishable (no enumeration).
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=404, detail="Server not found")
    sds = get_server_data_service()
    try:
        status = await sds.get_or_fetch(name, ServerDataType.SERVER_STATUS)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    if isinstance(status, dict):
        return status
    return {"status": "unknown"}

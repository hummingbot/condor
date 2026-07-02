from __future__ import annotations

import logging
import threading
import time
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException

from condor.fetchers.bots import build_bots_page, extract_bots_list
from condor.web.auth import get_current_user
from condor.web.models import (
    AvailableControllersResponse,
    BotDetailResponse,
    BotInfo,
    BotsPageResponse,
    ControllerActionRequest,
    ControllerConfigDetail,
    ControllerConfigSummary,
    ControllerSourceResponse,
    DeployBotRequest,
    WebUser,
)
from config_manager import get_config_manager
from handlers.bots._shared import clean_config_for_save

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bots"])

# ── Transitional state store ──
# Tracks bots/controllers that have been sent a stop command but haven't
# finished shutting down yet. Auto-expires after TTL seconds.

_TRANSITIONAL_TTL = 300  # 5 minutes max

# { "server:bot_name" -> timestamp }
_stopping_bots: dict[str, float] = {}
# { "server:bot_name:controller_id" -> timestamp }
_stopping_controllers: dict[str, float] = {}
# Guards all access to the two dicts above. Reads are synchronous, so a
# threading.Lock (not asyncio.Lock) is used to prevent concurrent coroutines
# from iterating one dict while another mutates it.
_stopping_lock = threading.Lock()


def mark_bot_stopping(server: str, bot_name: str) -> None:
    with _stopping_lock:
        _stopping_bots[f"{server}:{bot_name}"] = time.monotonic()


def mark_controllers_stopping(
    server: str, bot_name: str, controller_ids: list[str]
) -> None:
    now = time.monotonic()
    with _stopping_lock:
        for cid in controller_ids:
            _stopping_controllers[f"{server}:{bot_name}:{cid}"] = now


def clear_bot_stopping(server: str, bot_name: str) -> None:
    with _stopping_lock:
        _stopping_bots.pop(f"{server}:{bot_name}", None)


def clear_controller_stopping(server: str, bot_name: str, controller_id: str) -> None:
    with _stopping_lock:
        _stopping_controllers.pop(f"{server}:{bot_name}:{controller_id}", None)


def get_stopping_bots(server: str) -> set[str]:
    """Return bot names currently in stopping state for a server."""
    now = time.monotonic()
    result = set()
    expired = []
    with _stopping_lock:
        for key, ts in list(_stopping_bots.items()):
            if now - ts > _TRANSITIONAL_TTL:
                expired.append(key)
                continue
            srv, bot = key.split(":", 1)
            if srv == server:
                result.add(bot)
        for key in expired:
            _stopping_bots.pop(key, None)
    return result


def get_stopping_controllers(server: str) -> set[str]:
    """Return 'bot_name:controller_id' keys currently in stopping state."""
    now = time.monotonic()
    result = set()
    expired = []
    with _stopping_lock:
        for key, ts in list(_stopping_controllers.items()):
            if now - ts > _TRANSITIONAL_TTL:
                expired.append(key)
                continue
            parts = key.split(":", 2)
            if len(parts) == 3 and parts[0] == server:
                result.add(f"{parts[1]}:{parts[2]}")
        for key in expired:
            _stopping_controllers.pop(key, None)
    return result


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return (
        obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    )


def _set(obj: Any, key: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[key] = value
    else:
        setattr(obj, key, value)


def overlay_stopping_state(server: str, controllers: list, bots: list) -> None:
    """Overlay transitional 'stopping' state onto bots and controllers.

    Shared by the REST path (Pydantic models) and the WS broadcast path
    (plain dicts); the `_get`/`_set` adapter handles both shapes.
    """
    stopping_bot_names = get_stopping_bots(server)
    stopping_ctrl_keys = get_stopping_controllers(server)

    if not stopping_bot_names and not stopping_ctrl_keys:
        return

    active_bot_names = set()
    for bot in bots:
        bot_name = _get(bot, "bot_name", "")
        active_bot_names.add(bot_name)
        if bot_name in stopping_bot_names:
            if _get(bot, "status") == "running":
                _set(bot, "status", "stopping")
            else:
                # Bot already reports non-running → the stop landed
                clear_bot_stopping(server, bot_name)

    # Clear stopping bots that disappeared from the response (fully stopped)
    for sbn in stopping_bot_names:
        if sbn not in active_bot_names:
            clear_bot_stopping(server, sbn)

    for ctrl in controllers:
        bot_name = _get(ctrl, "bot_name")
        controller_id = _get(ctrl, "controller_id")
        if f"{bot_name}:{controller_id}" in stopping_ctrl_keys:
            # If kill switch is already on, the stop landed → clear
            if (_get(ctrl, "config") or {}).get("manual_kill_switch") is True:
                clear_controller_stopping(server, bot_name, controller_id)
            else:
                _set(ctrl, "status", "stopping")


def _parse_bot(bot: dict) -> BotInfo:
    # Aggregate PnL from controller performance if available
    pnl = float(bot.get("pnl", 0))
    if not pnl and "performance" in bot:
        perf = bot["performance"]
        if isinstance(perf, dict):
            for ctrl in perf.values():
                if isinstance(ctrl, dict):
                    pnl += float(ctrl.get("realized_pnl_quote", 0))
                    pnl += float(ctrl.get("unrealized_pnl_quote", 0))

    return BotInfo(
        id=str(bot.get("id", bot.get("bot_name", ""))),
        name=bot.get("bot_name", bot.get("id", "")),
        status=bot.get("status", "unknown"),
        connector=bot.get("connector", ""),
        trading_pair=bot.get("trading_pair", ""),
        pnl=pnl,
        uptime=float(bot.get("uptime", 0)),
        controller_type=bot.get("controller_type", ""),
    )


def _extract_perf_snapshots(result: Any) -> list[dict]:
    """Normalize controller performance API response into a list of snapshot dicts."""
    if isinstance(result, list):
        return [s for s in result if isinstance(s, dict)]
    if isinstance(result, dict):
        data = result.get("data", result.get("snapshots", result.get("records", [])))
        if isinstance(data, list):
            return [s for s in data if isinstance(s, dict)]
        if isinstance(data, dict):
            out = []
            for key, val in data.items():
                if isinstance(val, dict):
                    val.setdefault("controller_id", key)
                    out.append(val)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            item.setdefault("controller_id", key)
                            out.append(item)
            return out
    return []


@router.get("/servers/{name}/bots", response_model=BotsPageResponse)
async def list_bots(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(
            name, ServerDataType.BOTS_STATUS
        )
    except Exception as e:
        logger.warning("Failed to fetch bots from '%s': %s", name, e)
        return BotsPageResponse(
            server_online=False,
            error_hint=f"Connection error: {e}",
        )

    if result is None:
        return BotsPageResponse(
            server_online=False,
            error_hint="Unable to reach server",
        )

    # Get client for enrichment calls
    try:
        client = await cm.get_client(name)
    except Exception:
        client = None

    bots_list = extract_bots_list(result)
    logger.info("Server '%s': found %d bot(s)", name, len(bots_list))

    # Pre-fetch controller configs, bot runs, AND latest controller performance concurrently
    ctrl_configs: dict[str, dict] = {}
    bot_runs: dict[str, str] = {}
    latest_perf: dict[str, dict] = {}  # keyed by controller_id

    if client is not None:
        import asyncio

        async def _fetch_ctrl_configs() -> dict[str, dict]:
            configs_map: dict[str, dict] = {}
            bot_names = [b.get("bot_name", "") for b in bots_list if b.get("bot_name")]
            if not bot_names:
                return configs_map

            async def _get_one(bn: str):
                try:
                    configs = await client.controllers.get_bot_controller_configs(bn)
                    if isinstance(configs, list):
                        for cfg in configs:
                            cid = cfg.get("id") or cfg.get("controller_id", "")
                            if cid:
                                configs_map[cid] = cfg
                            cname = cfg.get("controller_name", "")
                            if cname and cname != cid:
                                configs_map[cname] = cfg
                except Exception:
                    pass

            await asyncio.gather(*[_get_one(bn) for bn in bot_names])
            return configs_map

        async def _fetch_bot_runs() -> dict[str, str]:
            runs: dict[str, str] = {}
            try:
                runs_result = await client.bot_orchestration.get_bot_runs()
                if isinstance(runs_result, dict):
                    runs_data = runs_result.get("data", runs_result)
                    if isinstance(runs_data, dict):
                        for bot_name, run_info in runs_data.items():
                            if isinstance(run_info, dict):
                                deployed = run_info.get("deployed_at") or run_info.get(
                                    "created_at"
                                )
                                if deployed:
                                    runs[bot_name] = str(deployed)
                            elif isinstance(run_info, str):
                                runs[bot_name] = run_info
                    elif isinstance(runs_data, list):
                        for run in runs_data:
                            if isinstance(run, dict):
                                bn = run.get("bot_name", "")
                                deployed = run.get("deployed_at") or run.get(
                                    "created_at"
                                )
                                if bn and deployed:
                                    runs[bn] = str(deployed)
            except Exception:
                pass
            return runs

        async def _fetch_latest_perf() -> dict[str, dict]:
            """Fetch latest controller performance snapshots from DB."""
            perf_map: dict[str, dict] = {}
            try:
                perf_result = (
                    await client.bot_orchestration.get_latest_controller_performance()
                )
                snapshots = _extract_perf_snapshots(perf_result)
                for snap in snapshots:
                    cid = snap.get("controller_id", "")
                    if cid:
                        perf_map[cid] = snap
            except Exception:
                logger.debug(
                    "Latest controller performance not available for '%s'", name
                )
            return perf_map

        ctrl_configs, bot_runs, latest_perf = await asyncio.gather(
            _fetch_ctrl_configs(), _fetch_bot_runs(), _fetch_latest_perf()
        )

    page = build_bots_page(
        result,
        ctrl_configs=ctrl_configs,
        bot_runs=bot_runs,
        latest_perf=latest_perf,
    )

    # Overlay transitional "stopping" state
    overlay_stopping_state(name, page["controllers"], page["bots"])

    return BotsPageResponse(**page)


@router.get("/servers/{name}/bots/{bot_id}")
async def get_bot(name: str, bot_id: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    import asyncio

    client = await cm.get_client(name)

    try:
        result = await client.bot_orchestration.get_bot_status(bot_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not isinstance(result, dict):
        raise HTTPException(status_code=404, detail="Bot not found")

    # Extract nested data from the status response
    data = result.get("data", result)
    if not isinstance(data, dict):
        data = result

    # Extract performance from status response (keyed by controller_id)
    performance = data.get("performance", {})
    if not isinstance(performance, dict):
        performance = {}

    # Flatten controller performance into a single merged dict for display
    flat_perf: dict = {}
    for ctrl_name, ctrl_info in performance.items():
        if isinstance(ctrl_info, dict):
            perf = ctrl_info.get("performance", {})
            if isinstance(perf, dict):
                flat_perf = perf
                break  # Single-controller bot: use first controller's performance

    # Fetch controller config concurrently
    config: dict = {}
    try:
        configs = await client.controllers.get_bot_controller_configs(bot_id)
        if isinstance(configs, list) and configs:
            config = configs[0] if isinstance(configs[0], dict) else {}
    except Exception:
        pass

    bot = _parse_bot(data)
    return BotDetailResponse(bot=bot, config=config, performance=flat_perf)


@router.get(
    "/servers/{name}/controllers/configs",
    response_model=AvailableControllersResponse,
)
async def list_controller_configs(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    import asyncio

    client = await cm.get_client(name)

    # Fetch controller types and saved configs in parallel
    async def _get_types():
        try:
            r = await client.controllers.list_controllers()
            return (
                {k: v for k, v in r.items() if isinstance(v, list)}
                if isinstance(r, dict)
                else {}
            )
        except Exception as e:
            logger.warning("Failed to list controller types from '%s': %s", name, e)
            return {}

    async def _get_configs():
        try:
            r = await client.controllers.list_controller_configs()
            if not isinstance(r, list):
                return []
            return [
                ControllerConfigSummary(
                    id=str(cfg.get("config_base_name") or cfg.get("id", "")),
                    controller_name=cfg.get("controller_name", ""),
                    controller_type=cfg.get("controller_type", ""),
                    connector_name=cfg.get("connector_name", ""),
                    trading_pair=cfg.get("trading_pair", ""),
                )
                for cfg in r
                if isinstance(cfg, dict)
            ]
        except Exception as e:
            logger.warning("Failed to list controller configs from '%s': %s", name, e)
            return []

    controller_types, configs = await asyncio.gather(_get_types(), _get_configs())

    return AvailableControllersResponse(
        configs=configs,
        controller_types=controller_types,
    )


@router.get(
    "/servers/{name}/controllers/configs/{config_id}",
    response_model=ControllerConfigDetail,
)
async def get_controller_config(
    name: str, config_id: str, user: WebUser = Depends(get_current_user)
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    try:
        result = await client.controllers.get_controller_config(config_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not isinstance(result, dict):
        raise HTTPException(status_code=404, detail="Config not found")

    return ControllerConfigDetail(
        id=str(result.get("config_base_name") or result.get("id", config_id)),
        controller_name=result.get("controller_name", ""),
        controller_type=result.get("controller_type", ""),
        config=result,
    )


@router.put("/servers/{name}/controllers/configs/{config_id}")
async def update_controller_config(
    name: str,
    config_id: str,
    body: dict[str, Any],
    user: WebUser = Depends(get_current_user),
):
    """Update a saved controller config's parameters.

    Accepts either:
      - { "yaml_content": "..." } — parse YAML to dict, save
      - { ... } (raw dict) — existing behavior preserved
    """
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)

    # If yaml_content is provided, parse it as the full config
    yaml_content = body.pop("yaml_content", None)
    if yaml_content is not None:
        try:
            parsed = yaml.safe_load(yaml_content)
            if not isinstance(parsed, dict):
                raise HTTPException(
                    status_code=400, detail="YAML must parse to a mapping"
                )
            body = parsed
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    is_full_replace = yaml_content is not None

    try:
        # Fetch existing config so we preserve controller_name/type/id
        existing = await client.controllers.get_controller_config(config_id)
        if not isinstance(existing, dict):
            raise HTTPException(status_code=404, detail="Config not found")

        if is_full_replace:
            # Full replacement: use parsed YAML as-is, only preserve identity fields
            merged = {**body}
            for key in ("id", "controller_name", "controller_type"):
                if key in existing and key not in merged:
                    merged[key] = existing[key]
        else:
            # Partial update: merge user edits into existing config
            merged = {**existing, **body}

        merged["id"] = config_id  # ensure id stays consistent
        # Strip internal fields like _config_name that cause Pydantic validation errors
        merged = {k: v for k, v in merged.items() if not k.startswith("_")}

        result = await client.controllers.create_or_update_controller_config(
            config_id, merged
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"updated": True, "config_id": config_id, "result": result}


@router.get(
    "/servers/{name}/controllers/{controller_type}/{controller_name}/source",
    response_model=ControllerSourceResponse,
)
async def get_controller_source(
    name: str,
    controller_type: str,
    controller_name: str,
    user: WebUser = Depends(get_current_user),
):
    """Fetch the Python source of a controller."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    try:
        result = await client.controllers.get_controller(
            controller_type, controller_name
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if isinstance(result, str):
        source = result
    elif isinstance(result, dict):
        source = (
            result.get("content")
            or result.get("source")
            or result.get("code")
            or str(result)
        )
    else:
        raise HTTPException(status_code=404, detail="Controller not found")

    return ControllerSourceResponse(
        controller_name=controller_name,
        controller_type=controller_type,
        source=source,
    )


@router.put(
    "/servers/{name}/controllers/{controller_type}/{controller_name}/source",
)
async def update_controller_source(
    name: str,
    controller_type: str,
    controller_name: str,
    body: dict[str, Any],
    user: WebUser = Depends(get_current_user),
):
    """Update the Python source of a controller."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    source = body.get("source")
    if not source or not isinstance(source, str):
        raise HTTPException(status_code=400, detail="Missing 'source' string")

    client = await cm.get_client(name)
    try:
        result = await client.controllers.create_or_update_controller(
            controller_type, controller_name, {"content": source}
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"updated": True, "result": result}


@router.get(
    "/servers/{name}/controllers/{controller_type}/{controller_name}/template",
)
async def get_controller_config_template(
    name: str,
    controller_type: str,
    controller_name: str,
    user: WebUser = Depends(get_current_user),
):
    """Fetch the config template/schema for a controller."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    try:
        result = await client.controllers.get_controller_config_template(
            controller_type, controller_name
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not result:
        raise HTTPException(status_code=404, detail="Template not found")

    # Normalize: could be a dict or list of field dicts
    if isinstance(result, dict):
        return result
    return {"fields": result}


@router.post("/servers/{name}/controllers/configs")
async def create_controller_config(
    name: str,
    body: dict[str, Any],
    user: WebUser = Depends(get_current_user),
):
    """Create or update a controller config."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    config_id = body.get("id")
    if not config_id:
        raise HTTPException(status_code=400, detail="Missing 'id' field")

    # If yaml_content is provided, parse it
    yaml_content = body.pop("yaml_content", None)
    if yaml_content is not None:
        try:
            parsed = yaml.safe_load(yaml_content)
            if not isinstance(parsed, dict):
                raise HTTPException(
                    status_code=400, detail="YAML must parse to a mapping"
                )
            body = parsed
            body["id"] = config_id
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    client = await cm.get_client(name)
    try:
        # Strip internal fields like _config_name and normalize stringified enum
        # values (e.g. "PositionMode.ONEWAY" -> "ONEWAY") before saving.
        clean_body = clean_config_for_save(body)
        result = await client.controllers.create_or_update_controller_config(
            config_id, clean_body
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"created": True, "config_id": config_id, "result": result}


@router.delete("/servers/{name}/controllers/configs/{config_id}")
async def delete_controller_config(
    name: str,
    config_id: str,
    user: WebUser = Depends(get_current_user),
):
    """Delete a saved controller config."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    try:
        result = await client.controllers.delete_controller_config(config_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"deleted": True, "config_id": config_id, "result": result}


@router.delete("/servers/{name}/controllers/{controller_type}/{controller_name}")
async def delete_controller(
    name: str,
    controller_type: str,
    controller_name: str,
    user: WebUser = Depends(get_current_user),
):
    """Delete a controller."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    try:
        result = await client.controllers.delete_controller(
            controller_type, controller_name
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "deleted": True,
        "controller_type": controller_type,
        "controller_name": controller_name,
        "result": result,
    }


@router.post("/servers/{name}/bots/deploy")
async def deploy_bot_endpoint(
    name: str, body: DeployBotRequest, user: WebUser = Depends(get_current_user)
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)

    from mcp_servers.hummingbot_api.tools.controllers import deploy_bot

    try:
        result = await deploy_bot(
            client=client,
            bot_name=body.bot_name,
            controllers_config=body.controllers_config,
            account_name=body.account_name,
            image=body.image,
            max_global_drawdown_quote=body.max_global_drawdown_quote,
            max_controller_drawdown_quote=body.max_controller_drawdown_quote,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return result


@router.post("/servers/{name}/bots/{bot_name}/stop")
async def stop_bot_endpoint(
    name: str, bot_name: str, user: WebUser = Depends(get_current_user)
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    # Mark as stopping immediately so UI reflects it
    mark_bot_stopping(name, bot_name)

    client = await cm.get_client(name)

    from mcp_servers.hummingbot_api.tools.bot_management import manage_bot_execution

    try:
        result = await manage_bot_execution(
            client=client,
            bot_name=bot_name,
            action="stop_bot",
        )
    except Exception as e:
        clear_bot_stopping(name, bot_name)
        raise HTTPException(status_code=502, detail=str(e))

    return result


@router.post("/servers/{name}/bots/{bot_name}/controllers/stop")
async def stop_controllers_endpoint(
    name: str,
    bot_name: str,
    body: ControllerActionRequest,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    # Mark controllers as stopping immediately
    mark_controllers_stopping(name, bot_name, body.controller_names)

    client = await cm.get_client(name)

    from mcp_servers.hummingbot_api.tools.bot_management import manage_bot_execution

    try:
        result = await manage_bot_execution(
            client=client,
            bot_name=bot_name,
            action="stop_controllers",
            controller_names=body.controller_names,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return result


@router.post("/servers/{name}/bots/{bot_name}/controllers/start")
async def start_controllers_endpoint(
    name: str,
    bot_name: str,
    body: ControllerActionRequest,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)

    from mcp_servers.hummingbot_api.tools.bot_management import manage_bot_execution

    try:
        result = await manage_bot_execution(
            client=client,
            bot_name=bot_name,
            action="start_controllers",
            controller_names=body.controller_names,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return result


@router.put("/servers/{name}/bots/{bot_name}/controllers/{config_id}/config")
async def update_bot_controller_config_endpoint(
    name: str,
    bot_name: str,
    config_id: str,
    body: dict[str, Any],
    user: WebUser = Depends(get_current_user),
):
    """Update a controller config inside a running bot in real-time."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)

    try:
        # Fetch current bot controller config to merge partial updates
        current_configs = await client.controllers.get_bot_controller_configs(bot_name)
        existing = next((c for c in current_configs if c.get("id") == config_id), None)
        if not existing:
            raise HTTPException(
                status_code=404,
                detail=f"Controller '{config_id}' not found in bot '{bot_name}'",
            )

        merged = {**existing, **body}
        merged["id"] = config_id
        # Strip internal fields like _config_name that cause Pydantic validation errors
        merged = {k: v for k, v in merged.items() if not k.startswith("_")}

        result = await client.controllers.update_bot_controller_config(
            bot_name, config_id, merged
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "updated": True,
        "config_id": config_id,
        "bot_name": bot_name,
        "result": result,
    }

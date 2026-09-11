from __future__ import annotations

import logging
import threading
import time
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException

from condor.controller_configs import clean_config_for_save
from condor.fetchers.bots import (
    BotsEnrichment,
    build_bots_page,
    extract_bots_list,
    invalidate_ctrl_configs,
)
from condor.server_data_service import ServerDataType, get_server_data_service
from condor.web.auth import require_server_access
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
from condor.web.routes._deeds import record_ui_deed
from condor.web.routes._errors import upstream_error
from config_manager import get_config_manager

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


async def enriched_bots_page(name: str, raw_status: Any) -> dict:
    """Build the bots page for a server from raw status plus cached enrichment.

    The one place the two delivery paths meet: the REST route and every WS
    ``bots:<server>`` frame render through this, so a frame carries the same
    ``config``, ``deployed_at``, ``controller_id`` and connector/trading pair as
    the REST body for the same raw payload — no client-side repair needed.

    The enrichment is read through ServerDataService, which holds it for a
    minute: the 5s bots frame costs an extra Hummingbot round-trip only when
    that cached answer has gone stale.
    """
    try:
        enrichment = await get_server_data_service().get_or_fetch(
            name, ServerDataType.BOTS_ENRICHMENT
        )
    except Exception as e:
        logger.debug("Bots enrichment unavailable for '%s': %s", name, e)
        enrichment = None

    ctrl_configs, bot_runs, latest_perf = enrichment or BotsEnrichment.empty()
    return build_bots_page(
        raw_status,
        ctrl_configs=ctrl_configs,
        bot_runs=bot_runs,
        latest_perf=latest_perf,
    )


@router.get("/servers/{name}/bots", response_model=BotsPageResponse)
async def list_bots(name: str, user: WebUser = Depends(require_server_access)):
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

    bots_list = extract_bots_list(result)
    logger.info("Server '%s': found %d bot(s)", name, len(bots_list))

    page = await enriched_bots_page(name, result)

    # Overlay transitional "stopping" state
    overlay_stopping_state(name, page["controllers"], page["bots"])

    return BotsPageResponse(**page)


@router.get("/servers/{name}/bots/{bot_id}")
async def get_bot(
    name: str, bot_id: str, user: WebUser = Depends(require_server_access)
):
    cm = get_config_manager()

    import asyncio

    client = await cm.get_client(name)

    try:
        result = await client.bot_orchestration.get_bot_status(bot_id)
    except Exception as e:
        logger.exception("Failed to fetch status for bot '%s' on '%s'", bot_id, name)
        raise upstream_error("Failed to fetch bot status", e)

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
async def list_controller_configs(
    name: str, user: WebUser = Depends(require_server_access)
):
    cm = get_config_manager()

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
    name: str, config_id: str, user: WebUser = Depends(require_server_access)
):
    cm = get_config_manager()

    client = await cm.get_client(name)
    try:
        result = await client.controllers.get_controller_config(config_id)
    except Exception as e:
        logger.exception(
            "Failed to fetch controller config '%s' from '%s'", config_id, name
        )
        raise upstream_error("Failed to fetch controller config", e)

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
    user: WebUser = Depends(require_server_access),
):
    """Update a saved controller config's parameters.

    Accepts either:
      - { "yaml_content": "..." } — parse YAML to dict, save
      - { ... } (raw dict) — existing behavior preserved
    """
    cm = get_config_manager()

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
        logger.exception(
            "Failed to update controller config '%s' on '%s'", config_id, name
        )
        raise upstream_error("Failed to save controller config", e)

    # A saved config is edited by id, with no bot attached, so drop this
    # server's whole controller-config cache rather than guess the holder.
    invalidate_ctrl_configs(client)

    record_ui_deed(
        user,
        verb="manage_controllers:upsert",
        summary=f"Update controller config '{config_id}' on {name}",
    )
    return {"updated": True, "config_id": config_id, "result": result}


@router.get(
    "/servers/{name}/controllers/{controller_type}/{controller_name}/source",
    response_model=ControllerSourceResponse,
)
async def get_controller_source(
    name: str,
    controller_type: str,
    controller_name: str,
    user: WebUser = Depends(require_server_access),
):
    """Fetch the Python source of a controller."""
    cm = get_config_manager()

    client = await cm.get_client(name)
    try:
        result = await client.controllers.get_controller(
            controller_type, controller_name
        )
    except Exception as e:
        logger.exception(
            "Failed to fetch controller source '%s/%s' from '%s'",
            controller_type,
            controller_name,
            name,
        )
        raise upstream_error("Failed to fetch controller source", e)

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
    user: WebUser = Depends(require_server_access),
):
    """Update the Python source of a controller."""
    cm = get_config_manager()

    source = body.get("source")
    if not source or not isinstance(source, str):
        raise HTTPException(status_code=400, detail="Missing 'source' string")

    client = await cm.get_client(name)
    try:
        result = await client.controllers.create_or_update_controller(
            controller_type, controller_name, {"content": source}
        )
    except Exception as e:
        logger.exception(
            "Failed to update controller source '%s/%s' on '%s'",
            controller_type,
            controller_name,
            name,
        )
        raise upstream_error("Failed to save controller source", e)

    record_ui_deed(
        user,
        verb="manage_controllers:upsert",
        summary=(
            f"Update controller source '{controller_type}/{controller_name}' on {name}"
        ),
    )
    return {"updated": True, "result": result}


@router.get(
    "/servers/{name}/controllers/{controller_type}/{controller_name}/template",
)
async def get_controller_config_template(
    name: str,
    controller_type: str,
    controller_name: str,
    user: WebUser = Depends(require_server_access),
):
    """Fetch the config template/schema for a controller."""
    cm = get_config_manager()

    client = await cm.get_client(name)
    try:
        result = await client.controllers.get_controller_config_template(
            controller_type, controller_name
        )
    except Exception as e:
        logger.exception(
            "Failed to fetch config template for '%s/%s' from '%s'",
            controller_type,
            controller_name,
            name,
        )
        raise upstream_error("Failed to fetch controller config template", e)

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
    user: WebUser = Depends(require_server_access),
):
    """Create or update a controller config."""
    cm = get_config_manager()

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
        logger.exception(
            "Failed to create controller config '%s' on '%s'", config_id, name
        )
        raise upstream_error("Failed to save controller config", e)

    record_ui_deed(
        user,
        verb="manage_controllers:upsert",
        summary=f"Save controller config '{config_id}' on {name}",
    )
    return {"created": True, "config_id": config_id, "result": result}


@router.delete("/servers/{name}/controllers/configs/{config_id}")
async def delete_controller_config(
    name: str,
    config_id: str,
    user: WebUser = Depends(require_server_access),
):
    """Delete a saved controller config."""
    cm = get_config_manager()

    client = await cm.get_client(name)
    try:
        result = await client.controllers.delete_controller_config(config_id)
    except Exception as e:
        logger.exception(
            "Failed to delete controller config '%s' from '%s'", config_id, name
        )
        raise upstream_error("Failed to delete controller config", e)

    record_ui_deed(
        user,
        verb="manage_controllers:delete",
        summary=f"Delete controller config '{config_id}' on {name}",
    )
    return {"deleted": True, "config_id": config_id, "result": result}


@router.delete("/servers/{name}/controllers/{controller_type}/{controller_name}")
async def delete_controller(
    name: str,
    controller_type: str,
    controller_name: str,
    user: WebUser = Depends(require_server_access),
):
    """Delete a controller."""
    cm = get_config_manager()

    client = await cm.get_client(name)
    try:
        result = await client.controllers.delete_controller(
            controller_type, controller_name
        )
    except Exception as e:
        logger.exception(
            "Failed to delete controller '%s/%s' from '%s'",
            controller_type,
            controller_name,
            name,
        )
        raise upstream_error("Failed to delete controller", e)

    record_ui_deed(
        user,
        verb="manage_controllers:delete",
        summary=f"Delete controller '{controller_type}/{controller_name}' on {name}",
    )
    return {
        "deleted": True,
        "controller_type": controller_type,
        "controller_name": controller_name,
        "result": result,
    }


@router.post("/servers/{name}/bots/deploy")
async def deploy_bot_endpoint(
    name: str, body: DeployBotRequest, user: WebUser = Depends(require_server_access)
):
    cm = get_config_manager()

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
        logger.exception("Failed to deploy bot '%s' on '%s'", body.bot_name, name)
        raise upstream_error("Failed to deploy bot", e)

    # The one deed that also claims ownership: the summary names the bot and the
    # subject is what ``owned_bots.json`` is keyed on, so a bot deployed from
    # ``/bots`` is as attributable as one a tick deployed (FEAT-105).
    record_ui_deed(
        user,
        verb="manage_bots:deploy",
        summary=(
            f"Deploy bot '{body.bot_name}' with controllers {body.controllers_config}"
        ),
        subject=body.bot_name,
    )
    return result


@router.post("/servers/{name}/bots/{bot_name}/stop")
async def stop_bot_endpoint(
    name: str, bot_name: str, user: WebUser = Depends(require_server_access)
):
    cm = get_config_manager()

    # Mark as stopping immediately so UI reflects it
    mark_bot_stopping(name, bot_name)

    from mcp_servers.hummingbot_api.tools.bot_management import manage_bot_execution

    try:
        client = await cm.get_client(name)
        result = await manage_bot_execution(
            client=client,
            bot_name=bot_name,
            action="stop_bot",
        )
    except Exception as e:
        clear_bot_stopping(name, bot_name)
        logger.exception("Failed to stop bot '%s' on '%s'", bot_name, name)
        raise upstream_error("Failed to stop bot", e)

    record_ui_deed(
        user, verb="manage_bots:stop_bot", summary=f"Bot '{bot_name}': stop_bot"
    )
    return result


@router.post("/servers/{name}/bots/{bot_name}/controllers/stop")
async def stop_controllers_endpoint(
    name: str,
    bot_name: str,
    body: ControllerActionRequest,
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    # Mark controllers as stopping immediately
    mark_controllers_stopping(name, bot_name, body.controller_names)

    from mcp_servers.hummingbot_api.tools.bot_management import manage_bot_execution

    try:
        client = await cm.get_client(name)
        result = await manage_bot_execution(
            client=client,
            bot_name=bot_name,
            action="stop_controllers",
            controller_names=body.controller_names,
        )
    except Exception as e:
        # Nothing was stopped, so the kill switch will never flip and the overlay
        # would show these controllers as "stopping" until the TTL expires —
        # blocking a retry from the UI. Mirror stop_bot_endpoint and undo the mark.
        for controller_id in body.controller_names:
            clear_controller_stopping(name, bot_name, controller_id)
        logger.exception(
            "Failed to stop controllers on bot '%s' of '%s'", bot_name, name
        )
        raise upstream_error("Failed to stop controllers", e)

    # manage_bot_execution can return 200 with a partial failure (some
    # controllers' config writes rejected) — it only raises when *nothing*
    # succeeded. Those failed ids never flip manual_kill_switch, so without
    # this the overlay would keep painting them "stopping" for the full TTL,
    # locking the retry control out from under the operator.
    for controller_id in result.get("failed", {}):
        clear_controller_stopping(name, bot_name, controller_id)

    record_ui_deed(
        user,
        verb="manage_bots:stop_controllers",
        summary=f"Bot '{bot_name}': stop_controllers {body.controller_names}",
    )
    return result


@router.post("/servers/{name}/bots/{bot_name}/controllers/start")
async def start_controllers_endpoint(
    name: str,
    bot_name: str,
    body: ControllerActionRequest,
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

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
        logger.exception(
            "Failed to start controllers on bot '%s' of '%s'", bot_name, name
        )
        raise upstream_error("Failed to start controllers", e)

    record_ui_deed(
        user,
        verb="manage_bots:start_controllers",
        summary=f"Bot '{bot_name}': start_controllers {body.controller_names}",
    )
    return result


@router.put("/servers/{name}/bots/{bot_name}/controllers/{config_id}/config")
async def update_bot_controller_config_endpoint(
    name: str,
    bot_name: str,
    config_id: str,
    body: dict[str, Any],
    user: WebUser = Depends(require_server_access),
):
    """Update a controller config inside a running bot in real-time."""
    cm = get_config_manager()

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
        logger.exception(
            "Failed to update controller config '%s' on bot '%s' of '%s'",
            config_id,
            bot_name,
            name,
        )
        raise upstream_error("Failed to save controller config", e)

    # The edit lands in this bot's live configs: re-fetch them on the next
    # bots page instead of serving the pre-edit copy until the TTL expires.
    invalidate_ctrl_configs(client, bot_name)

    record_ui_deed(
        user,
        verb="manage_bots:update_config",
        summary=f"Update config '{config_id}' on bot '{bot_name}'",
    )
    return {
        "updated": True,
        "config_id": config_id,
        "bot_name": bot_name,
        "result": result,
    }

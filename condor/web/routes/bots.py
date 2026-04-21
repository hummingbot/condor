from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from config_manager import get_config_manager
from condor.web.auth import get_current_user
import yaml

from condor.web.models import (
    AvailableControllersResponse,
    BotDetailResponse,
    BotInfo,
    BotSummary,
    BotsPageResponse,
    ControllerConfigDetail,
    ControllerConfigSummary,
    ControllerInfo,
    ControllerSourceResponse,
    DeployBotRequest,
    WebUser,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bots"])


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


def _extract_bots_list(result: Any) -> list[dict]:
    """Normalize the various API response formats into a list of bot dicts."""
    if result is None:
        logger.warning("Bot status API returned None")
        return []
    if isinstance(result, str):
        logger.warning("Bot status API returned string (possibly HTML error page): %s", result[:200])
        return []
    if isinstance(result, dict):
        if result.get("status") == "error":
            logger.warning("Bot status API returned error: %s", result.get("message", result))
            return []
        data = result.get("data", {})
        if isinstance(data, dict):
            return [{"bot_name": k, **v} for k, v in data.items() if isinstance(v, dict)]
        elif isinstance(data, list):
            return [b for b in data if isinstance(b, dict)]
        return []
    elif isinstance(result, list):
        return [b for b in result if isinstance(b, dict)]
    logger.warning("Bot status API returned unexpected type: %s", type(result).__name__)
    return []


@router.get("/servers/{name}/bots", response_model=BotsPageResponse)
async def list_bots(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(name, ServerDataType.BOTS_STATUS)
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

    # Still need a client for bot_runs (not cached)
    try:
        client = await cm.get_client(name)
    except Exception:
        client = None

    bots_list = _extract_bots_list(result)
    logger.info("Server '%s': found %d bot(s)", name, len(bots_list))

    # Pre-fetch controller configs keyed by controller id
    ctrl_configs: dict[str, dict] = {}
    if client is not None:
        for bot_data in bots_list:
            bn = bot_data.get("bot_name", "")
            if not bn:
                continue
            try:
                configs = await client.controllers.get_bot_controller_configs(bn)
                if isinstance(configs, list):
                    for cfg in configs:
                        cid = cfg.get("id") or cfg.get("controller_id", "")
                        if cid:
                            ctrl_configs[cid] = cfg
            except Exception:
                pass

    # Try to get bot runs for uptime/deployed_at info
    bot_runs: dict[str, str] = {}
    try:
        if client is None:
            raise ValueError("No client")
        runs_result = await client.bot_orchestration.get_bot_runs()
        if isinstance(runs_result, dict):
            runs_data = runs_result.get("data", runs_result)
            if isinstance(runs_data, dict):
                for bot_name, run_info in runs_data.items():
                    if isinstance(run_info, dict):
                        deployed = run_info.get("deployed_at") or run_info.get("created_at")
                        if deployed:
                            bot_runs[bot_name] = str(deployed)
                    elif isinstance(run_info, str):
                        bot_runs[bot_name] = run_info
            elif isinstance(runs_data, list):
                for run in runs_data:
                    if isinstance(run, dict):
                        bn = run.get("bot_name", "")
                        deployed = run.get("deployed_at") or run.get("created_at")
                        if bn and deployed:
                            bot_runs[bn] = str(deployed)
    except Exception:
        pass

    controllers: list[ControllerInfo] = []
    bots: list[BotSummary] = []
    total_pnl = 0.0
    total_volume = 0.0

    for bot_data in bots_list:
        bot_name = bot_data.get("bot_name", "")
        bot_status = bot_data.get("status", "unknown")
        performance = bot_data.get("performance", {})
        error_logs = bot_data.get("error_logs", [])

        num_controllers = 0

        if isinstance(performance, dict):
            for ctrl_name, ctrl_info in performance.items():
                if not isinstance(ctrl_info, dict):
                    continue

                num_controllers += 1
                ctrl_status = ctrl_info.get("status", "running")
                ctrl_perf = ctrl_info.get("performance", {})

                if not isinstance(ctrl_perf, dict):
                    ctrl_perf = {}

                realized = float(ctrl_perf.get("realized_pnl_quote", 0) or 0)
                unrealized = float(ctrl_perf.get("unrealized_pnl_quote", 0) or 0)
                global_pnl = realized + unrealized
                global_pnl_pct = float(ctrl_perf.get("global_pnl_pct", 0) or 0)
                volume = float(ctrl_perf.get("volume_traded", 0) or 0)
                close_types = ctrl_perf.get("close_type_counts", {})
                if not isinstance(close_types, dict):
                    close_types = {}
                positions = ctrl_perf.get("positions_summary", [])
                if not isinstance(positions, list):
                    positions = []

                # Get config from pre-fetched configs
                ctrl_config = ctrl_configs.get(ctrl_name, {})

                # Primary: config dict (correct keys)
                connector = ctrl_config.get("connector_name", "")
                trading_pair = ctrl_config.get("trading_pair", "")

                # Fallback: try to parse connector/pair from controller name
                # e.g. "binance_perpetual_SOL-USDT_pmm_simple"
                if not connector or not trading_pair:
                    parts = ctrl_name.split("_")
                    for i, part in enumerate(parts):
                        if "-" in part and part[0].isupper():
                            # Looks like a trading pair (e.g. SOL-USDT)
                            if not trading_pair:
                                trading_pair = part
                            if not connector and i > 0:
                                connector = "_".join(parts[:i])
                            break

                total_pnl += global_pnl
                total_volume += volume

                controllers.append(
                    ControllerInfo(
                        controller_name=ctrl_name,
                        bot_name=bot_name,
                        status=ctrl_status,
                        connector=connector,
                        trading_pair=trading_pair,
                        realized_pnl_quote=realized,
                        unrealized_pnl_quote=unrealized,
                        global_pnl_quote=global_pnl,
                        global_pnl_pct=global_pnl_pct,
                        volume_traded=volume,
                        close_type_counts=close_types,
                        positions_summary=positions,
                        deployed_at=bot_runs.get(bot_name),
                        config=ctrl_config,
                    )
                )

        bots.append(
            BotSummary(
                bot_name=bot_name,
                status=bot_status,
                num_controllers=num_controllers,
                error_count=len(error_logs) if isinstance(error_logs, list) else 0,
                deployed_at=bot_runs.get(bot_name),
            )
        )

    return BotsPageResponse(
        controllers=controllers,
        bots=bots,
        total_pnl=total_pnl,
        total_volume=total_volume,
    )


@router.get("/servers/{name}/bots/{bot_id}")
async def get_bot(name: str, bot_id: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    try:
        result = await client.bot_orchestration.get_bot_status(bot_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not isinstance(result, dict):
        raise HTTPException(status_code=404, detail="Bot not found")

    bot = _parse_bot(result)

    config: dict[str, Any] = {}
    try:
        config = await client.bot_orchestration.get_bot_config(bot_id)
        if not isinstance(config, dict):
            config = {}
    except Exception:
        pass

    performance: dict[str, Any] = {}
    try:
        perf = await client.bot_orchestration.get_bot_performance(bot_id)
        if isinstance(perf, dict):
            performance = perf
    except Exception:
        pass

    return BotDetailResponse(bot=bot, config=config, performance=performance)


@router.get(
    "/servers/{name}/controllers/configs",
    response_model=AvailableControllersResponse,
)
async def list_controller_configs(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)

    # Fetch controller types and saved configs in parallel
    controller_types: dict[str, list[str]] = {}
    try:
        types_result = await client.controllers.list_controllers()
        if isinstance(types_result, dict):
            controller_types = {
                k: v for k, v in types_result.items() if isinstance(v, list)
            }
    except Exception as e:
        logger.warning("Failed to list controller types from '%s': %s", name, e)

    configs: list[ControllerConfigSummary] = []
    try:
        configs_result = await client.controllers.list_controller_configs()
        if isinstance(configs_result, list):
            for cfg in configs_result:
                if not isinstance(cfg, dict):
                    continue
                configs.append(
                    ControllerConfigSummary(
                        id=str(cfg.get("id", "")),
                        controller_name=cfg.get("controller_name", ""),
                        controller_type=cfg.get("controller_type", ""),
                        connector_name=cfg.get("connector_name", ""),
                        trading_pair=cfg.get("trading_pair", ""),
                    )
                )
    except Exception as e:
        logger.warning("Failed to list controller configs from '%s': %s", name, e)

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
        id=str(result.get("id", config_id)),
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

    try:
        # Fetch existing config so we preserve controller_name/type/id
        existing = await client.controllers.get_controller_config(config_id)
        if not isinstance(existing, dict):
            raise HTTPException(status_code=404, detail="Config not found")

        # Merge user edits into existing config
        merged = {**existing, **body}
        merged["id"] = config_id  # ensure id stays consistent

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
        source = result.get("content") or result.get("source") or result.get("code") or str(result)
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
            controller_type, controller_name, source
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"updated": True, "result": result}


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

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from config_manager import get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import (
    BotDetailResponse,
    BotInfo,
    BotSummary,
    BotsPageResponse,
    ControllerInfo,
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

                # Extract connector/pair from controller config or performance
                ctrl_config = ctrl_info.get("config", {})
                if not isinstance(ctrl_config, dict):
                    ctrl_config = {}
                connector = (
                    ctrl_perf.get("connector", "")
                    or ctrl_info.get("connector", "")
                    or ctrl_perf.get("connector_name", "")
                    or ctrl_config.get("connector_name", "")
                    or ctrl_config.get("connector", "")
                    or ""
                )
                trading_pair = (
                    ctrl_perf.get("trading_pair", "")
                    or ctrl_info.get("trading_pair", "")
                    or ctrl_config.get("trading_pair", "")
                    or ""
                )

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

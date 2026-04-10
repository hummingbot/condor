from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from config_manager import get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import WebUser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["positions"])


def _normalize_position(pos: dict, source: str, source_name: str) -> dict:
    """Normalize a position dict to a common shape with source info."""
    return {
        "connector_name": pos.get("connector_name") or pos.get("connector") or "",
        "trading_pair": pos.get("trading_pair") or "",
        "position_side": pos.get("position_side") or pos.get("side") or "",
        "amount": pos.get("net_amount_base") or pos.get("amount") or 0,
        "entry_price": pos.get("buy_breakeven_price") or pos.get("entry_price") or 0,
        "current_price": pos.get("current_price") or 0,
        "unrealized_pnl": pos.get("unrealized_pnl_quote") or pos.get("unrealized_pnl") or 0,
        "leverage": pos.get("leverage") or 1,
        "controller_id": pos.get("controller_id") or "",
        "source": source,
        "source_name": source_name,
    }


@router.get("/servers/{name}/positions")
async def get_consolidated_positions(
    name: str,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    # Fetch executor positions and bot data in parallel
    async def fetch_executor_positions():
        try:
            client = await cm.get_client(name)
            result = await client.executors.get_positions_summary()
            if isinstance(result, dict):
                positions = result.get("positions", [])
                if not isinstance(positions, list):
                    positions = [positions] if positions else []
            elif isinstance(result, list):
                positions = result
            else:
                positions = []
            return positions
        except Exception as e:
            logger.warning("Failed to fetch executor positions from '%s': %s", name, e)
            return []

    async def fetch_bot_positions():
        try:
            result = await get_server_data_service().get_or_fetch(name, ServerDataType.BOTS_STATUS)
            if result is None:
                return []

            positions = []
            bots_list = []
            if isinstance(result, dict):
                data = result.get("data", {})
                if isinstance(data, dict):
                    bots_list = [{"bot_name": k, **v} for k, v in data.items() if isinstance(v, dict)]
                elif isinstance(data, list):
                    bots_list = [b for b in data if isinstance(b, dict)]
            elif isinstance(result, list):
                bots_list = [b for b in result if isinstance(b, dict)]

            for bot_data in bots_list:
                bot_name = bot_data.get("bot_name", "")
                performance = bot_data.get("performance", {})
                if not isinstance(performance, dict):
                    continue
                for ctrl_name, ctrl_info in performance.items():
                    if not isinstance(ctrl_info, dict):
                        continue
                    ctrl_perf = ctrl_info.get("performance", {})
                    if not isinstance(ctrl_perf, dict):
                        continue
                    pos_summary = ctrl_perf.get("positions_summary", [])
                    if not isinstance(pos_summary, list):
                        continue
                    for pos in pos_summary:
                        if isinstance(pos, dict):
                            positions.append((pos, f"{bot_name}/{ctrl_name}"))
            return positions
        except Exception as e:
            logger.warning("Failed to fetch bot positions from '%s': %s", name, e)
            return []

    exec_raw, bot_raw = await asyncio.gather(
        fetch_executor_positions(),
        fetch_bot_positions(),
    )

    executor_positions = [
        _normalize_position(pos, "executor", "Executor")
        for pos in exec_raw
        if isinstance(pos, dict)
    ]

    bot_positions = [
        _normalize_position(pos, "bot", source_name)
        for pos, source_name in bot_raw
    ]

    return {
        "executor_positions": executor_positions,
        "bot_positions": bot_positions,
    }

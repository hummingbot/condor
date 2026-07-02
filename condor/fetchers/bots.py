"""Fetch bot data from Hummingbot API."""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def extract_bots_list(result: Any) -> list[dict]:
    """Normalize the various API response formats into a list of bot dicts."""
    if result is None:
        logger.warning("Bot status API returned None")
        return []
    if isinstance(result, str):
        logger.warning(
            "Bot status API returned string (possibly HTML error page): %s",
            result[:200],
        )
        return []
    if isinstance(result, dict):
        if result.get("status") == "error":
            logger.warning(
                "Bot status API returned error: %s", result.get("message", result)
            )
            return []
        data = result.get("data", {})
        if isinstance(data, dict):
            return [
                {"bot_name": k, **v} for k, v in data.items() if isinstance(v, dict)
            ]
        elif isinstance(data, list):
            return [b for b in data if isinstance(b, dict)]
        return []
    elif isinstance(result, list):
        return [b for b in result if isinstance(b, dict)]
    logger.warning("Bot status API returned unexpected type: %s", type(result).__name__)
    return []


def build_bots_page(
    raw_status: Any,
    *,
    ctrl_configs: Optional[dict[str, dict]] = None,
    bot_runs: Optional[dict[str, str]] = None,
    latest_perf: Optional[dict[str, dict]] = None,
) -> dict:
    """Transform raw BOTS_STATUS data into a BotsPageResponse-shaped dict.

    Single source of truth for the {controllers, bots, total_pnl, total_volume}
    transform, shared by the REST route (with enrichment data) and the WS
    broadcast path (without enrichment, so all kwargs degrade to empty maps).

    Args:
        raw_status: Raw bot status API response (any of the shapes handled by
            ``extract_bots_list``).
        ctrl_configs: Controller configs keyed by config id / controller name.
        bot_runs: Deployed-at timestamps keyed by bot name.
        latest_perf: Latest DB performance snapshots keyed by controller_id.
    """
    ctrl_configs = ctrl_configs or {}
    bot_runs = bot_runs or {}
    latest_perf = latest_perf or {}

    bots_list = extract_bots_list(raw_status)
    controllers: list[dict] = []
    bots: list[dict] = []
    total_pnl = 0.0
    total_volume = 0.0

    for bot_data in bots_list:
        bot_name = bot_data.get("bot_name", "")
        bot_status = bot_data.get("status", "unknown")
        performance = bot_data.get("performance", {})
        error_logs = bot_data.get("error_logs", [])
        general_logs = bot_data.get("general_logs", [])
        if not isinstance(error_logs, list):
            error_logs = []
        if not isinstance(general_logs, list):
            general_logs = []

        num_controllers = 0

        if isinstance(performance, dict):
            for ctrl_name, ctrl_info in performance.items():
                if not isinstance(ctrl_info, dict):
                    continue

                num_controllers += 1
                ctrl_status = ctrl_info.get("status", "running")

                # Get config from pre-fetched configs
                ctrl_config = ctrl_configs.get(ctrl_name, {})
                config_id = ctrl_config.get("id") or ctrl_config.get(
                    "controller_id", ctrl_name
                )

                # Use latest DB performance if available, fallback to live bot status
                db_snap = latest_perf.get(config_id) or latest_perf.get(ctrl_name)
                if db_snap:
                    db_perf = db_snap.get("performance", db_snap)
                    if not isinstance(db_perf, dict):
                        db_perf = {}
                else:
                    db_perf = {}

                # Live performance from bot status (always available)
                live_perf = ctrl_info.get("performance", {})
                if not isinstance(live_perf, dict):
                    live_perf = {}

                # Merge: prefer live data for real-time fields, DB for historical consistency
                realized = float(
                    live_perf.get("realized_pnl_quote", 0)
                    or db_perf.get("realized_pnl_quote", 0)
                    or 0
                )
                unrealized = float(
                    live_perf.get("unrealized_pnl_quote", 0)
                    or db_perf.get("unrealized_pnl_quote", 0)
                    or 0
                )
                global_pnl = realized + unrealized
                global_pnl_pct = float(
                    live_perf.get("global_pnl_pct", 0)
                    or db_perf.get("global_pnl_pct", 0)
                    or 0
                )
                volume = float(
                    live_perf.get("volume_traded", 0)
                    or db_perf.get("volume_traded", 0)
                    or 0
                )
                close_types = live_perf.get("close_type_counts") or db_perf.get(
                    "close_type_counts", {}
                )
                if not isinstance(close_types, dict):
                    close_types = {}
                positions = live_perf.get("positions_summary") or db_perf.get(
                    "positions_summary", []
                )
                if not isinstance(positions, list):
                    positions = []

                # Primary: config dict (correct keys)
                connector = ctrl_config.get("connector_name", "")
                trading_pair = ctrl_config.get("trading_pair", "")

                # Fallback: try DB snapshot, then parse from controller name
                if not connector:
                    connector = db_perf.get(
                        "connector", db_perf.get("connector_name", "")
                    )
                if not trading_pair:
                    trading_pair = db_perf.get("trading_pair", "")

                if not connector or not trading_pair:
                    parts = ctrl_name.split("_")
                    for i, part in enumerate(parts):
                        if "-" in part and part[0].isupper():
                            if not trading_pair:
                                trading_pair = part
                            if not connector and i > 0:
                                connector = "_".join(parts[:i])
                            break

                total_pnl += global_pnl
                total_volume += volume

                config_cname = ctrl_config.get("controller_name", "")
                display_name = config_cname or ctrl_name
                display_id = config_id or ctrl_name

                controllers.append(
                    {
                        "controller_name": display_name,
                        "controller_id": display_id,
                        "bot_name": bot_name,
                        "status": ctrl_status,
                        "connector": connector,
                        "trading_pair": trading_pair,
                        "realized_pnl_quote": realized,
                        "unrealized_pnl_quote": unrealized,
                        "global_pnl_quote": global_pnl,
                        "global_pnl_pct": global_pnl_pct,
                        "volume_traded": volume,
                        "close_type_counts": close_types,
                        "positions_summary": positions,
                        "deployed_at": bot_runs.get(bot_name),
                        "config": ctrl_config,
                    }
                )

        bots.append(
            {
                "bot_name": bot_name,
                "status": bot_status,
                "num_controllers": num_controllers,
                "error_count": len(error_logs),
                "deployed_at": bot_runs.get(bot_name),
                "error_logs": error_logs[-100:],
                "general_logs": general_logs[-100:],
            }
        )

    return {
        "controllers": controllers,
        "bots": bots,
        "total_pnl": total_pnl,
        "total_volume": total_volume,
        "server_online": True,
    }


async def fetch_bots_status(client, **_kw):
    """Fetch active bots status."""
    return await client.bot_orchestration.get_active_bots_status()


async def fetch_bot_runs(client, **_kw):
    """Fetch bot run history."""
    return await client.bot_orchestration.get_bot_runs()

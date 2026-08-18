"""
Bot management operations business logic.

This module provides the core business logic for managing bots, including
status retrieval, log management, and execution control.
"""

import asyncio
from typing import Any, Literal

from mcp_servers.hummingbot_api.formatters import (
    format_active_bots_as_table,
    format_bot_logs_as_table,
)


async def _get_controller_configs_map(
    client: Any, bot_name: str
) -> dict[str, dict[str, Any]]:
    """
    Fetch a bot's controller configs keyed by both config id and config file name.

    The status payload keys controllers by their config `id`, while the update endpoint
    addresses them by file name (`_config_name`); they usually match but not always, so
    both are indexed here.
    """
    configs = await client.controllers.get_bot_controller_configs(bot_name)
    by_key: dict[str, dict[str, Any]] = {}
    for config in configs or []:
        if not isinstance(config, dict):
            continue
        for key in (config.get("id"), config.get("_config_name")):
            if key:
                by_key[str(key)] = config
    return by_key


async def get_active_bots_status(client: Any) -> dict[str, Any]:
    """
    Get the status of all active bots.

    Args:
        client: Hummingbot API client

    Returns:
        Dictionary containing active bots data and formatted table
    """
    active_bots = await client.bot_orchestration.get_active_bots_status()

    # Limit logs to last 5 entries for each bot to reduce output size
    if isinstance(active_bots, dict) and "data" in active_bots:
        for bot_name, bot_data in active_bots["data"].items():
            if isinstance(bot_data, dict):
                # Keep only the last 5 error logs
                if "error_logs" in bot_data:
                    bot_data["error_logs"] = bot_data["error_logs"][-5:]
                # Keep only the last 5 general logs
                if "general_logs" in bot_data:
                    bot_data["general_logs"] = bot_data["general_logs"][-5:]

        await _attach_kill_switches(client, active_bots["data"])

    # Format as table for better readability
    bots_table = format_active_bots_as_table(active_bots)

    # Count total bots
    total_bots = (
        len(active_bots.get("data", {})) if isinstance(active_bots, dict) else 0
    )

    return {
        "active_bots": active_bots,
        "bots_table": bots_table,
        "total_bots": total_bots,
    }


async def _attach_kill_switches(client: Any, bots: dict[str, Any]) -> None:
    """
    Attach each controller's `manual_kill_switch` to its entry in the status payload.

    The per-controller `status` the API reports is a hardcoded "running" that never
    reflects a stopped controller, so the kill switch from the bot's controller config
    is the only way to tell a running controller from a stopped one. Bots whose configs
    cannot be read keep `kill_switch=None`, which surfaces as state "unknown" rather
    than a wrong "running".
    """
    bot_names = [name for name, data in bots.items() if isinstance(data, dict)]
    if not bot_names:
        return

    configs_per_bot = await asyncio.gather(
        *(_get_controller_configs_map(client, name) for name in bot_names),
        return_exceptions=True,
    )

    for bot_name, configs in zip(bot_names, configs_per_bot):
        if isinstance(configs, BaseException):
            continue
        performance = bots[bot_name].get("performance")
        if not isinstance(performance, dict):
            continue
        for controller_id, controller_data in performance.items():
            if not isinstance(controller_data, dict):
                continue
            config = configs.get(str(controller_id))
            if config is not None:
                controller_data["kill_switch"] = bool(
                    config.get("manual_kill_switch", False)
                )


async def get_bot_logs(
    client: Any,
    bot_name: str,
    log_type: Literal["error", "general", "all"] = "all",
    limit: int = 50,
    search_term: str | None = None,
) -> dict[str, Any]:
    """
    Get detailed logs for a specific bot with filtering options.

    Args:
        client: Hummingbot API client
        bot_name: Name of the bot to get logs for
        log_type: Type of logs to retrieve ('error', 'general', or 'all')
        limit: Maximum number of log entries to return (default: 50, max: 1000)
        search_term: Optional search term to filter logs by message content

    Returns:
        Dictionary containing logs data and formatted table

    Raises:
        ValueError: If bot is not found
    """
    active_bots = await client.bot_orchestration.get_active_bots_status()

    if not isinstance(active_bots, dict) or "data" not in active_bots:
        return {
            "error": "No active bots data found",
            "message": "No active bots data found",
        }

    if bot_name not in active_bots["data"]:
        available_bots = list(active_bots["data"].keys())
        return {
            "error": f"Bot '{bot_name}' not found",
            "available_bots": available_bots,
            "message": f"Bot '{bot_name}' not found. Available bots: {available_bots}",
        }

    bot_data = active_bots["data"][bot_name]

    # Validate limit
    limit = min(max(1, limit), 1000)

    logs = []

    # Collect error logs if requested
    if log_type in ["error", "all"] and "error_logs" in bot_data:
        error_logs = bot_data["error_logs"]
        for log_entry in error_logs:
            if (
                search_term is None
                or search_term.lower() in log_entry.get("msg", "").lower()
            ):
                log_entry["log_category"] = "error"
                logs.append(log_entry)

    # Collect general logs if requested
    if log_type in ["general", "all"] and "general_logs" in bot_data:
        general_logs = bot_data["general_logs"]
        for log_entry in general_logs:
            if (
                search_term is None
                or search_term.lower() in log_entry.get("msg", "").lower()
            ):
                log_entry["log_category"] = "general"
                logs.append(log_entry)

    # Sort logs by timestamp (most recent first) and apply limit
    logs.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    logs = logs[:limit]

    # Format logs as table for better readability
    logs_table = format_bot_logs_as_table(logs)

    return {
        "bot_name": bot_name,
        "log_type": log_type,
        "search_term": search_term,
        "logs": logs,
        "logs_table": logs_table,
        "total_logs": len(logs),
    }


async def manage_bot_execution(
    client: Any,
    bot_name: str,
    action: Literal["stop_bot", "stop_controllers", "start_controllers"],
    controller_names: list[str] | None = None,
) -> dict[str, Any]:
    """
    Manage bot and controller execution states.

    Args:
        client: Hummingbot API client
        bot_name: Name of the bot to manage
        action: The action to perform
        controller_names: List of controller names (required for stop/start_controllers)

    Returns:
        Dictionary containing execution management results

    Raises:
        ValueError: If parameters are invalid
    """
    if action == "stop_bot":
        result = await client.bot_orchestration.stop_and_archive_bot(bot_name)
        return {
            "action": "stop_bot",
            "bot_name": bot_name,
            "result": result,
            "message": f"Bot execution stopped and archived: {result}",
        }

    elif action in ("stop_controllers", "start_controllers"):
        if controller_names is None or len(controller_names) == 0:
            raise ValueError(f"controller_names is required for {action} action")

        return await _set_kill_switches(
            client=client,
            bot_name=bot_name,
            controller_names=controller_names,
            kill_switch=action == "stop_controllers",
        )

    else:
        raise ValueError(f"Invalid action: {action}")


async def _set_kill_switches(
    client: Any,
    bot_name: str,
    controller_names: list[str],
    kill_switch: bool,
) -> dict[str, Any]:
    """
    Flip `manual_kill_switch` on the given controllers and verify it was written.

    The write goes to the controller config file the bot re-reads on a timer
    (`config_update_interval`, 10s by default), so the bot acts on it shortly after
    this returns — it is not applied synchronously. The config is read back here to
    confirm the flag landed on the right file, since a name that resolves to no config
    would otherwise look like a silent success.
    """
    action = "stop_controllers" if kill_switch else "start_controllers"
    verb = "stopped" if kill_switch else "started"

    # Resolve each name to the config file name the update endpoint addresses
    try:
        configs = await _get_controller_configs_map(client, bot_name)
    except Exception:
        configs = {}
    targets = {
        name: str((configs.get(name) or {}).get("_config_name") or name)
        for name in controller_names
    }

    results = await asyncio.gather(
        *(
            client.controllers.update_bot_controller_config(
                bot_name, target, {"manual_kill_switch": kill_switch}
            )
            for target in targets.values()
        ),
        return_exceptions=True,
    )

    succeeded: list[str] = []
    failed: dict[str, str] = {}
    for name, result in zip(targets, results):
        if isinstance(result, BaseException):
            failed[name] = str(result)
        else:
            succeeded.append(name)

    if not succeeded:
        raise ValueError(f"No controller could be {verb} on bot '{bot_name}': {failed}")

    # Read the configs back so the reported state is the persisted one, not the request
    confirmed: dict[str, bool | None] = {}
    try:
        updated_configs = await _get_controller_configs_map(client, bot_name)
    except Exception:
        updated_configs = {}
    for name in succeeded:
        config = updated_configs.get(name) or updated_configs.get(targets[name])
        confirmed[name] = (
            bool(config.get("manual_kill_switch", False)) if config else None
        )

    unconfirmed = [
        name for name, value in confirmed.items() if value is not kill_switch
    ]

    verified = [name for name in succeeded if name not in unconfirmed]
    message = (
        f"Controllers {verb} on bot '{bot_name}': {verified or succeeded}. "
        f"manual_kill_switch={kill_switch} written to the controller configs; "
        f"the bot applies it on its next config reload (~10s), then closes that "
        f"controller's executors. The controller stays listed in status with state '{verb}'."
    )
    if unconfirmed:
        message += f" Write NOT confirmed by read-back for: {unconfirmed}."
    if failed:
        message += f" Failed: {failed}."

    return {
        "action": action,
        "bot_name": bot_name,
        "controller_names": controller_names,
        "succeeded": succeeded,
        "failed": failed,
        "confirmed_kill_switch": confirmed,
        "message": message,
    }


async def get_bot_controller_configs(client: Any, bot_name: str) -> dict[str, Any]:
    """
    Get the current controller configs of a running bot.

    Args:
        client: Hummingbot API client
        bot_name: Name of the running bot

    Returns:
        Dictionary containing the bot's controller configs and formatted output
    """
    current_configs = await client.controllers.get_bot_controller_configs(bot_name)

    result = f"Controller configs for bot '{bot_name}':\n\n"
    if not current_configs:
        result += "No controller configs found.\n"
    else:
        for config in current_configs:
            config_id = config.get("id", "unknown")
            result += f"Config: {config_id}\n"
            for key, value in config.items():
                result += f"  {key}: {value}\n"
            result += "\n"

    return {
        "bot_name": bot_name,
        "configs": current_configs,
        "formatted_output": result,
    }


async def update_bot_controller_config(
    client: Any,
    bot_name: str,
    config_name: str,
    config_data: dict[str, Any],
    confirm_override: bool = False,
) -> dict[str, Any]:
    """
    Update a controller config inside a running bot in real-time.

    Args:
        client: Hummingbot API client
        bot_name: Name of the running bot
        config_name: Name of the config to update
        config_data: New configuration data (must include 'controller_type' and 'controller_name')
        confirm_override: Required True if overwriting an existing config

    Returns:
        Dictionary containing update results and message
    """
    # Extract and validate controller info from config_data
    config_controller_type = config_data.get("controller_type")
    config_controller_name = config_data.get("controller_name")

    if not config_controller_type or not config_controller_name:
        raise ValueError(
            "config_data must include 'controller_type' and 'controller_name'"
        )

    # Validate config first
    await client.controllers.validate_controller_config(
        config_controller_type, config_controller_name, config_data
    )

    if not confirm_override:
        current_configs = await client.controllers.get_bot_controller_configs(bot_name)
        config = next((c for c in current_configs if c.get("id") == config_name), None)
        if config:
            return {
                "action": "update_config",
                "exists": True,
                "config_name": config_name,
                "bot_name": bot_name,
                "current_config": config,
                "message": (
                    f"Config '{config_name}' already exists in bot '{bot_name}' with data: {config}. "
                    "Set confirm_override=True to update it."
                ),
            }
        else:
            clean_data = {k: v for k, v in config_data.items() if not k.startswith("_")}
            update_op = await client.controllers.update_bot_controller_config(
                bot_name, config_name, clean_data
            )
            return {
                "action": "update_config",
                "exists": False,
                "config_name": config_name,
                "bot_name": bot_name,
                "result": update_op,
                "message": f"Config created in bot '{bot_name}': {update_op}",
            }
    else:
        # Ensure config_data has the correct id
        if "id" not in config_data or config_data["id"] != config_name:
            config_data["id"] = config_name
        clean_data = {k: v for k, v in config_data.items() if not k.startswith("_")}
        update_op = await client.controllers.update_bot_controller_config(
            bot_name, config_name, clean_data
        )
        return {
            "action": "update_config",
            "exists": True,
            "config_name": config_name,
            "bot_name": bot_name,
            "result": update_op,
            "message": f"Config updated in bot '{bot_name}': {update_op}",
        }

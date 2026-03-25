"""
Controller management operations business logic.

This module provides the core business logic for managing controllers and their
configurations, including exploration, modification, and bot deployment.
"""
from typing import Any, Literal


async def manage_controllers(
    client: Any,
    action: Literal["list", "describe", "upsert", "delete"],
    target: Literal["controller", "config"] | None = None,
    controller_type: Literal["directional_trading", "market_making", "generic"] | None = None,
    controller_name: str | None = None,
    controller_code: str | None = None,
    config_name: str | None = None,
    config_data: dict[str, Any] | None = None,
    confirm_override: bool = False,
    include_code: bool = False,
) -> dict[str, Any]:
    """
    Unified controller management: list, describe, upsert, delete.

    Design-time only — works with saved templates and configs for future deployments.
    Does NOT affect running bots. To modify a live bot's config, use manage_bots with action='update_config'.

    Routes to explore_controllers for list/describe and modify_controllers for upsert/delete.
    """
    if action in ("list", "describe"):
        return await explore_controllers(
            client=client,
            action=action,
            controller_type=controller_type,
            controller_name=controller_name,
            config_name=config_name,
            include_code=include_code,
        )
    elif action in ("upsert", "delete"):
        if not target:
            raise ValueError("'target' parameter ('controller' or 'config') is required for upsert/delete actions")
        return await modify_controllers(
            client=client,
            action=action,
            target=target,
            controller_type=controller_type,
            controller_name=controller_name,
            controller_code=controller_code,
            config_name=config_name,
            config_data=config_data,
            confirm_override=confirm_override,
        )
    else:
        raise ValueError(f"Invalid action '{action}'. Use 'list', 'describe', 'upsert', or 'delete'.")


async def explore_controllers(
    client: Any,
    action: Literal["list", "describe"],
    controller_type: Literal["directional_trading", "market_making", "generic"] | None = None,
    controller_name: str | None = None,
    config_name: str | None = None,
    include_code: bool = False,
) -> dict[str, Any]:
    """
    Explore controllers and their configurations.

    Args:
        client: Hummingbot API client
        action: "list" to list controllers or "describe" to show details
        controller_type: Type of controller to filter by
        controller_name: Name of controller to describe
        config_name: Name of config to describe
        include_code: If True, include full controller source code in describe output

    Returns:
        Dictionary containing exploration results and formatted output
    """
    # List all controllers and their configs
    controllers = await client.controllers.list_controllers()
    configs = await client.controllers.list_controller_configs()

    if action == "list":
        result = "Available Controllers:\n\n"
        for c_type, controller_list in controllers.items():
            if controller_type is not None and c_type != controller_type:
                continue
            result += f"Controller Type: {c_type}\n"
            for controller in controller_list:
                controller_configs = [c for c in configs if c.get('controller_name') == controller]
                result += f"- {controller} ({len(controller_configs)} configs)\n"
                if len(controller_configs) > 0:
                    for config in controller_configs:
                        result += f"    - {config.get('id', 'unknown')}\n"

        return {
            "action": "list",
            "controllers": controllers,
            "configs": configs,
            "formatted_output": result,
        }

    elif action == "describe":
        result = ""
        config = None

        # Get config if specified — show config details directly
        if config_name:
            config = await client.controllers.get_controller_config(config_name)
            if config:
                if controller_name and controller_name != config.get("controller_name"):
                    controller_name = config.get("controller_name")
                    result += f"Controller name not matching, using config's controller name: {controller_name}\n"
                elif not controller_name:
                    controller_name = config.get("controller_name")
                result += f"Config '{config_name}' Details:\n"
                for key, value in config.items():
                    result += f"  {key}: {value}\n"
                result += "\n"

        if not controller_name:
            return {
                "action": "describe",
                "error": "Please provide a controller_name or config_name to describe.",
                "formatted_output": "Please provide a controller_name or config_name to describe.",
            }

        # Determine the controller type
        found_controller_type = None
        for c_type, controller_list in controllers.items():
            if controller_name in controller_list:
                found_controller_type = c_type
                break

        if not found_controller_type:
            return {
                "action": "describe",
                "error": f"Controller '{controller_name}' not found.",
                "formatted_output": f"Controller '{controller_name}' not found.",
            }

        # Get config template (lightweight — just parameter schema)
        controller_configs = [c.get("id") for c in configs if c.get('controller_name') == controller_name]
        template = await client.controllers.get_controller_config_template(found_controller_type, controller_name)

        result += f"Controller: {controller_name} ({found_controller_type})\n\n"

        # Only fetch and include full source code when explicitly requested
        controller_code_content = None
        if include_code:
            controller_code_content = await client.controllers.get_controller(found_controller_type, controller_name)
            result += f"Controller Code:\n{controller_code_content}\n\n"

        # Format config template parameters as table
        result += "Configuration Parameters:\n"
        result += "parameter                    | type              | default\n"
        result += "-" * 80 + "\n"

        for param_name, param_info in template.items():
            if param_name in ['id', 'controller_name', 'controller_type', 'candles_config', 'initial_positions']:
                continue  # Skip internal fields

            param_type = str(param_info.get('type', 'unknown'))
            # Simplify type names
            param_type = param_type.replace("<class '", "").replace("'>", "").replace("decimal.Decimal", "Decimal")
            param_type = param_type.replace("typing.", "").split(".")[-1][:15]

            default = str(param_info.get('default', 'None'))
            if len(default) > 30:
                default = default[:27] + "..."

            result += f"{param_name:28} | {param_type:17} | {default}\n"

        result += "\n"

        # Format configs list
        result += f"Total Configs: {len(controller_configs)}\n"
        if len(controller_configs) <= 10:
            result += "Configs:\n" + "\n".join(f"  - {c}" for c in controller_configs if c) + "\n"
        else:
            result += f"Configs (showing first 10 of {len(controller_configs)}):\n"
            result += "\n".join(f"  - {c}" for c in controller_configs[:10] if c) + "\n"
            result += f"  ... and {len(controller_configs) - 10} more\n"

        if not include_code:
            result += "\nTip: Set include_code=True to see the full controller source code.\n"

        return_data = {
            "action": "describe",
            "controller_name": controller_name,
            "controller_type": found_controller_type,
            "template": template,
            "configs": controller_configs,
            "config_details": config,
            "formatted_output": result,
        }
        if controller_code_content is not None:
            return_data["controller_code"] = controller_code_content
        return return_data

    else:
        return {
            "action": action,
            "error": "Invalid action. Use 'list' or 'describe'.",
            "formatted_output": "Invalid action. Use 'list' or 'describe'.",
        }


async def modify_controllers(
    client: Any,
    action: Literal["upsert", "delete"],
    target: Literal["controller", "config"],
    controller_type: Literal["directional_trading", "market_making", "generic"] | None = None,
    controller_name: str | None = None,
    controller_code: str | None = None,
    config_name: str | None = None,
    config_data: dict[str, Any] | None = None,
    confirm_override: bool = False,
) -> dict[str, Any]:
    """
    Create, update, or delete controllers and saved configurations (design-time only).

    Does NOT affect running bots. To modify a live bot's config, use manage_bots with action='update_config'.

    Args:
        client: Hummingbot API client
        action: "upsert" (create/update) or "delete"
        target: "controller" (template) or "config" (instance)
        controller_type: Type of controller
        controller_name: Name of controller
        controller_code: Code for controller (required for controller upsert)
        config_name: Name of config
        config_data: Configuration data (required for config upsert)
        confirm_override: Confirm overwriting existing items

    Returns:
        Dictionary containing modification results and message

    Raises:
        ValueError: If required parameters are missing or invalid
    """
    if target == "controller":
        if action == "upsert":
            if not controller_type or not controller_name or not controller_code:
                raise ValueError("controller_type, controller_name, and controller_code are required for controller upsert")

            # Check if controller exists
            controllers = await client.controllers.list_controllers()
            exists = controller_name in controllers.get(controller_type, [])

            if exists and not confirm_override:
                existing_code = await client.controllers.get_controller(controller_type, controller_name)
                return {
                    "action": "upsert",
                    "target": "controller",
                    "exists": True,
                    "controller_name": controller_name,
                    "controller_type": controller_type,
                    "current_code": existing_code,
                    "message": (f"Controller '{controller_name}' already exists and this is the current code: {existing_code}. "
                               f"Set confirm_override=True to update it."),
                }

            result = await client.controllers.create_or_update_controller(
                controller_type, controller_name, controller_code
            )

            return {
                "action": "upsert",
                "target": "controller",
                "exists": exists,
                "controller_name": controller_name,
                "controller_type": controller_type,
                "result": result,
                "message": f"Controller {'updated' if exists else 'created'}: {result}",
            }

        elif action == "delete":
            if not controller_type or not controller_name:
                raise ValueError("controller_type and controller_name are required for controller delete")

            result = await client.controllers.delete_controller(controller_type, controller_name)

            return {
                "action": "delete",
                "target": "controller",
                "controller_name": controller_name,
                "controller_type": controller_type,
                "result": result,
                "message": f"Controller deleted: {result}",
            }

    elif target == "config":
        if action == "upsert":
            if not config_name or not config_data:
                raise ValueError("config_name and config_data are required for config upsert")

            # Extract controller_type and controller_name from config_data
            config_controller_type = config_data.get("controller_type")
            config_controller_name = config_data.get("controller_name")

            if not config_controller_type or not config_controller_name:
                raise ValueError("config_data must include 'controller_type' and 'controller_name'")

            # Validate config first
            await client.controllers.validate_controller_config(config_controller_type, config_controller_name, config_data)

            # Modifying saved/global config (design-time only)
            if "id" not in config_data or config_data["id"] != config_name:
                config_data["id"] = config_name

            controller_configs = await client.controllers.list_controller_configs()
            exists = config_name in [c.get("id") for c in controller_configs]

            if exists and not confirm_override:
                existing_config = await client.controllers.get_controller_config(config_name)
                return {
                    "action": "upsert",
                    "target": "config",
                    "exists": True,
                    "config_name": config_name,
                    "current_config": existing_config,
                    "message": (f"Config '{config_name}' already exists with data: {existing_config}. "
                               "Set confirm_override=True to update it."),
                }

            result = await client.controllers.create_or_update_controller_config(config_name, config_data)
            return {
                "action": "upsert",
                "target": "config",
                "exists": exists,
                "config_name": config_name,
                "result": result,
                "message": f"Config {'updated' if exists else 'created'}: {result}",
            }

        elif action == "delete":
            if not config_name:
                raise ValueError("config_name is required for config delete")

            result = await client.controllers.delete_controller_config(config_name)
            await client.bot_orchestration.deploy_v2_controllers()

            return {
                "action": "delete",
                "target": "config",
                "config_name": config_name,
                "result": result,
                "message": f"Config deleted: {result}",
            }

    else:
        raise ValueError("Invalid target. Must be 'controller' or 'config'.")


async def deploy_bot(
    client: Any,
    bot_name: str,
    controllers_config: list[str],
    account_name: str | None = "master_account",
    max_global_drawdown_quote: float | None = None,
    max_controller_drawdown_quote: float | None = None,
    image: str = "hummingbot/hummingbot:latest",
) -> dict[str, Any]:
    """
    Deploy a bot with specified controller configurations.

    Args:
        client: Hummingbot API client
        bot_name: Name of the bot to deploy
        controllers_config: List of controller config names
        account_name: Account name to use
        max_global_drawdown_quote: Maximum global drawdown
        max_controller_drawdown_quote: Maximum per-controller drawdown
        image: Docker image to use

    Returns:
        Dictionary containing deployment results
    """
    result = await client.bot_orchestration.deploy_v2_controllers(
        instance_name=bot_name,
        controllers_config=controllers_config,
        credentials_profile=account_name,
        max_global_drawdown_quote=max_global_drawdown_quote,
        max_controller_drawdown_quote=max_controller_drawdown_quote,
        image=image,
    )

    return {
        "bot_name": bot_name,
        "controllers_config": controllers_config,
        "account_name": account_name,
        "image": image,
        "result": result,
        "message": f"Bot Deployment Result: {result}",
    }

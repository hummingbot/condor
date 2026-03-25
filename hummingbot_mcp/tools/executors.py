"""
Executor management tools for Hummingbot MCP Server.

This module provides business logic for managing trading executors including
creation, viewing, stopping, and position management with progressive disclosure.
"""
import logging
from typing import Any

from hummingbot_mcp.executor_preferences import executor_preferences
from hummingbot_mcp.formatters.executors import (
    format_executor_detail,
    format_executor_schema_table,
    format_executors_table,
    format_positions_held_table,
    format_positions_summary,
)
from hummingbot_mcp.schemas import ManageExecutorsRequest

logger = logging.getLogger("hummingbot-mcp")

# Internal fields injected by the MCP layer, not user-supplied
_INTERNAL_FIELDS = {"type", "executor_type", "id"}


def validate_executor_config(config: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate config keys against the backend schema properties.

    Returns a list of error strings. An empty list means the config is valid.
    """
    errors: list[str] = []
    _validate_level(config, schema, "", errors)
    return errors


def _validate_level(config: dict[str, Any], schema: dict[str, Any], path: str, errors: list[str]) -> None:
    """Recursively validate config keys against schema properties."""
    properties = schema.get("properties", {})
    if not properties:
        return

    allowed = set(properties.keys())

    for key in config:
        if not path and key in _INTERNAL_FIELDS:
            continue
        if key not in allowed:
            field_list = ", ".join(sorted(allowed - _INTERNAL_FIELDS))
            location = f" inside '{path}'" if path else ""
            errors.append(f"Unknown field '{key}'{location}. Allowed fields: {field_list}")
            continue
        # Recurse into nested objects
        prop_schema = properties[key]
        if isinstance(prop_schema, dict) and isinstance(config[key], dict) and "properties" in prop_schema:
            _validate_level(config[key], prop_schema, key, errors)


async def manage_executors(client: Any, request: ManageExecutorsRequest) -> dict[str, Any]:
    """
    Manage executors with progressive disclosure.

    Args:
        client: Hummingbot API client
        request: ManageExecutorsRequest with action and parameters

    Returns:
        Dictionary containing results and formatted output
    """
    flow_stage = request.get_flow_stage()

    if flow_stage == "list_types":
        # Brief static response — full descriptions are in the tool docstring
        formatted = (
            "Available Executor Types:\n\n"
            "- **position_executor** — Directional trading with entry, stop-loss, and take-profit\n"
            "- **dca_executor** — Dollar-cost averaging for gradual position building\n"
            "- **grid_executor** — Grid trading across multiple price levels in ranging markets\n"
            "- **order_executor** — Simple BUY/SELL order with execution strategy\n"
            "- **lp_executor** — Liquidity provision on CLMM DEXs (Meteora, Raydium)\n\n"
            "Provide `executor_type` to see the configuration schema."
        )

        return {
            "action": "list_types",
            "formatted_output": formatted,
            "next_step": "Call again with 'executor_type' to see the configuration schema",
            "example": "manage_executors(executor_type='position_executor')",
        }

    elif flow_stage == "show_schema":
        # Stage 2: Show config schema with user defaults
        try:
            schema = await client.executors.get_executor_config_schema(request.executor_type)
        except Exception as e:
            return {
                "action": "show_schema",
                "error": f"Failed to get schema for {request.executor_type}: {e}",
                "formatted_output": f"Error: Failed to get schema for {request.executor_type}: {e}",
            }

        # Get user defaults
        user_defaults = executor_preferences.get_defaults(request.executor_type)

        # Get the guide from the markdown file
        executor_guide = executor_preferences.get_executor_guide(request.executor_type)

        formatted = f"Configuration Schema for {request.executor_type}\n\n"
        if executor_guide:
            formatted += f"{executor_guide}\n\n"

        formatted += format_executor_schema_table(schema, user_defaults)

        if user_defaults:
            formatted += f"\n\nYour saved defaults for {request.executor_type}:\n"
            for key, value in user_defaults.items():
                formatted += f"  {key}: {value}\n"
            formatted += f"\nPreferences file: {executor_preferences.get_preferences_path()}"

        return {
            "action": "show_schema",
            "executor_type": request.executor_type,
            "schema": schema,
            "user_defaults": user_defaults,
            "formatted_output": formatted,
            "next_step": "Call with action='create' and executor_config to create an executor",
            "example": f"manage_executors(action='create', executor_type='{request.executor_type}', executor_config={{...}})",
        }

    elif flow_stage == "create":
        # Stage 3: Create executor
        executor_type = request.executor_type or request.executor_config.get("type") or request.executor_config.get("executor_type")

        if not executor_type:
            return {
                "action": "create",
                "error": "executor_type is required for creating an executor",
                "formatted_output": "Error: Please provide executor_type",
            }

        # Merge with defaults
        merged_config = executor_preferences.merge_with_defaults(executor_type, request.executor_config)

        # Ensure type is set in config
        if "type" not in merged_config and "executor_type" not in merged_config:
            merged_config["type"] = executor_type

        # Validate config fields against backend schema before sending
        try:
            schema = await client.executors.get_executor_config_schema(executor_type)
            validation_errors = validate_executor_config(merged_config, schema)
            if validation_errors:
                error_list = "\n".join(f"  - {e}" for e in validation_errors)
                return {
                    "action": "create",
                    "error": f"Invalid executor configuration:\n{error_list}",
                    "formatted_output": (
                        f"Error: Invalid configuration for {executor_type}:\n\n"
                        f"{error_list}\n\n"
                        f"Please fix the fields above and try again."
                    ),
                }
        except Exception:
            pass  # If schema fetch fails, skip validation

        account = request.account_name or "master_account"
        controller_id = request.controller_id or "main"

        try:
            result = await client.executors.create_executor(
                executor_config=merged_config,
                account_name=account,
                controller_id=controller_id,
            )

            # Save as default if requested
            if request.save_as_default:
                executor_preferences.update_defaults(executor_type, request.executor_config)

            executor_id = result.get("executor_id") or result.get("id")

            formatted = f"Executor created successfully!\n\n"
            formatted += f"Executor ID: {executor_id or 'N/A'}\n"
            formatted += f"Type: {executor_type}\n"
            formatted += f"Account: {account}\n"

            if request.save_as_default:
                formatted += f"\nConfiguration saved as default for {executor_type}"

            return {
                "action": "create",
                "executor_id": executor_id,
                "executor_type": executor_type,
                "account": account,
                "config_used": merged_config,
                "saved_as_default": request.save_as_default,
                "result": result,
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "create",
                "error": str(e),
                "formatted_output": f"Error creating executor: {e}",
            }

    elif flow_stage == "search":
        # Search executors, or get detail for a specific executor_id
        try:
            if request.executor_id:
                # Get specific executor detail
                result = await client.executors.get_executor(request.executor_id)
                formatted = format_executor_detail(result)
                return {
                    "action": "search",
                    "executor_id": request.executor_id,
                    "executor": result,
                    "formatted_output": formatted,
                }

            result = await client.executors.search_executors(
                account_names=request.account_names,
                connector_names=request.connector_names,
                trading_pairs=request.trading_pairs,
                executor_types=request.executor_types,
                status=request.status,
                cursor=request.cursor,
                limit=request.limit,
                controller_ids=request.controller_ids,
            )

            executors = result.get("data", result) if isinstance(result, dict) else result
            if not isinstance(executors, list):
                executors = [executors] if executors else []

            formatted = f"Executors Found: {len(executors)}\n\n"
            formatted += format_executors_table(executors)

            # Add pagination info if available
            if isinstance(result, dict) and "next_cursor" in result:
                formatted += f"\n\nNext cursor: {result.get('next_cursor')}"

            return {
                "action": "search",
                "executors": executors,
                "count": len(executors),
                "cursor": result.get("next_cursor") if isinstance(result, dict) else None,
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "search",
                "error": str(e),
                "formatted_output": f"Error searching executors: {e}",
            }

    elif flow_stage == "stop":
        # Stage 6: Stop executor
        try:
            result = await client.executors.stop_executor(
                executor_id=request.executor_id,
                keep_position=request.keep_position,
            )

            formatted = f"Executor stopped successfully!\n\n"
            formatted += f"Executor ID: {request.executor_id}\n"
            formatted += f"Keep Position: {request.keep_position}\n"

            return {
                "action": "stop",
                "executor_id": request.executor_id,
                "keep_position": request.keep_position,
                "result": result,
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "stop",
                "error": str(e),
                "formatted_output": f"Error stopping executor {request.executor_id}: {e}",
            }

    elif flow_stage == "get_logs":
        # Get executor logs via direct API call (not yet in client library)
        try:
            params = {"limit": request.limit}
            if request.log_level:
                params["level"] = request.log_level.upper()

            resp = await client.executors.session.get(
                f"{client.executors.base_url}/executors/{request.executor_id}/logs",
                params=params,
            )
            resp.raise_for_status()
            result = await resp.json()

            logs = result.get("logs", [])
            total = result.get("total_count", len(logs))

            formatted = f"Executor Logs: {request.executor_id}\n"
            formatted += f"Total entries: {total}"
            if request.log_level:
                formatted += f" (filtered: {request.log_level.upper()})"
            formatted += f", showing: {len(logs)}\n\n"

            if not logs:
                formatted += "No log entries found. Note: logs are only available for active executors and are cleared on completion."
            else:
                for entry in logs:
                    ts = entry.get("timestamp", "")
                    level = entry.get("level", "")
                    msg = entry.get("message", "")
                    formatted += f"[{ts}] {level}: {msg}\n"
                    exc = entry.get("exc_info")
                    if exc:
                        formatted += f"  Exception: {exc}\n"

            return {
                "action": "get_logs",
                "executor_id": request.executor_id,
                "logs": logs,
                "total_count": total,
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "get_logs",
                "error": str(e),
                "formatted_output": f"Error getting logs for executor {request.executor_id}: {e}",
            }

    elif flow_stage == "get_preferences":
        # Stage 8: Get saved preferences (returns raw markdown content)
        raw_content = executor_preferences.get_raw_content()

        formatted = f"Preferences file: {executor_preferences.get_preferences_path()}\n\n"
        formatted += raw_content

        return {
            "action": "get_preferences",
            "executor_type": request.executor_type,
            "raw_content": raw_content,
            "preferences_path": executor_preferences.get_preferences_path(),
            "formatted_output": formatted,
        }

    elif flow_stage == "save_preferences":
        # Stage 9: Save full preferences file content
        executor_preferences.save_content(request.preferences_content)

        formatted = f"Preferences file saved successfully.\n\n"
        formatted += f"Preferences file: {executor_preferences.get_preferences_path()}"

        return {
            "action": "save_preferences",
            "preferences_path": executor_preferences.get_preferences_path(),
            "formatted_output": formatted,
        }

    elif flow_stage == "reset_preferences":
        # Stage 10: Reset preferences to defaults (preserves YAML configs)
        preserved = executor_preferences.reset_to_defaults()
        preserved_count = sum(1 for c in preserved.values() if c)

        formatted = "Preferences documentation updated to latest version.\n\n"
        if preserved_count > 0:
            preserved_names = [k for k, v in preserved.items() if v]
            formatted += f"Preserved {preserved_count} config(s): {', '.join(preserved_names)}\n"
        else:
            formatted += "No existing configs to preserve.\n"
        formatted += f"\nPreferences file: {executor_preferences.get_preferences_path()}"

        return {
            "action": "reset_preferences",
            "preserved_configs": preserved,
            "preserved_count": preserved_count,
            "formatted_output": formatted,
        }

    # Position management stages (merged from manage_executor_positions)

    elif flow_stage == "positions_summary":
        # Get all positions, or specific position if connector_name + trading_pair given
        try:
            if request.connector_name and request.trading_pair:
                # Get specific position detail
                account = request.account_name or "master_account"
                result = await client.executors.get_position_held(
                    connector_name=request.connector_name,
                    trading_pair=request.trading_pair,
                    account_name=account,
                    controller_id=request.controller_id,
                )

                formatted = f"Position Details\n\n"
                formatted += f"Connector: {request.connector_name}\n"
                formatted += f"Trading Pair: {request.trading_pair}\n"
                formatted += f"Account: {account}\n\n"

                if result:
                    positions = [result] if not isinstance(result, list) else result
                    formatted += format_positions_held_table(positions)
                else:
                    formatted += "No position found for this connector/pair combination."

                return {
                    "action": "positions_summary",
                    "connector_name": request.connector_name,
                    "trading_pair": request.trading_pair,
                    "account": account,
                    "position": result,
                    "formatted_output": formatted,
                }

            result = await client.executors.get_positions_summary(
                controller_id=request.controller_id,
            )

            positions = result.get("positions", result) if isinstance(result, dict) else result
            if not isinstance(positions, list):
                positions = [positions] if positions else []

            formatted = f"Positions Held Summary\n\n"

            if isinstance(result, dict) and any(k in result for k in ["total_positions", "total_value", "by_connector"]):
                formatted += format_positions_summary(result)
                if positions:
                    formatted += "\n\nPositions Detail:\n"
                    formatted += format_positions_held_table(positions)
            else:
                formatted += format_positions_held_table(positions)

            return {
                "action": "positions_summary",
                "positions": positions,
                "summary": result if isinstance(result, dict) else {"positions": positions},
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "positions_summary",
                "error": str(e),
                "formatted_output": f"Error getting positions: {e}",
            }

    elif flow_stage == "clear_position":
        # Clear a position that was closed manually
        account = request.account_name or "master_account"
        try:
            result = await client.executors.clear_position_held(
                connector_name=request.connector_name,
                trading_pair=request.trading_pair,
                account_name=account,
                controller_id=request.controller_id,
            )

            formatted = f"Position cleared successfully!\n\n"
            formatted += f"Connector: {request.connector_name}\n"
            formatted += f"Trading Pair: {request.trading_pair}\n"
            formatted += f"Account: {account}\n"

            return {
                "action": "clear_position",
                "connector_name": request.connector_name,
                "trading_pair": request.trading_pair,
                "account": account,
                "result": result,
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "clear_position",
                "error": str(e),
                "formatted_output": f"Error clearing position: {e}",
            }

    elif flow_stage == "performance_report":
        try:
            result = await client.executors.get_performance_report(
                controller_id=request.controller_id,
            )
            formatted = "Executor Performance Report\n\n"
            if request.controller_id:
                formatted += f"Controller: {request.controller_id}\n\n"
            if isinstance(result, dict):
                for key, value in result.items():
                    formatted += f"{key}: {value}\n"
            else:
                formatted += str(result)
            return {
                "action": "performance_report",
                "result": result,
                "formatted_output": formatted,
            }
        except Exception as e:
            return {
                "action": "performance_report",
                "error": str(e),
                "formatted_output": f"Error getting performance report: {e}",
            }

    else:
        return {
            "action": "unknown",
            "error": f"Unknown flow stage: {flow_stage}",
            "formatted_output": f"Error: Unknown flow stage: {flow_stage}",
        }

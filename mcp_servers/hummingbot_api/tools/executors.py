"""
Executor read and control tools for the Hummingbot MCP Server.

One impl per registered tool (FEAT-062). These used to be branches of a
``get_flow_stage()`` dispatch inside a single ``manage_executors`` mega-tool, where a
missing argument silently re-routed the call to a different branch — ``action="create"``
without a config fell through to ``show_schema``, ``save_preferences`` without content
fell through to ``list_types``. Reaching a branch is now the tool name, so a missing
argument is a host-side validation error instead of a different answer to a different
question.

Creation lives in ``executor_create.py``; the two halves are split because a create is
dangerous by name and everything here except ``stop_executor`` is safe by name.
"""

import logging
from typing import Any

from mcp_servers.hummingbot_api.executor_preferences import executor_preferences
from mcp_servers.hummingbot_api.formatters.executors import (
    format_executor_detail,
    format_executors_table,
    format_positions_held_table,
    format_positions_summary,
)

logger = logging.getLogger("hummingbot-mcp")


async def list_executors(
    client: Any,
    *,
    account_names: list[str] | None = None,
    connector_names: list[str] | None = None,
    trading_pairs: list[str] | None = None,
    executor_types: list[str] | None = None,
    controller_ids: list[str] | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List and filter executors."""
    try:
        result = await client.executors.search_executors(
            account_names=account_names,
            connector_names=connector_names,
            trading_pairs=trading_pairs,
            executor_types=executor_types,
            status=status,
            cursor=cursor,
            limit=limit,
            controller_ids=controller_ids,
        )

        executors = result.get("data", result) if isinstance(result, dict) else result
        if not isinstance(executors, list):
            executors = [executors] if executors else []

        formatted = f"Executors Found: {len(executors)}\n\n"
        formatted += format_executors_table(executors)

        if isinstance(result, dict) and "next_cursor" in result:
            formatted += f"\n\nNext cursor: {result.get('next_cursor')}"

        return {
            "action": "list",
            "executors": executors,
            "count": len(executors),
            "cursor": result.get("next_cursor") if isinstance(result, dict) else None,
            "formatted_output": formatted,
        }

    except Exception as e:
        return {
            "action": "list",
            "error": str(e),
            "formatted_output": f"Error listing executors: {e}",
        }


async def _fetch_logs(
    client: Any, executor_id: str, log_level: str | None, limit: int
) -> dict[str, Any]:
    """Executor logs via a direct API call (not yet in the client library)."""
    params: dict[str, Any] = {"limit": limit}
    if log_level:
        params["level"] = log_level.upper()

    resp = await client.executors.session.get(
        f"{client.executors.base_url}/executors/{executor_id}/logs",
        params=params,
    )
    resp.raise_for_status()
    return await resp.json()


def _format_logs(
    executor_id: str, result: dict[str, Any], log_level: str | None
) -> str:
    logs = result.get("logs", [])
    total = result.get("total_count", len(logs))

    formatted = f"Executor Logs: {executor_id}\n"
    formatted += f"Total entries: {total}"
    if log_level:
        formatted += f" (filtered: {log_level.upper()})"
    formatted += f", showing: {len(logs)}\n\n"

    if not logs:
        formatted += (
            "No log entries found. Note: logs are only available for active "
            "executors and are cleared on completion."
        )
        return formatted

    for entry in logs:
        ts = entry.get("timestamp", "")
        level = entry.get("level", "")
        msg = entry.get("message", "")
        formatted += f"[{ts}] {level}: {msg}\n"
        exc = entry.get("exc_info")
        if exc:
            formatted += f"  Exception: {exc}\n"
    return formatted


async def get_executor(
    client: Any,
    *,
    executor_id: str,
    include_logs: bool = False,
    log_level: str | None = None,
    log_limit: int = 50,
) -> dict[str, Any]:
    """One executor's detail, optionally with its logs appended.

    Logs are a flag rather than their own tool because they are never wanted without the
    executor they belong to, and fetching both was two calls under the mega-tool.
    """
    try:
        result = await client.executors.get_executor(executor_id)
        formatted = format_executor_detail(result)
        response: dict[str, Any] = {
            "action": "get",
            "executor_id": executor_id,
            "executor": result,
        }
    except Exception as e:
        return {
            "action": "get",
            "executor_id": executor_id,
            "error": str(e),
            "formatted_output": f"Error getting executor {executor_id}: {e}",
        }

    if include_logs:
        try:
            log_result = await _fetch_logs(client, executor_id, log_level, log_limit)
            response["logs"] = log_result.get("logs", [])
            response["total_log_count"] = log_result.get(
                "total_count", len(response["logs"])
            )
            formatted += "\n\n" + _format_logs(executor_id, log_result, log_level)
        except Exception as e:
            # The detail was fetched; say the logs were not rather than losing both.
            formatted += f"\n\nError getting logs for executor {executor_id}: {e}"
            response["logs_error"] = str(e)

    response["formatted_output"] = formatted
    return response


async def stop_executor(
    client: Any,
    *,
    executor_id: str,
    keep_position: bool = False,
) -> dict[str, Any]:
    """Stop an executor, closing or keeping its position."""
    try:
        result = await client.executors.stop_executor(
            executor_id=executor_id,
            keep_position=keep_position,
        )

        if result.get("status") == "already_terminated":
            # No-op: the executor was already terminal. Say so — a generic
            # "stopped successfully" would hide the one payload that matters
            # (an orphaned on-chain position needing recovery).
            formatted = "Executor was ALREADY terminated (stop was a no-op).\n\n"
            formatted += f"Executor ID: {executor_id}\n"
            formatted += f"Final close_type: {result.get('close_type')}\n"
            if result.get("orphaned_position"):
                formatted += (
                    f"\n🚨 ORPHANED POSITION: {result.get('position_address')} is still "
                    "open on-chain with no automated owner. Stopping the executor does not "
                    "close it — it has already terminated. Close it with "
                    'manage_clmm(action="close", position_address=..., pool_address=...), '
                    "then mark it recovered with "
                    f'resolve_orphaned_position(executor_id="{executor_id}").\n'
                    "Run list_orphaned_positions() to get the dex, pool and network for "
                    "the call.\n"
                )
            elif result.get("position_address"):
                formatted += f"Position address (final state): {result.get('position_address')}\n"
        else:
            formatted = "Executor stopped successfully!\n\n"
            formatted += f"Executor ID: {executor_id}\n"
            formatted += f"Keep Position: {keep_position}\n"

        return {
            "action": "stop",
            "executor_id": executor_id,
            "keep_position": keep_position,
            "result": result,
            "formatted_output": formatted,
        }

    except Exception as e:
        return {
            "action": "stop",
            "error": str(e),
            "formatted_output": f"Error stopping executor {executor_id}: {e}",
        }


async def list_orphaned_positions(client: Any) -> dict[str, Any]:
    """Terminated executors that may still own an on-chain position."""
    try:
        resp = await client.executors.session.get(
            f"{client.executors.base_url}/executors/positions/orphaned",
        )
        resp.raise_for_status()
        result = await resp.json()

        orphans = result.get("orphans", [])
        formatted = (
            f"Orphaned position candidates: {result.get('count', len(orphans))}\n\n"
        )
        if not orphans:
            formatted += (
                "No orphaned positions. All terminated executors closed cleanly."
            )
        else:
            for o in orphans:
                formatted += (
                    f"- {o.get('executor_id')} ({o.get('executor_type')}, "
                    f"{o.get('trading_pair')} on {o.get('connector_name')}, "
                    f"close_type={o.get('close_type')}, closed_at={o.get('closed_at')})\n"
                )
                if o.get("position_address"):
                    formatted += f"    position: {o['position_address']}\n"
                if o.get("lp_provider") or o.get("pool_address"):
                    formatted += f"    dex: {o.get('lp_provider')}  pool: {o.get('pool_address')}\n"
                if o.get("needs_onchain_reconciliation"):
                    formatted += (
                        "    position address unknown (API restart) - reconcile against "
                        "on-chain positions (get_portfolio_overview include_lp_positions=True)\n"
                    )
                elif o.get("lp_provider") and o.get("pool_address"):
                    # Spell the recovery call out: the executor is terminated, so stopping it is
                    # a no-op and the position can only be closed by address.
                    formatted += (
                        '    close with: manage_clmm(action="close", '
                        f"connector=\"{o.get('lp_provider')}\", "
                        f"network=\"{o.get('connector_name')}\", "
                        f"position_address=\"{o.get('position_address')}\", "
                        f"pool_address=\"{o.get('pool_address')}\")\n"
                    )
            formatted += (
                '\nRecover each by closing the position with manage_clmm(action="close") - '
                "pool_address is required because LP-executor positions are not in the API "
                "database. Stopping the executor will NOT close it; it has already terminated. "
                'Then mark it recovered with resolve_orphaned_position(executor_id="...").'
            )

        return {
            "action": "orphaned",
            "result": result,
            "formatted_output": formatted,
        }

    except Exception as e:
        return {
            "action": "orphaned",
            "error": str(e),
            "formatted_output": f"Error listing orphaned positions: {e}",
        }


async def resolve_orphaned_position(client: Any, *, executor_id: str) -> dict[str, Any]:
    """Mark an orphaned position as recovered, after closing it externally."""
    try:
        resp = await client.executors.session.post(
            f"{client.executors.base_url}/executors/{executor_id}/resolve-orphan",
        )
        resp.raise_for_status()
        result = await resp.json()

        return {
            "action": "resolve_orphan",
            "executor_id": executor_id,
            "result": result,
            "formatted_output": (
                f"Orphaned position for executor {executor_id} marked recovered. "
                "It will no longer appear in orphan listings or warnings."
            ),
        }

    except Exception as e:
        return {
            "action": "resolve_orphan",
            "error": str(e),
            "formatted_output": (
                f"Error resolving orphan for executor {executor_id}: {e}"
            ),
        }


async def list_positions_held(
    client: Any,
    *,
    connector_name: str | None = None,
    trading_pair: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
) -> dict[str, Any]:
    """Positions held, in summary or for one connector/pair."""
    try:
        if connector_name and trading_pair:
            account = account_name or "master_account"
            result = await client.executors.get_position_held(
                connector_name=connector_name,
                trading_pair=trading_pair,
                account_name=account,
                controller_id=controller_id,
            )

            formatted = "Position Details\n\n"
            formatted += f"Connector: {connector_name}\n"
            formatted += f"Trading Pair: {trading_pair}\n"
            formatted += f"Account: {account}\n\n"

            if result:
                positions = [result] if not isinstance(result, list) else result
                formatted += format_positions_held_table(positions)
            else:
                formatted += "No position found for this connector/pair combination."

            return {
                "action": "positions_summary",
                "connector_name": connector_name,
                "trading_pair": trading_pair,
                "account": account,
                "position": result,
                "formatted_output": formatted,
            }

        result = await client.executors.get_positions_summary(
            controller_id=controller_id,
        )

        positions = (
            result.get("positions", result) if isinstance(result, dict) else result
        )
        if not isinstance(positions, list):
            positions = [positions] if positions else []

        formatted = "Positions Held Summary\n\n"

        if isinstance(result, dict) and any(
            k in result for k in ["total_positions", "total_value", "by_connector"]
        ):
            formatted += format_positions_summary(result)
            if positions:
                formatted += "\n\nPositions Detail:\n"
                formatted += format_positions_held_table(positions)
        else:
            formatted += format_positions_held_table(positions)

        return {
            "action": "positions_summary",
            "positions": positions,
            "summary": (
                result if isinstance(result, dict) else {"positions": positions}
            ),
            "formatted_output": formatted,
        }

    except Exception as e:
        return {
            "action": "positions_summary",
            "error": str(e),
            "formatted_output": f"Error getting positions: {e}",
        }


async def clear_position_held(
    client: Any,
    *,
    connector_name: str,
    trading_pair: str,
    account_name: str | None = None,
    controller_id: str | None = None,
) -> dict[str, Any]:
    """Clear a position that was closed outside the bot."""
    account = account_name or "master_account"
    try:
        result = await client.executors.clear_position_held(
            connector_name=connector_name,
            trading_pair=trading_pair,
            account_name=account,
            controller_id=controller_id,
        )

        formatted = "Position cleared successfully!\n\n"
        formatted += f"Connector: {connector_name}\n"
        formatted += f"Trading Pair: {trading_pair}\n"
        formatted += f"Account: {account}\n"

        return {
            "action": "clear_position",
            "connector_name": connector_name,
            "trading_pair": trading_pair,
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


async def get_performance_report(
    client: Any, *, controller_id: str | None = None
) -> dict[str, Any]:
    """Aggregate executor performance, optionally for one controller."""
    try:
        result = await client.executors.get_performance_report(
            controller_id=controller_id,
        )
        formatted = "Executor Performance Report\n\n"
        if controller_id:
            formatted += f"Controller: {controller_id}\n\n"
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


async def executor_defaults(
    *,
    action: str,
    content: str | None = None,
) -> dict[str, Any]:
    """Read, replace or reset the saved executor defaults file.

    Local file work — it takes no client. The defaults are what every ``create_*``
    tool merges underneath the arguments it was actually given.
    """
    if action == "get":
        raw_content = executor_preferences.get_raw_content()
        formatted = (
            f"Preferences file: {executor_preferences.get_preferences_path()}\n\n"
        )
        formatted += raw_content
        return {
            "action": "get",
            "raw_content": raw_content,
            "preferences_path": executor_preferences.get_preferences_path(),
            "formatted_output": formatted,
        }

    if action == "save":
        if not content:
            return {
                "action": "save",
                "error": "content is required to save the defaults file",
                "formatted_output": (
                    "Error: executor_defaults(action='save') needs the complete "
                    "markdown content. Read the current file with action='get' first."
                ),
            }
        executor_preferences.save_content(content)
        return {
            "action": "save",
            "preferences_path": executor_preferences.get_preferences_path(),
            "formatted_output": (
                "Preferences file saved successfully.\n\n"
                f"Preferences file: {executor_preferences.get_preferences_path()}"
            ),
        }

    if action == "reset":
        preserved = executor_preferences.reset_to_defaults()
        preserved_count = sum(1 for c in preserved.values() if c)

        formatted = "Preferences documentation updated to latest version.\n\n"
        if preserved_count > 0:
            preserved_names = [k for k, v in preserved.items() if v]
            formatted += (
                f"Preserved {preserved_count} config(s): {', '.join(preserved_names)}\n"
            )
        else:
            formatted += "No existing configs to preserve.\n"
        formatted += (
            f"\nPreferences file: {executor_preferences.get_preferences_path()}"
        )

        return {
            "action": "reset",
            "preserved_configs": preserved,
            "preserved_count": preserved_count,
            "formatted_output": formatted,
        }

    return {
        "action": action,
        "error": f"Unknown action: {action}",
        "formatted_output": f"Error: Unknown action: {action}",
    }

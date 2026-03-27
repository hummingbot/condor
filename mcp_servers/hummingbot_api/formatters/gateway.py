"""
Gateway formatters for the Hummingbot MCP server.
"""
from typing import Any


def format_gateway_container_result(result: dict[str, Any]) -> str:
    """Format gateway container action results into a human-readable string."""
    result_action = result.get("action", "")

    if result_action == "get_status":
        status = result.get("status", {})
        running = status.get("running", False)
        container_id = status.get('container_id')
        created_at = status.get('created_at')

        container_id_display = f"{container_id[:12]}..." if container_id else "None"
        created_at_display = created_at[:19] if created_at else "None"

        return (
            f"Gateway Container Status:\n\n"
            f"Status: {'Running ✓' if running else 'Stopped ✗'}\n"
            f"Container ID: {container_id_display}\n"
            f"Image: {status.get('image') or 'None'}\n"
            f"Port: {status.get('port') or 'None'}\n"
            f"Created: {created_at_display}"
        )

    elif result_action == "get_logs":
        logs = result.get("logs", "No logs available")
        return f"Gateway Container Logs:\n\n{logs}"

    elif result_action in ["start", "stop", "restart"]:
        message = result.get("message", "")
        return f"Gateway Container: {message}"

    return f"Gateway Container Result: {result}"


def format_gateway_config_result(result: dict[str, Any]) -> str:
    """Format gateway config action results into a human-readable string."""
    result_resource_type = result.get("resource_type", "")
    result_action = result.get("action", "")

    if result_action == "list":
        if result_resource_type == "chains":
            chains = result.get("result", {}).get("chains", [])
            output = "Available Chains:\n\n"
            for chain_info in chains:
                chain_name = chain_info.get("chain", "")
                networks = chain_info.get("networks", [])
                output += f"- {chain_name}: {', '.join(networks)}\n"
            return output

        elif result_resource_type == "networks":
            networks = result.get("result", {}).get("networks", [])
            count = result.get("result", {}).get("count", len(networks))
            output = f"Available Networks ({count} total):\n\n"
            for net in networks:
                output += f"- {net.get('network_id', 'N/A')}\n"
            return output

        elif result_resource_type == "connectors":
            connectors = result.get("result", {}).get("connectors", [])
            output = f"Available DEX Connectors ({len(connectors)} total):\n\n"
            for conn in connectors:
                if isinstance(conn, dict):
                    name = conn.get("name", "unknown")
                    trading_types = ", ".join(conn.get("trading_types", []))
                    chain_name = conn.get("chain", "")
                    output += f"- {name} ({chain_name}): {trading_types}\n"
                else:
                    output += f"- {conn}\n"
            return output

        elif result_resource_type == "tokens":
            tokens = result.get("result", {}).get("tokens", [])
            result_network_id = result.get("result", {}).get("network_id", "")
            output = f"Tokens on {result_network_id} ({len(tokens)} total):\n\n"
            output += "symbol   | address\n"
            output += "-" * 50 + "\n"
            for token in tokens[:20]:
                symbol = token.get("symbol", "")[:8]
                address = token.get("address", "")
                if len(address) > 20:
                    address = f"{address[:8]}...{address[-6:]}"
                output += f"{symbol:8} | {address}\n"
            if len(tokens) > 20:
                output += f"... and {len(tokens) - 20} more tokens\n"
            return output

        elif result_resource_type == "wallets":
            wallets = result.get("result", {}).get("wallets", [])
            output = f"Configured Wallets ({len(wallets)} total):\n\n"
            for wallet in wallets:
                chain_name = wallet.get("chain", "")
                address = wallet.get("address", "")
                if len(address) > 20:
                    address = f"{address[:10]}...{address[-8:]}"
                output += f"- {chain_name}: {address}\n"
            return output

    elif result_action in ["add", "delete", "update"]:
        message = result.get("result", {}).get("message", "")
        return f"Gateway Config {result_action.title()}: {message}"

    elif result_action == "get":
        return f"Gateway Configuration:\n{result.get('result', {})}"

    return f"Gateway Configuration Result: {result}"


def format_gateway_swap_result(action: str, result: dict[str, Any]) -> str:
    """Format gateway swap action results into a human-readable string."""
    if action == "search" and isinstance(result, dict):
        filters = result.get("filters", {})
        pagination = result.get("pagination", {})
        swaps = result.get("result", {}).get("data", [])

        return (
            f"Gateway Swaps Search Result:\n"
            f"Total Swaps Found: {len(swaps)}\n"
            f"Limit: {pagination.get('limit', 'N/A')}, Offset: {pagination.get('offset', 'N/A')}\n"
            f"Filters: {filters if filters else 'None'}\n\n"
            f"Swaps: {swaps}"
        )

    return f"Gateway Swap Result: {result}"


def format_gateway_clmm_pool_result(action: str, result: dict[str, Any]) -> str:
    """Format gateway CLMM pool exploration results into a human-readable string."""
    if action == "list_pools" and "pools_table" in result:
        return (
            f"Gateway CLMM Pool Exploration Result:\n"
            f"Connector: {result['connector']}\n"
            f"Total Pools: {result['pagination']['total']}\n"
            f"Page: {result['pagination']['page']}, Limit: {result['pagination']['limit']}\n"
            f"Filters: {result['filters']}\n\n"
            f"{result['pools_table']}"
        )

    return f"Gateway CLMM Pool Exploration Result: {result}"

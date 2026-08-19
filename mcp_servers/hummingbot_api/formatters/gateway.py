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
        container_id = status.get("container_id")
        created_at = status.get("created_at")

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


def format_amm_result(action: str, result: dict[str, Any]) -> str:
    """Format manage_amm results into a human-readable string."""
    # Progressive disclosure: the guide is returned directly.
    if action is None or result.get("action") is None:
        return result.get("formatted_output", str(result))

    connector = result.get("connector", "")
    network = result.get("network", "")
    payload = result.get("result", {})
    header = f"AMM {action} [{connector} · {network}]"

    if action == "pool_info" and isinstance(payload, dict):
        return (
            f"{header}\n"
            f"Pool: {payload.get('address')}\n"
            f"Price: {payload.get('price')} (quote per base)\n"
            f"Base: {payload.get('base_token_amount')}  Quote: {payload.get('quote_token_amount')}\n"
            f"Fee: {payload.get('fee_pct')}%"
        )

    if action == "position_info" and isinstance(payload, dict):
        positions = payload.get("positions") or []
        lines = [
            f"{header}",
            f"Pool: {payload.get('pool_address')}  Wallet: {payload.get('wallet_address')}",
            f"Aggregate — LP: {payload.get('lp_token_amount')}  "
            f"Base: {payload.get('base_token_amount')}  Quote: {payload.get('quote_token_amount')}",
        ]
        if positions:
            lines.append(f"Positions ({len(positions)}):")
            for p in positions:
                lines.append(
                    f"  • {p.get('position_address')} — LP: {p.get('lp_token_amount')}  "
                    f"Base: {p.get('base_token_amount')}  Quote: {p.get('quote_token_amount')}"
                )
        return "\n".join(lines)

    if action == "positions_owned" and isinstance(payload, list):
        lines = [f"{header} — {len(payload)} pool(s) with positions"]
        for pi in payload:
            positions = pi.get("positions") or []
            lines.append(
                f"  Pool {pi.get('pool_address')}: {len(positions)} position(s), "
                f"aggregate base={pi.get('base_token_amount')} quote={pi.get('quote_token_amount')}"
            )
            for p in positions:
                lines.append(
                    f"    • {p.get('position_address')} — LP: {p.get('lp_token_amount')}  "
                    f"Base: {p.get('base_token_amount')}  Quote: {p.get('quote_token_amount')}"
                )
        return "\n".join(lines)

    if action == "quote_liquidity" and isinstance(payload, dict):
        return f"{header}\n{payload}"

    if action in (
        "add_liquidity",
        "remove_liquidity",
        "create_pool",
    ) and isinstance(payload, dict):
        # AMM write responses are chain-neutral: the tx identifier is
        # `signature` (AMMTransactionResponse / AMMCreatePoolResponse), not
        # `transaction_hash` as on the CLMM surface.
        tx = payload.get("signature")
        extra = ""
        if action == "create_pool":
            extra = f"\nPool: {payload.get('pool_address')}  Seed price: {payload.get('price')}"
        return f"{header}\nTx: {tx}  Status: {payload.get('status')}{extra}"

    return f"{header}\n{payload}"


def format_clmm_result(action: str, result: dict[str, Any]) -> str:
    """Format manage_clmm results into a human-readable string."""
    # Progressive disclosure: the guide is returned directly.
    if action is None or result.get("action") is None:
        return result.get("formatted_output", str(result))

    connector = result.get("connector", "")
    network = result.get("network", "")
    payload = result.get("result", {})
    header = f"CLMM {action} [{connector} · {network}]"

    if action == "position_info" and isinstance(payload, list):
        if not payload:
            return f"{header}\nNo open positions for this wallet on this connector."
        lines = [f"{header}", f"{len(payload)} position(s) owned by this wallet"]
        for p in payload:
            lines.append(
                f"  • {p.get('position_address')} — Pool: {p.get('pool_address')}"
            )
            lines.append(
                f"    Base: {p.get('base_token_amount')}  "
                f"Quote: {p.get('quote_token_amount')}  "
                f"Range: {p.get('lower_price')}–{p.get('upper_price')}  "
                f"Price: {p.get('current_price')}"
            )
            lines.append(
                f"    Uncollected fees — base: {p.get('base_fee_amount')}  "
                f"quote: {p.get('quote_fee_amount')}"
            )
        return "\n".join(lines)

    if action == "open" and isinstance(payload, dict):
        # A submitted-not-confirmed open has position_address=None: the address
        # is unknowable until the tx lands, so say that instead of "None".
        position = (
            payload.get("position_address")
            or "pending — known once the transaction confirms"
        )
        return (
            f"{header}\n"
            f"Position: {position}\n"
            f"Tx: {payload.get('transaction_hash')}  Status: {payload.get('status')}\n"
            f"Range: {payload.get('lower_price')}–{payload.get('upper_price')}\n"
            "This position is NOT tracked by any executor — it will not be range-monitored, "
            "rebalanced, or auto-closed."
        )

    if action in ("close", "collect_fees") and isinstance(payload, dict):
        return (
            f"{header}\n"
            f"Position: {payload.get('position_address')}\n"
            f"Tx: {payload.get('transaction_hash')}  Status: {payload.get('status')}\n"
            f"Fees collected — base: {payload.get('base_fee_collected')}  "
            f"quote: {payload.get('quote_fee_collected')}"
        )

    if action in ("add_liquidity", "remove_liquidity") and isinstance(payload, dict):
        tx = payload.get("transaction_hash")
        return (
            f"{header}\n"
            f"Position: {result.get('position_address')}\n"
            f"Tx: {tx}  Status: {payload.get('status')}"
        )

    if action == "create_pool" and isinstance(payload, dict):
        return (
            f"{header}\n"
            f"Pool: {payload.get('pool_address')}\n"
            f"Tx: {payload.get('transaction_hash')}  Status: {payload.get('status')}\n"
            "The pool is created empty — add liquidity by opening a position "
            "(action='open' or manage_executors lp_executor)."
        )

    return f"{header}\n{payload}"

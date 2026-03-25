"""
Account management formatters for the Hummingbot MCP server.
"""
from typing import Any


def format_connector_result(result: dict[str, Any]) -> str:
    """Format the result from setup_connector/delete_connector into a human-readable string."""
    result_action = result.get("action", "")

    if result_action == "list_connectors":
        connectors = result.get("connectors", [])
        connector_lines = []
        for i in range(0, len(connectors), 4):
            line = "  ".join(f"{c:25}" for c in connectors[i:i+4])
            connector_lines.append(line)

        return (
            f"Available Exchange Connectors ({result.get('total_connectors', 0)} total):\n\n"
            + "\n".join(connector_lines) + "\n\n"
            f"{result.get('current_accounts', '')}\n\n"
            f"Next Step: {result.get('next_step', '')}\n"
            f"Example: {result.get('example', '')}"
        )

    elif result_action == "show_config_map":
        fields = result.get("required_fields", [])
        return (
            f"Required Credentials for {result.get('connector', '')}:\n\n"
            f"Fields needed:\n" + "\n".join(f"  - {field}" for field in fields) + "\n\n"
            f"Next Step: {result.get('next_step', '')}\n"
            f"Example: {result.get('example', '')}"
        )

    elif result_action == "select_account":
        accounts = result.get("accounts", [])
        return (
            f"{result.get('message', '')}\n\n"
            f"Available Accounts:\n" + "\n".join(f"  - {acc}" for acc in accounts) + "\n\n"
            f"Default Account: {result.get('default_account', '')}\n\n"
            f"Next Step: {result.get('next_step', '')}\n"
            f"Example: {result.get('example', '')}"
        )

    elif result_action == "requires_confirmation":
        return (
            f"\u26a0\ufe0f  {result.get('message', '')}\n\n"
            f"Account: {result.get('account', '')}\n"
            f"Connector: {result.get('connector', '')}\n"
            f"Warning: {result.get('warning', '')}\n\n"
            f"Next Step: {result.get('next_step', '')}\n"
            f"Example: {result.get('example', '')}"
        )

    elif result_action == "override_rejected":
        return (
            f"\u274c {result.get('message', '')}\n\n"
            f"Account: {result.get('account', '')}\n"
            f"Connector: {result.get('connector', '')}\n\n"
            f"Next Step: {result.get('next_step', '')}"
        )

    elif result_action in ["credentials_added", "credentials_overridden"]:
        return (
            f"\u2705 {result.get('message', '')}\n\n"
            f"Account: {result.get('account', '')}\n"
            f"Connector: {result.get('connector', '')}\n"
            f"Credentials Count: {result.get('credentials_count', 0)}\n"
            f"Was Existing: {result.get('was_existing', False)}\n\n"
            f"Next Step: {result.get('next_step', '')}"
        )

    # Delete flow responses
    elif result_action == "delete_list":
        account_connectors = result.get("account_connectors", {})
        lines = [result.get("message", "")]
        for acc, conns in account_connectors.items():
            conns_str = ", ".join(conns) if conns else "(none)"
            lines.append(f"  - {acc}: {conns_str}")
        lines.append("")
        lines.append(f"Next Step: {result.get('next_step', '')}")
        lines.append(f"Example: {result.get('example', '')}")
        return "\n".join(lines)

    elif result_action == "delete_select_account":
        accounts = result.get("accounts", [])
        return (
            f"{result.get('message', '')}\n\n"
            f"Accounts:\n" + "\n".join(f"  - {acc}" for acc in accounts) + "\n\n"
            f"Default Account: {result.get('default_account', '')}\n\n"
            f"Next Step: {result.get('next_step', '')}\n"
            f"Example: {result.get('example', '')}"
        )

    elif result_action == "delete_not_found":
        return (
            f"\u274c {result.get('message', '')}\n\n"
            f"Next Step: {result.get('next_step', '')}"
        )

    elif result_action == "credentials_deleted":
        return (
            f"\u2705 {result.get('message', '')}\n\n"
            f"Account: {result.get('account', '')}\n"
            f"Connector: {result.get('connector', '')}\n\n"
            f"Next Step: {result.get('next_step', '')}"
        )

    # Fallback for unknown actions
    return f"Connector Result: {result}"

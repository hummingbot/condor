"""
Account management tools for Hummingbot MCP Server
"""
import asyncio
import logging
from typing import Any

from hummingbot_mcp.exceptions import ToolError
from hummingbot_mcp.schemas import SetupConnectorRequest
from hummingbot_mcp.settings import settings

logger = logging.getLogger("hummingbot-mcp")


async def _check_existing_connector(client: Any, account_name: str, connector_name: str) -> bool:
    """Check if a connector already exists for the given account"""
    try:
        credentials = await client.accounts.list_account_credentials(account_name=account_name)
        return connector_name in credentials
    except Exception as e:
        logger.warning(f"Failed to check existing connector: {str(e)}")
        return False


async def setup_connector(client: Any, request: SetupConnectorRequest) -> dict[str, Any]:
    """Setup or delete an exchange connector with credentials using progressive disclosure.

    Setup flow:
    1. No connector -> List available exchanges
    2. Connector only -> Show required credential fields
    3. Connector + credentials, no account -> Select account from available accounts
    4. All parameters -> Connect the exchange (with override confirmation if needed)

    Delete flow:
    1. action="delete" only -> List accounts and their configured connectors
    2. action="delete" + connector -> Show which accounts have this connector
    3. action="delete" + connector + account -> Delete the credential
    """
    flow_stage = request.get_flow_stage()

    # ============================
    # Delete Flow
    # ============================

    if flow_stage == "delete_list":
        # List all accounts and their configured connectors
        accounts = await client.accounts.list_accounts()
        credentials_tasks = [
            client.accounts.list_account_credentials(account_name=account_name)
            for account_name in accounts
        ]
        credentials = await asyncio.gather(*credentials_tasks)

        account_connectors = {}
        for account, creds in zip(accounts, credentials):
            account_connectors[account] = creds if creds else []

        return {
            "action": "delete_list",
            "message": "Configured connectors by account:",
            "account_connectors": account_connectors,
            "next_step": "Call again with action='delete' and 'connector' to select which connector to remove",
            "example": "Use action='delete', connector='binance' to remove Binance credentials",
        }

    elif flow_stage == "delete_select_account":
        # Show which accounts have this connector configured
        accounts = await client.accounts.list_accounts()
        credentials_tasks = [
            client.accounts.list_account_credentials(account_name=account_name)
            for account_name in accounts
        ]
        credentials = await asyncio.gather(*credentials_tasks)

        matching_accounts = []
        for account, creds in zip(accounts, credentials):
            if request.connector in (creds or []):
                matching_accounts.append(account)

        if not matching_accounts:
            return {
                "action": "delete_not_found",
                "message": f"Connector '{request.connector}' is not configured on any account",
                "connector": request.connector,
                "next_step": "Use action='delete' without a connector to see all configured connectors",
            }

        return {
            "action": "delete_select_account",
            "message": f"Connector '{request.connector}' is configured on the following accounts:",
            "connector": request.connector,
            "accounts": matching_accounts,
            "default_account": settings.default_account,
            "next_step": "Call again with 'account' to specify which account to delete from",
            "example": f"Use action='delete', connector='{request.connector}', "
                       f"account='{matching_accounts[0]}' to delete",
        }

    elif flow_stage == "delete":
        # Actually delete the credential
        account_name = request.get_account_name()

        # Verify the connector exists before deleting
        connector_exists = await _check_existing_connector(client, account_name, request.connector)
        if not connector_exists:
            return {
                "action": "delete_not_found",
                "message": f"Connector '{request.connector}' is not configured on account '{account_name}'",
                "account": account_name,
                "connector": request.connector,
                "next_step": "Use action='delete' without parameters to see all configured connectors",
            }

        try:
            await client.accounts.delete_credential(
                account_name=account_name,
                connector_name=request.connector,
            )

            return {
                "action": "credentials_deleted",
                "message": f"Successfully deleted {request.connector} credentials from account {account_name}",
                "account": account_name,
                "connector": request.connector,
                "next_step": "Use setup_connector() to see remaining configured connectors",
            }
        except Exception as e:
            raise ToolError(f"Failed to delete credentials for {request.connector}: {str(e)}")

    # ============================
    # Setup Flow
    # ============================

    elif flow_stage == "select_account":
        # Step 2.5: List available accounts for selection (after connector and credentials are provided)
        accounts = await client.accounts.list_accounts()

        return {
            "action": "select_account",
            "message": f"Ready to connect {request.connector}. Please select an account:",
            "connector": request.connector,
            "accounts": accounts,
            "default_account": settings.default_account,
            "next_step": "Call again with 'account' parameter to specify which account to use",
            "example": f"Use account='{settings.default_account}' to use the default account, or choose from "
            f"the available accounts above",
        }

    elif flow_stage == "list_exchanges":
        # Step 1: List available connectors
        connectors = await client.connectors.list_connectors()

        # Handle both string and object responses from the API
        connector_names = []
        for c in connectors:
            if isinstance(c, str):
                connector_names.append(c)
            elif hasattr(c, "name"):
                connector_names.append(c.name)
            else:
                connector_names.append(str(c))
        current_accounts_str = "Current accounts: "
        accounts = await client.accounts.list_accounts()
        credentials_tasks = [client.accounts.list_account_credentials(account_name=account_name) for account_name in accounts]
        credentials = await asyncio.gather(*credentials_tasks)
        for account, creds in zip(accounts, credentials):
            current_accounts_str += f"{account}: {creds}), "

        return {
            "action": "list_connectors",
            "message": "Available exchange connectors:",
            "connectors": connector_names,
            "total_connectors": len(connector_names),
            "current_accounts": current_accounts_str.strip(", "),
            "next_step": "Call again with 'connector' parameter to see required credentials for a specific exchange",
            "example": "Use connector='binance' to see Binance setup requirements",
        }

    elif flow_stage == "show_config":
        # Step 2: Show required credential fields for the connector
        try:
            config_fields = await client.connectors.get_config_map(request.connector)

            # Build a dictionary from the list of field names
            credentials_dict = {field: f"your_{field}" for field in config_fields}

            return {
                "action": "show_config_map",
                "connector": request.connector,
                "required_fields": config_fields,
                "next_step": "Call again with 'credentials' parameter containing the required fields",
                "example": f"Use credentials={credentials_dict} to connect",
            }
        except Exception as e:
            raise ToolError(f"Failed to get configuration for connector '{request.connector}': {str(e)}")

    elif flow_stage == "connect":
        # Step 3: Actually connect the exchange with provided credentials
        account_name = request.get_account_name()

        # Check if connector already exists
        connector_exists = await _check_existing_connector(client, account_name, request.connector)

        if connector_exists and request.requires_override_confirmation():
            return {
                "action": "requires_confirmation",
                "message": f"WARNING: Connector '{request.connector}' already exists for account '{account_name}'",
                "account": account_name,
                "connector": request.connector,
                "warning": "Adding credentials will override the existing connector configuration",
                "next_step": "To proceed with overriding, add 'confirm_override': true to your request",
                "example": "Use confirm_override=true along with your credentials to override the existing connector",
            }

        if connector_exists and not request.confirm_override:
            return {
                "action": "override_rejected",
                "message": f"Cannot override existing connector {request.connector} without explicit confirmation",
                "account": account_name,
                "connector": request.connector,
                "next_step": "Set confirm_override=true to override the existing connector",
            }

        # Remove force_override from credentials before sending to API
        credentials_to_send = dict(request.credentials)
        if "force_override" in credentials_to_send:
            del credentials_to_send["force_override"]

        try:
            await client.accounts.add_credential(
                account_name=account_name, connector_name=request.connector, credentials=credentials_to_send
            )

            action_type = "credentials_overridden" if connector_exists else "credentials_added"
            message_action = "overridden" if connector_exists else "connected"

            return {
                "action": action_type,
                "message": f"Successfully {message_action} {request.connector} exchange to account {account_name}",
                "account": account_name,
                "connector": request.connector,
                "credentials_count": len(credentials_to_send),
                "was_existing": connector_exists,
                "next_step": "Exchange is now ready for trading. Use get_account_state to verify the connection.",
            }
        except Exception as e:
            raise ToolError(f"Failed to add credentials for {request.connector}: {str(e)}")

    else:
        raise ToolError(f"Unknown flow stage: {flow_stage}")

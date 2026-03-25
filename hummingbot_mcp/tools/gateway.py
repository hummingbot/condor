"""
Gateway management tools for Hummingbot MCP Server
"""
import logging
from typing import Any

from hummingbot_mcp.exceptions import ToolError
from hummingbot_mcp.schemas import GatewayConfigRequest, GatewayContainerRequest

logger = logging.getLogger("hummingbot-mcp")


async def manage_gateway_container(client: Any, request: GatewayContainerRequest) -> dict[str, Any]:
    """Manage Gateway container lifecycle operations.

    Supports:
    - get_status: Check Gateway container status
    - start: Start Gateway with configuration
    - stop: Stop Gateway container
    - restart: Restart Gateway (optionally with new config)
    - get_logs: Get container logs
    """
    if request.action == "get_status":
        result = await client.gateway.get_status()
        return {
            "action": "get_status",
            "status": result
        }

    elif request.action == "start":
        if not request.config:
            raise ToolError(
                "Configuration is required to start Gateway. "
                "Provide 'config' with at least 'image' and optionally 'port' and 'environment'."
            )

        result = await client.gateway.start(request.config)
        return {
            "action": "start",
            "message": "Gateway started successfully",
            "result": result
        }

    elif request.action == "stop":
        result = await client.gateway.stop()
        return {
            "action": "stop",
            "message": "Gateway stopped successfully",
            "result": result
        }

    elif request.action == "restart":
        result = await client.gateway.restart(request.config)
        return {
            "action": "restart",
            "message": "Gateway restarted successfully",
            "result": result,
            "config_updated": request.config is not None
        }

    elif request.action == "get_logs":
        result = await client.gateway.get_logs(tail=request.tail or 100)
        return {
            "action": "get_logs",
            "tail": request.tail or 100,
            "logs": result
        }

    else:
        raise ToolError(f"Unknown action: {request.action}")


async def manage_gateway_config(client: Any, request: GatewayConfigRequest) -> dict[str, Any]:
    """Manage Gateway configuration for chains, networks, tokens, connectors, pools, and wallets.

    Resource Types:
    - chains: Get all blockchain chains
    - networks: List/get/update network configurations (format: 'chain-network')
    - tokens: List/add/delete tokens per network
    - connectors: List/get/update DEX connector configurations
    - pools: List/add liquidity pools per connector/network
    - wallets: Add/delete wallets for blockchain chains
    """
    # ============================================
    # CHAINS
    # ============================================
    if request.resource_type == "chains":
        if request.action != "list":
            raise ToolError(f"Only 'list' action is supported for chains, got: {request.action}")

        result = await client.gateway.list_chains()
        return {
            "resource_type": "chains",
            "action": "list",
            "result": result
        }

    # ============================================
    # NETWORKS
    # ============================================
    elif request.resource_type == "networks":
        if request.action == "list":
            result = await client.gateway.list_networks()
            return {
                "resource_type": "networks",
                "action": "list",
                "result": result
            }

        elif request.action == "get":
            if not request.network_id:
                raise ToolError("network_id is required for 'get' network action")

            result = await client.gateway.get_network_config(request.network_id)
            return {
                "resource_type": "networks",
                "action": "get",
                "network_id": request.network_id,
                "result": result
            }

        elif request.action == "update":
            if not request.network_id:
                raise ToolError("network_id is required for 'update' network action")
            if not request.config_updates:
                raise ToolError("config_updates is required for 'update' network action")

            result = await client.gateway.update_network_config(
                request.network_id,
                request.config_updates
            )
            return {
                "resource_type": "networks",
                "action": "update",
                "network_id": request.network_id,
                "result": result
            }

        else:
            raise ToolError(
                f"Action '{request.action}' not supported for networks. "
                f"Supported: list, get, update"
            )

    # ============================================
    # TOKENS
    # ============================================
    elif request.resource_type == "tokens":
        if request.action == "list":
            if not request.network_id:
                raise ToolError("network_id is required for 'list' tokens action")

            result = await client.gateway.get_network_tokens(
                request.network_id,
                search=request.search
            )
            return {
                "resource_type": "tokens",
                "action": "list",
                "network_id": request.network_id,
                "search": request.search,
                "result": result
            }

        elif request.action == "add":
            if not request.network_id:
                raise ToolError("network_id is required for 'add' token action")
            if not request.token_address:
                raise ToolError("token_address is required for 'add' token action")
            if not request.token_symbol:
                raise ToolError("token_symbol is required for 'add' token action")
            if request.token_decimals is None:
                raise ToolError("token_decimals is required for 'add' token action")

            result = await client.gateway.add_token(
                network_id=request.network_id,
                address=request.token_address,
                symbol=request.token_symbol,
                decimals=request.token_decimals,
                name=request.token_name
            )
            return {
                "resource_type": "tokens",
                "action": "add",
                "network_id": request.network_id,
                "token": {
                    "address": request.token_address,
                    "symbol": request.token_symbol,
                    "decimals": request.token_decimals,
                    "name": request.token_name
                },
                "result": result
            }

        elif request.action == "delete":
            if not request.network_id:
                raise ToolError("network_id is required for 'delete' token action")
            if not request.token_address:
                raise ToolError("token_address is required for 'delete' token action")

            result = await client.gateway.delete_token(
                network_id=request.network_id,
                token_address=request.token_address
            )
            return {
                "resource_type": "tokens",
                "action": "delete",
                "network_id": request.network_id,
                "token_address": request.token_address,
                "result": result
            }

        else:
            raise ToolError(
                f"Action '{request.action}' not supported for tokens. "
                f"Supported: list, add, delete"
            )

    # ============================================
    # CONNECTORS
    # ============================================
    elif request.resource_type == "connectors":
        if request.action == "list":
            result = await client.gateway.list_connectors()
            return {
                "resource_type": "connectors",
                "action": "list",
                "result": result
            }

        elif request.action == "get":
            if not request.connector_name:
                raise ToolError("connector_name is required for 'get' connector action")

            result = await client.gateway.get_connector_config(request.connector_name)
            return {
                "resource_type": "connectors",
                "action": "get",
                "connector_name": request.connector_name,
                "result": result
            }

        elif request.action == "update":
            if not request.connector_name:
                raise ToolError("connector_name is required for 'update' connector action")
            if not request.config_updates:
                raise ToolError("config_updates is required for 'update' connector action")

            result = await client.gateway.update_connector_config(
                request.connector_name,
                request.config_updates
            )
            return {
                "resource_type": "connectors",
                "action": "update",
                "connector_name": request.connector_name,
                "result": result
            }

        else:
            raise ToolError(
                f"Action '{request.action}' not supported for connectors. "
                f"Supported: list, get, update"
            )

    # ============================================
    # POOLS
    # ============================================
    elif request.resource_type == "pools":
        if request.action == "list":
            if not request.connector_name:
                raise ToolError("connector_name is required for 'list' pools action")
            if not request.network:
                raise ToolError("network is required for 'list' pools action")

            result = await client.gateway.list_pools(
                request.connector_name,
                request.network
            )
            return {
                "resource_type": "pools",
                "action": "list",
                "connector_name": request.connector_name,
                "network": request.network,
                "result": result
            }

        elif request.action == "add":
            if not request.connector_name:
                raise ToolError("connector_name is required for 'add' pool action")
            if not request.pool_type:
                raise ToolError("pool_type is required for 'add' pool action")
            if not request.network:
                raise ToolError("network is required for 'add' pool action")
            if not request.pool_base:
                raise ToolError("pool_base is required for 'add' pool action")
            if not request.pool_quote:
                raise ToolError("pool_quote is required for 'add' pool action")
            if not request.pool_address:
                raise ToolError("pool_address is required for 'add' pool action")

            result = await client.gateway.add_pool(
                connector_name=request.connector_name,
                pool_type=request.pool_type,
                network=request.network,
                base=request.pool_base,
                quote=request.pool_quote,
                address=request.pool_address
            )
            return {
                "resource_type": "pools",
                "action": "add",
                "connector_name": request.connector_name,
                "network": request.network,
                "pool": {
                    "type": request.pool_type,
                    "base": request.pool_base,
                    "quote": request.pool_quote,
                    "address": request.pool_address
                },
                "result": result
            }

        else:
            raise ToolError(
                f"Action '{request.action}' not supported for pools. "
                f"Supported: list, add"
            )

    # ============================================
    # WALLETS
    # ============================================
    elif request.resource_type == "wallets":
        if request.action == "add":
            if not request.chain:
                raise ToolError("chain is required for 'add' wallet action")
            if not request.private_key:
                raise ToolError("private_key is required for 'add' wallet action")

            result = await client.accounts.add_gateway_wallet(
                chain=request.chain,
                private_key=request.private_key
            )
            return {
                "resource_type": "wallets",
                "action": "add",
                "chain": request.chain,
                "result": result
            }

        elif request.action == "delete":
            if not request.chain:
                raise ToolError("chain is required for 'delete' wallet action")
            if not request.wallet_address:
                raise ToolError("wallet_address is required for 'delete' wallet action")

            result = await client.accounts.remove_gateway_wallet(
                chain=request.chain,
                address=request.wallet_address
            )
            return {
                "resource_type": "wallets",
                "action": "delete",
                "chain": request.chain,
                "wallet_address": request.wallet_address,
                "result": result
            }

        else:
            raise ToolError(
                f"Action '{request.action}' not supported for wallets. "
                f"Supported: add, delete"
            )

    else:
        raise ToolError(f"Unknown resource type: {request.resource_type}")

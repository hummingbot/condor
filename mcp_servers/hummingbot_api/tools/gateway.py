"""
Gateway management tools for Hummingbot MCP Server
"""

import logging
from typing import Any

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.schemas import GatewayConfigRequest

logger = logging.getLogger("hummingbot-mcp")


async def manage_gateway_config(
    client: Any, request: GatewayConfigRequest
) -> dict[str, Any]:
    """Manage Gateway configuration for chains, networks, tokens, connectors, pools, and wallets.

    Resource Types:
    - chains: Get all blockchain chains
    - networks: List/get network configurations (format: 'chain-network'), read-only
    - tokens: List/add/delete tokens per network
    - connectors: List/get DEX connector configurations, read-only
    - pools: List/add liquidity pools per connector/network
    - wallets: List the configured wallets per chain (read-only)
    """
    # ============================================
    # CHAINS
    # ============================================
    if request.resource_type == "chains":
        if request.action != "list":
            raise ToolError(
                f"Only 'list' action is supported for chains, got: {request.action}"
            )

        result = await client.gateway.list_chains()
        return {"resource_type": "chains", "action": "list", "result": result}

    # ============================================
    # NETWORKS
    # ============================================
    elif request.resource_type == "networks":
        if request.action == "list":
            result = await client.gateway.list_networks()
            return {"resource_type": "networks", "action": "list", "result": result}

        elif request.action == "get":
            if not request.network_id:
                raise ToolError("network_id is required for 'get' network action")

            result = await client.gateway.get_network_config(request.network_id)
            return {
                "resource_type": "networks",
                "action": "get",
                "network_id": request.network_id,
                "result": result,
            }

        else:
            raise ToolError(
                f"Action '{request.action}' not supported for networks. "
                "Supported: list, get. A network's config, its RPC endpoint "
                "included, is changed in the Condor dashboard (Settings → Gateway)."
            )

    # ============================================
    # TOKENS
    # ============================================
    elif request.resource_type == "tokens":
        if request.action == "list":
            if not request.network_id:
                raise ToolError("network_id is required for 'list' tokens action")

            result = await client.gateway.get_network_tokens(
                request.network_id, search=request.search
            )
            return {
                "resource_type": "tokens",
                "action": "list",
                "network_id": request.network_id,
                "search": request.search,
                "result": result,
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
                name=request.token_name,
            )
            return {
                "resource_type": "tokens",
                "action": "add",
                "network_id": request.network_id,
                "token": {
                    "address": request.token_address,
                    "symbol": request.token_symbol,
                    "decimals": request.token_decimals,
                    "name": request.token_name,
                },
                "result": result,
            }

        elif request.action == "delete":
            if not request.network_id:
                raise ToolError("network_id is required for 'delete' token action")
            if not request.token_address:
                raise ToolError("token_address is required for 'delete' token action")

            result = await client.gateway.delete_token(
                network_id=request.network_id, token_address=request.token_address
            )
            return {
                "resource_type": "tokens",
                "action": "delete",
                "network_id": request.network_id,
                "token_address": request.token_address,
                "result": result,
            }

        elif request.action == "save":
            # Simplified token addition - auto-fetches info from GeckoTerminal
            if not request.network_id:
                raise ToolError(
                    "network_id is required for 'save' token action. "
                    "Format: 'chain-network' (e.g., 'solana-mainnet-beta')"
                )
            if not request.token_address:
                raise ToolError("token_address is required for 'save' token action")

            result = await client.gateway.save_network_token(
                network_id=request.network_id, token_address=request.token_address
            )
            return {
                "resource_type": "tokens",
                "action": "save",
                "network_id": request.network_id,
                "token_address": request.token_address,
                "result": result,
            }

        else:
            raise ToolError(
                f"Action '{request.action}' not supported for tokens. "
                f"Supported: list, add, delete, save"
            )

    # ============================================
    # CONNECTORS
    # ============================================
    elif request.resource_type == "connectors":
        if request.action == "list":
            result = await client.gateway.list_connectors()
            return {"resource_type": "connectors", "action": "list", "result": result}

        elif request.action == "get":
            if not request.connector_name:
                raise ToolError("connector_name is required for 'get' connector action")

            result = await client.gateway.get_connector_config(request.connector_name)
            return {
                "resource_type": "connectors",
                "action": "get",
                "connector_name": request.connector_name,
                "result": result,
            }

        else:
            raise ToolError(
                f"Action '{request.action}' not supported for connectors. "
                "Supported: list, get. A connector's settings are changed by the "
                "server owner in Condor, not over MCP."
            )

    # ============================================
    # POOLS
    # ============================================
    elif request.resource_type == "pools":
        if request.action == "list":
            if not request.network_id:
                raise ToolError(
                    "network_id is required for 'list' pools action. "
                    "Format: 'chain-network' (e.g., 'solana-mainnet-beta')"
                )

            result = await client.gateway.get_network_pools(
                network_id=request.network_id,
                connector=request.connector_name,  # Optional filter
                pool_type=request.pool_type,  # Optional filter
                search=request.search,  # Optional search
            )
            return {
                "resource_type": "pools",
                "action": "list",
                "network_id": request.network_id,
                "connector": request.connector_name,
                "result": result,
            }

        elif request.action == "add":
            if not request.network_id:
                raise ToolError(
                    "network_id is required for 'add' pool action. "
                    "Format: 'chain-network' (e.g., 'solana-mainnet-beta')"
                )
            if not request.connector_name:
                raise ToolError("connector_name is required for 'add' pool action")
            if not request.pool_type:
                raise ToolError("pool_type is required for 'add' pool action")
            if not request.pool_address:
                raise ToolError("pool_address is required for 'add' pool action")

            result = await client.gateway.add_network_pool(
                network_id=request.network_id,
                connector_name=request.connector_name,
                pool_type=request.pool_type,
                address=request.pool_address,
                base=request.pool_base,
                quote=request.pool_quote,
            )
            return {
                "resource_type": "pools",
                "action": "add",
                "network_id": request.network_id,
                "connector_name": request.connector_name,
                "pool": {
                    "type": request.pool_type,
                    "base": request.pool_base,
                    "quote": request.pool_quote,
                    "address": request.pool_address,
                },
                "result": result,
            }

        elif request.action == "delete":
            if not request.network_id:
                raise ToolError(
                    "network_id is required for 'delete' pool action. "
                    "Format: 'chain-network' (e.g., 'solana-mainnet-beta')"
                )
            if not request.pool_address:
                raise ToolError("pool_address is required for 'delete' pool action")

            result = await client.gateway.delete_network_pool(
                network_id=request.network_id,
                address=request.pool_address,
                pool_type=request.pool_type,
            )
            return {
                "resource_type": "pools",
                "action": "delete",
                "network_id": request.network_id,
                "pool_address": request.pool_address,
                "result": result,
            }

        elif request.action == "save":
            # Simplified pool addition - auto-fetches info from blockchain
            if not request.network_id:
                raise ToolError(
                    "network_id is required for 'save' pool action. "
                    "Format: 'chain-network' (e.g., 'solana-mainnet-beta')"
                )
            if not request.pool_address:
                raise ToolError("pool_address is required for 'save' pool action")

            result = await client.gateway.save_network_pool(
                network_id=request.network_id, pool_address=request.pool_address
            )
            return {
                "resource_type": "pools",
                "action": "save",
                "network_id": request.network_id,
                "pool_address": request.pool_address,
                "result": result,
            }

        else:
            raise ToolError(
                f"Action '{request.action}' not supported for pools. "
                f"Supported: list, add, delete, save"
            )

    # ============================================
    # WALLETS (read-only)
    # ============================================
    # A wallet is added or removed in the Condor dashboard, never here: `add` took a
    # PRIVATE KEY, and a key typed at an agent is persisted by the chat transport, by
    # the bot's own state and by every transcript that session writes. No confirmation
    # gate un-leaks it, so the parameter does not exist. Exchange API keys are kept off
    # this surface for the same reason (see server.py).
    elif request.resource_type == "wallets":
        if request.action != "list":
            raise ToolError(
                f"Action '{request.action}' not supported for wallets. "
                f"Supported: list. Wallets are added and removed in the Condor "
                f"dashboard (Settings -> Gateway) — a private key must never be "
                f"sent through chat."
            )

        # The API answers with one group per chain; flatten it to one row per wallet.
        groups = await client.accounts.list_gateway_wallets()
        wallets = [
            {"chain": group.get("chain", "unknown"), "address": address}
            for group in (groups or [])
            if isinstance(group, dict)
            for address in group.get("walletAddresses", [])
            if not request.chain or group.get("chain") == request.chain
        ]
        return {
            "resource_type": "wallets",
            "action": "list",
            "result": {"wallets": wallets},
        }

    else:
        raise ToolError(f"Unknown resource type: {request.resource_type}")

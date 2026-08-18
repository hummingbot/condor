"""
manage_amm implementation — a direct, chain- & DEX-agnostic AMM tool.

Drives AMM operations and pool creation across Meteora DAMM v2 (Solana), Raydium CPMM (Solana),
and Uniswap V2 (EVM) via the SDK's client.gateway_amm.* router. Stateless — the agent holds
position state in its journal.

Meteora DAMM v2 positions are NFTs, so this tool is position-addressed: remove_liquidity requires
position_address, add_liquidity takes it optionally (omit = new position), position_info returns a
positions[] breakdown, and positions_owned lists all of a wallet's positions. Fungible-LP AMMs
(raydium, uniswap) ignore position_address and have no enumerable positions.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.schemas import AMMRequest

SUPPORTED_CONNECTORS = {"meteora", "raydium", "uniswap"}
# Non-fungible-LP (NFT position) connectors — position-addressed and enumerable.
NFT_POSITION_CONNECTORS = {"meteora"}


def _guide() -> str:
    guide_file = Path(__file__).parent.parent / "guides" / "gateway_amm.md"
    if guide_file.exists():
        return guide_file.read_text().strip()
    raise ToolError("AMM guide not found (guides/gateway_amm.md)")


def _dec(value: str | None, name: str) -> Decimal:
    if value is None:
        raise ToolError(f"{name} is required for this action")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ToolError(f"{name} must be a number, got {value!r}") from exc


def _opt_dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _require(request: AMMRequest, *fields: str) -> None:
    for f in fields:
        if getattr(request, f) is None:
            raise ToolError(f"{f} is required for the '{request.action}' action")


async def manage_amm_impl(client: Any, request: AMMRequest) -> dict[str, Any]:
    """Validate per-(connector, action) and dispatch to client.gateway_amm.*."""
    # Progressive disclosure: no action → load the guide.
    if request.action is None:
        return {"action": None, "formatted_output": _guide()}

    # Every action needs a supported connector.
    if not request.connector:
        raise ToolError(
            "connector is required (meteora | raydium | uniswap). "
            "Call manage_amm with no action to load the guide."
        )
    connector = request.connector.lower()
    if connector not in SUPPORTED_CONNECTORS:
        raise ToolError(
            f"Unsupported AMM connector '{request.connector}'. Supported: {', '.join(sorted(SUPPORTED_CONNECTORS))}"
        )
    if not request.network:
        raise ToolError(
            "network is required (e.g. 'solana-mainnet-beta', 'ethereum-mainnet')"
        )

    net = request.network
    action = request.action
    # Dereferenced only after per-action validation so guard errors never depend on the client.
    ga = None if client is None else client.gateway_amm

    if action == "pool_info":
        _require(request, "pool_address")
        result = await ga.get_pool_info(
            connector=connector, network=net, pool_address=request.pool_address
        )

    elif action == "position_info":
        _require(request, "pool_address")
        result = await ga.get_position_info(
            connector=connector,
            network=net,
            pool_address=request.pool_address,
            wallet_address=request.wallet_address,
        )

    elif action == "positions_owned":
        if connector not in NFT_POSITION_CONNECTORS:
            raise ToolError(
                f"positions_owned is not supported for '{connector}': fungible-LP AMMs have no enumerable "
                "positions. Use position_info with a specific pool_address instead."
            )
        result = await ga.get_positions_owned(
            connector=connector, network=net, wallet_address=request.wallet_address
        )

    elif action == "quote_swap":
        _require(request, "pool_address", "base_token", "side", "amount")
        result = await ga.get_swap_quote(
            connector=connector,
            network=net,
            pool_address=request.pool_address,
            base_token=request.base_token,
            side=request.side,
            amount=_dec(request.amount, "amount"),
            slippage_pct=_opt_dec(request.slippage_pct),
        )

    elif action == "execute_swap":
        _require(request, "pool_address", "base_token", "side", "amount")
        result = await ga.execute_swap(
            connector=connector,
            network=net,
            pool_address=request.pool_address,
            base_token=request.base_token,
            side=request.side,
            amount=_dec(request.amount, "amount"),
            slippage_pct=_opt_dec(request.slippage_pct),
            wallet_address=request.wallet_address,
        )

    elif action == "quote_liquidity":
        _require(request, "pool_address", "base_token_amount", "quote_token_amount")
        result = await ga.get_liquidity_quote(
            connector=connector,
            network=net,
            pool_address=request.pool_address,
            base_token_amount=_dec(request.base_token_amount, "base_token_amount"),
            quote_token_amount=_dec(request.quote_token_amount, "quote_token_amount"),
            slippage_pct=_opt_dec(request.slippage_pct),
        )

    elif action == "add_liquidity":
        _require(request, "pool_address", "base_token_amount", "quote_token_amount")
        # Meteora: position_address optional (omit = new position). Fungible-LP: ignored.
        result = await ga.add_liquidity(
            connector=connector,
            network=net,
            pool_address=request.pool_address,
            base_token_amount=_dec(request.base_token_amount, "base_token_amount"),
            quote_token_amount=_dec(request.quote_token_amount, "quote_token_amount"),
            slippage_pct=_opt_dec(request.slippage_pct),
            wallet_address=request.wallet_address,
            position_address=request.position_address,
        )

    elif action == "remove_liquidity":
        _require(request, "pool_address", "percentage_to_remove")
        # DAMM v2 positions are NFTs — fail fast rather than letting Gateway 400.
        if connector in NFT_POSITION_CONNECTORS and not request.position_address:
            raise ToolError(
                f"position_address is required for {connector} remove_liquidity: DAMM v2 positions are NFTs "
                "and a wallet may hold several per pool. List them with position_info or positions_owned."
            )
        result = await ga.remove_liquidity(
            connector=connector,
            network=net,
            pool_address=request.pool_address,
            percentage_to_remove=_dec(
                request.percentage_to_remove, "percentage_to_remove"
            ),
            position_address=request.position_address,
            slippage_pct=_opt_dec(request.slippage_pct),
            wallet_address=request.wallet_address,
        )

    elif action == "create_pool":
        _require(request, "base_token", "quote_token", "base_token_amount")
        if connector == "meteora" and not request.config_address:
            raise ToolError(
                "config_address is required for meteora create_pool (DAMM v2 pools are created against a "
                "config account that fixes the fee schedule). Discover configs via the Meteora app/SDK; "
                "avoid token-launch configs whose base fee starts near 99%."
            )
        result = await ga.create_pool(
            connector=connector,
            network=net,
            base_token=request.base_token,
            quote_token=request.quote_token,
            base_token_amount=_dec(request.base_token_amount, "base_token_amount"),
            quote_token_amount=_opt_dec(request.quote_token_amount),
            initial_price=_opt_dec(request.initial_price),
            config_address=request.config_address,
            fee_config_index=request.fee_config_index,
            gas_price=_opt_dec(request.gas_price),
            max_gas=request.max_gas,
            wallet_address=request.wallet_address,
        )

    else:
        raise ToolError(f"Unknown action: {action}")

    return {
        "action": action,
        "connector": connector,
        "network": net,
        "pool_address": request.pool_address,
        "position_address": request.position_address,
        "result": result,
    }

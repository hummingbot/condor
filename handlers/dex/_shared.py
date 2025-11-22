"""
Shared utilities for DEX trading handlers

Contains:
- Server client helper
- Explorer URL generation
- Common formatters
"""

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# ============================================
# SERVER CLIENT HELPERS
# ============================================

async def get_gateway_client():
    """Get the gateway client from the first enabled server

    Returns:
        Client instance with gateway_swap and gateway_clmm attributes

    Raises:
        ValueError: If no enabled servers or gateway not available
    """
    from servers import server_manager

    servers = server_manager.list_servers()
    enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

    if not enabled_servers:
        raise ValueError("No enabled API servers available")

    server_name = enabled_servers[0]
    client = await server_manager.get_client(server_name)

    return client


# ============================================
# EXPLORER URL GENERATION
# ============================================

SOLANA_EXPLORERS = {
    "orb": "https://orb.helius.dev/tx/{tx_hash}?cluster={cluster}&tab=summary",
    "solscan": "https://solscan.io/tx/{tx_hash}",
    "solana_explorer": "https://explorer.solana.com/tx/{tx_hash}",
}

ETHEREUM_EXPLORERS = {
    "etherscan": "https://etherscan.io/tx/{tx_hash}",
    "arbiscan": "https://arbiscan.io/tx/{tx_hash}",
    "basescan": "https://basescan.org/tx/{tx_hash}",
}


def get_explorer_url(tx_hash: str, network: str) -> Optional[str]:
    """Generate explorer URL for a transaction

    Args:
        tx_hash: Transaction hash/signature
        network: Network name (e.g., 'solana-mainnet-beta', 'ethereum-mainnet')

    Returns:
        Explorer URL or None if network not supported
    """
    if not tx_hash:
        return None

    if network.startswith("solana"):
        # Use Orb explorer for Solana (Helius)
        cluster = "mainnet-beta" if "mainnet" in network else "devnet"
        return SOLANA_EXPLORERS["orb"].format(tx_hash=tx_hash, cluster=cluster)
    elif "ethereum" in network or "mainnet" in network:
        if "arbitrum" in network:
            return ETHEREUM_EXPLORERS["arbiscan"].format(tx_hash=tx_hash)
        elif "base" in network:
            return ETHEREUM_EXPLORERS["basescan"].format(tx_hash=tx_hash)
        else:
            return ETHEREUM_EXPLORERS["etherscan"].format(tx_hash=tx_hash)

    return None


def get_explorer_name(network: str) -> str:
    """Get the explorer name for display

    Args:
        network: Network name

    Returns:
        Explorer name (e.g., 'Orb', 'Etherscan')
    """
    if network.startswith("solana"):
        return "Orb"
    elif "arbitrum" in network:
        return "Arbiscan"
    elif "base" in network:
        return "Basescan"
    elif "ethereum" in network:
        return "Etherscan"
    return "Explorer"


# ============================================
# SWAP FORMATTERS
# ============================================

def format_swap_summary(swap: Dict[str, Any], include_explorer: bool = True) -> str:
    """Format a swap record for display

    Args:
        swap: Swap data dictionary
        include_explorer: Whether to include explorer link

    Returns:
        Formatted swap summary string (not escaped)
    """
    pair = swap.get('trading_pair', 'N/A')
    side = swap.get('side', 'N/A')
    status = swap.get('status', 'N/A')
    network = swap.get('network', '')
    tx_hash = swap.get('transaction_hash', '')

    # Format amounts
    input_amount = swap.get('input_amount')
    output_amount = swap.get('output_amount')
    base_token = swap.get('base_token', '')
    quote_token = swap.get('quote_token', '')

    # Build amount string
    if input_amount is not None and output_amount is not None:
        if side == 'BUY':
            # Buying base with quote
            amount_str = f"{_format_amount(output_amount)} {base_token} for {_format_amount(input_amount)} {quote_token}"
        else:
            # Selling base for quote
            amount_str = f"{_format_amount(input_amount)} {base_token} for {_format_amount(output_amount)} {quote_token}"
    elif input_amount is not None:
        amount_str = f"{_format_amount(input_amount)}"
    else:
        amount_str = "N/A"

    # Format price
    price = swap.get('price')
    price_str = f"@ {_format_price(price)}" if price else ""

    # Build the line
    parts = [f"{side} {pair}", amount_str]
    if price_str:
        parts.append(price_str)
    parts.append(f"[{status}]")

    return " ".join(parts)


def format_swap_detail(swap: Dict[str, Any]) -> str:
    """Format detailed swap information

    Args:
        swap: Swap data dictionary

    Returns:
        Formatted multi-line swap details (not escaped)
    """
    lines = []

    # Header with status emoji
    status = swap.get('status', 'UNKNOWN')
    status_emoji = get_status_emoji(status)
    lines.append(f"{status_emoji} Swap Details")
    lines.append("")

    # Trading info
    pair = swap.get('trading_pair', 'N/A')
    side = swap.get('side', 'N/A')
    lines.append(f"Pair: {pair}")
    lines.append(f"Side: {side}")

    # Amounts
    input_amount = swap.get('input_amount')
    output_amount = swap.get('output_amount')
    base_token = swap.get('base_token', '')
    quote_token = swap.get('quote_token', '')

    if input_amount is not None:
        lines.append(f"Input: {_format_amount(input_amount)} {quote_token if side == 'BUY' else base_token}")
    if output_amount is not None:
        lines.append(f"Output: {_format_amount(output_amount)} {base_token if side == 'BUY' else quote_token}")

    # Price
    price = swap.get('price')
    if price:
        lines.append(f"Price: {_format_price(price)}")

    # Slippage
    slippage = swap.get('slippage_pct')
    if slippage is not None:
        lines.append(f"Slippage: {slippage}%")

    # Network info
    lines.append("")
    connector = swap.get('connector', 'N/A')
    network = swap.get('network', 'N/A')
    lines.append(f"Connector: {connector}")
    lines.append(f"Network: {network}")

    # Transaction
    tx_hash = swap.get('transaction_hash', '')
    if tx_hash:
        lines.append(f"Tx: {tx_hash[:16]}...")

    # Timestamp
    timestamp = swap.get('timestamp', '')
    if timestamp:
        # Format timestamp for display
        if 'T' in timestamp:
            date_part = timestamp.split('T')[0]
            time_part = timestamp.split('T')[1].split('.')[0] if '.' in timestamp.split('T')[1] else timestamp.split('T')[1].split('+')[0]
            lines.append(f"Time: {date_part} {time_part}")

    # Status
    lines.append(f"Status: {status}")

    return "\n".join(lines)


def get_status_emoji(status: str) -> str:
    """Get emoji for swap status

    Args:
        status: Status string (CONFIRMED, PENDING, FAILED, etc.)

    Returns:
        Emoji character
    """
    status_emojis = {
        "CONFIRMED": "✅",
        "PENDING": "⏳",
        "FAILED": "❌",
        "REJECTED": "🚫",
        "UNKNOWN": "❓",
    }
    return status_emojis.get(status.upper(), "📊")


def _format_amount(amount: float) -> str:
    """Format amount with appropriate precision"""
    if amount is None:
        return "N/A"

    if amount == 0:
        return "0"

    # Use appropriate decimal places based on size
    if abs(amount) >= 1000:
        return f"{amount:,.2f}"
    elif abs(amount) >= 1:
        return f"{amount:.4f}"
    elif abs(amount) >= 0.0001:
        return f"{amount:.6f}"
    else:
        return f"{amount:.8f}"


def _format_price(price: float) -> str:
    """Format price with appropriate precision"""
    if price is None:
        return "N/A"

    if price == 0:
        return "0"

    if abs(price) >= 1:
        return f"{price:.4f}"
    elif abs(price) >= 0.0001:
        return f"{price:.6f}"
    else:
        return f"{price:.10f}"


# ============================================
# STATE HELPERS
# ============================================

def clear_dex_state(context) -> None:
    """Clear all DEX-related state from user context

    Args:
        context: Telegram context object
    """
    context.user_data.pop("dex_state", None)
    context.user_data.pop("dex_previous_state", None)
    context.user_data.pop("quote_swap_params", None)
    context.user_data.pop("execute_swap_params", None)

# Gateway Configuration Module

This directory contains the modular gateway configuration system for the Condor bot.

## Structure

The gateway module has been refactored from a monolithic 3222-line `gateway.py` file into a modular structure:

```
gateway/
├── __init__.py          # Main router and module exports
├── _shared.py           # Shared imports and utilities
├── menu.py              # Gateway main menu and server selection
├── deployment.py        # Gateway deployment, lifecycle, and logs
├── wallets.py           # Wallet management functions
├── connectors.py        # DEX connector configuration
├── networks.py          # Network configuration
├── pools.py             # Liquidity pool management
├── tokens.py            # Token management
└── README.md            # This file
```

## Module Responsibilities

### `__init__.py` - Main Router
- `handle_gateway_callback()` - Routes all gateway-related button callbacks
- `handle_gateway_input()` - Routes text input to appropriate module

### `_shared.py` - Shared Utilities
- Common imports (logger, escape_markdown_v2, Telegram types)
- Shared between all modules to reduce duplication

### `menu.py` - Gateway Menu (~140 lines)
- `show_gateway_menu()` - Display main gateway configuration menu
- `show_server_selection()` - Select which server's gateway to configure
- `handle_server_selection()` - Handle server selection

### `deployment.py` - Lifecycle Management (~220 lines)
- `start_deploy_gateway()` - Show Docker image selection
- `deploy_gateway_with_image()` - Deploy gateway with selected image
- `prompt_custom_image()` - Prompt for custom Docker image
- `stop_gateway()` - Stop gateway container
- `restart_gateway()` - Restart gateway container
- `show_gateway_logs()` - Display gateway logs

### `wallets.py` - Wallet Management (~520 lines)
- `show_wallets_menu()` - Display connected wallets grouped by chain
- `handle_wallet_action()` - Route wallet-specific actions
- `prompt_add_wallet_chain()` - Select blockchain for adding wallet
- `prompt_add_wallet_private_key()` - Enter private key for wallet
- `prompt_remove_wallet_chain()` - Select chain for removal
- `prompt_remove_wallet_address()` - Select specific wallet to remove
- `remove_wallet()` - Remove wallet from gateway
- `handle_wallet_input()` - Process private key input with security measures

### `connectors.py` - DEX Connector Configuration (~600 lines)
- `show_connectors_menu()` - Display DEX connectors in grid layout
- `handle_connector_action()` - Route connector actions
- `show_connector_details()` - Display connector configuration details
- `start_connector_config_edit()` - Begin progressive configuration editing
- `handle_connector_config_back()` - Navigate to previous field
- `handle_connector_config_keep()` - Keep current value for field
- `handle_connector_config_input()` - Process configuration input
- `submit_connector_config()` - Save configuration to gateway
- `_build_connector_config_message()` - Build progressive configuration UI
- `_update_connector_config_message()` - Update message during configuration flow

### `networks.py` - Network Configuration (~600 lines)
- `show_networks_menu()` - Display available networks
- `handle_network_action()` - Route network-specific actions
- `show_network_details()` - Display network configuration
- `start_network_config_edit()` - Begin configuration editing
- `handle_network_config_back()` - Navigate backward in config flow
- `handle_network_config_keep()` - Keep current value
- `handle_network_config_input()` - Process input with type conversion
- `submit_network_config()` - Save network configuration
- `_build_network_config_message()` - Build progressive UI
- `_update_network_config_message()` - Update message during flow
- `handle_network_config_cancel()` - Cancel configuration flow

### `pools.py` - Liquidity Pool Management (~650 lines)
- `show_pools_menu()` - Select DEX connector for pool management
- `handle_pool_action()` - Route pool-specific actions
- `show_pool_networks()` - Select network for viewing pools
- `show_connector_pools()` - Display pools for connector/network pair
- `prompt_add_pool()` - Prompt for pool details (type, base, quote, address)
- `prompt_remove_pool()` - Prompt for pool address to remove
- `show_delete_pool_confirmation()` - Confirm pool deletion
- `remove_pool()` - Delete pool from gateway
- `handle_pool_input()` - Process pool addition/removal input

**Important fixes:**
- Filters out aggregator connectors (jupiter, 0x, openocean, 1inch, dexag) that don't support pools
- Uses indexed network selection to avoid Button_data_invalid errors
- Stores context in `user_data` to avoid long callback_data strings

### `tokens.py` - Token Management (~520 lines)
- `show_tokens_menu()` - Select network for token management
- `handle_token_action()` - Route token-specific actions
- `show_network_tokens()` - Display tokens for selected network
- `prompt_add_token()` - Prompt for token details (address, symbol, decimals, name)
- `prompt_remove_token()` - Prompt for token address to remove
- `show_delete_token_confirmation()` - Confirm token deletion
- `remove_token()` - Delete token from gateway
- `handle_token_input()` - Process token addition/removal input

**Important fixes:**
- Properly extracts `network_id` from dict objects instead of displaying raw dict
- Uses indexed network selection for consistency with pools

## Key Improvements

1. **Modularity**: Each module has a clear, single responsibility
2. **Maintainability**: Smaller files are easier to understand and modify
3. **Reduced duplication**: Shared utilities in `_shared.py`
4. **Clear imports**: Relative imports make dependencies explicit
5. **Bug fixes**: Aggregator filtering, Button_data_invalid fixes, network_id extraction

## Import Pattern

All modules follow this import pattern:

```python
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ..server_context import build_config_message_header  # If needed
from ._shared import logger, escape_markdown_v2
from .menu import show_gateway_menu  # Cross-module imports as needed
```

## Testing

The modules have been:
- ✅ Extracted from original gateway.py with all logic preserved
- ✅ Compile-checked with Python's py_compile
- ✅ Structured with proper imports and exports
- ⏳ Ready for runtime testing with actual Telegram bot

## Migration Notes

The original `gateway.py` file has been backed up as `gateway.py.bak`.

No changes to `handlers/config/__init__.py` were needed as it already imported:
```python
from .gateway import handle_gateway_callback, handle_gateway_input
```

This import now resolves to the new modular structure automatically.

"""
Simple API Server Manager with YAML configuration
Manages multiple Hummingbot API servers from servers.yml
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, Optional

import yaml
from aiohttp import ClientTimeout
from hummingbot_api_client import HummingbotAPIClient

logger = logging.getLogger(__name__)


class ServerManager:
    """Manages multiple API servers from servers.yml configuration"""

    def __init__(self, config_path: str = "servers.yml"):
        self.config_path = Path(config_path)
        self.servers: Dict[str, dict] = {}
        self.clients: Dict[str, HummingbotAPIClient] = {}
        self.default_server: Optional[str] = None
        self.per_chat_servers: Dict[int, str] = {}  # chat_id -> server_name
        self._load_config()

    def _load_config(self):
        """Load servers configuration from YAML file"""
        if not self.config_path.exists():
            logger.warning(f"Config file not found: {self.config_path}")
            self.servers = {}
            self.default_server = None
            return

        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
                self.servers = config.get('servers', {})
                self.default_server = config.get('default_server', None)

                # Load per-chat server defaults
                per_chat_raw = config.get('per_chat_defaults', {})
                self.per_chat_servers = {
                    int(chat_id): server_name
                    for chat_id, server_name in per_chat_raw.items()
                    if server_name in self.servers
                }

                # Validate default server exists
                if self.default_server and self.default_server not in self.servers:
                    logger.warning(f"Default server '{self.default_server}' not found in servers list")
                    self.default_server = None

                logger.info(f"Loaded {len(self.servers)} servers from {self.config_path}")
                if self.default_server:
                    logger.info(f"Default server: {self.default_server}")
                if self.per_chat_servers:
                    logger.info(f"Loaded {len(self.per_chat_servers)} per-chat server defaults")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            self.servers = {}
            self.default_server = None
            self.per_chat_servers = {}

    def _save_config(self):
        """Save servers configuration to YAML file"""
        try:
            config = {'servers': self.servers}
            if self.default_server:
                config['default_server'] = self.default_server
            if self.per_chat_servers:
                config['per_chat_defaults'] = self.per_chat_servers
            with open(self.config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            logger.info(f"Saved configuration to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise

    def add_server(self, name: str, host: str, port: int, username: str,
                   password: str) -> bool:
        """Add a new server to configuration"""
        if name in self.servers:
            logger.error(f"Server '{name}' already exists")
            return False

        self.servers[name] = {
            'host': host,
            'port': port,
            'username': username,
            'password': password
        }
        self._save_config()
        logger.info(f"Added server '{name}'")
        return True

    def modify_server(self, name: str, host: Optional[str] = None,
                     port: Optional[int] = None, username: Optional[str] = None,
                     password: Optional[str] = None) -> bool:
        """Modify an existing server configuration"""
        if name not in self.servers:
            logger.error(f"Server '{name}' not found")
            return False

        # Close existing client if configuration is changing
        if name in self.clients:
            asyncio.create_task(self._close_client(name))

        # Update only provided fields
        if host is not None:
            self.servers[name]['host'] = host
        if port is not None:
            self.servers[name]['port'] = port
        if username is not None:
            self.servers[name]['username'] = username
        if password is not None:
            self.servers[name]['password'] = password

        self._save_config()
        logger.info(f"Modified server '{name}'")
        return True

    def delete_server(self, name: str) -> bool:
        """Delete a server from configuration and runtime"""
        if name not in self.servers:
            logger.error(f"Server '{name}' not found")
            return False

        # Close and remove client if exists
        if name in self.clients:
            asyncio.create_task(self._close_client(name))

        del self.servers[name]
        self._save_config()
        logger.info(f"Deleted server '{name}'")
        return True

    def list_servers(self) -> Dict[str, dict]:
        """List all configured servers"""
        return self.servers.copy()

    def get_server(self, name: str) -> Optional[dict]:
        """Get a specific server configuration"""
        return self.servers.get(name)

    def set_default_server(self, name: str) -> bool:
        """Set the default server"""
        if name not in self.servers:
            logger.error(f"Server '{name}' not found")
            return False

        self.default_server = name
        self._save_config()
        logger.info(f"Set default server to '{name}'")
        return True

    def get_default_server(self) -> Optional[str]:
        """Get the default server name"""
        return self.default_server

    def get_default_server_for_chat(self, chat_id: int) -> Optional[str]:
        """Get the default server for a specific chat, falling back to global default"""
        server = self.per_chat_servers.get(chat_id)
        if server and server in self.servers:
            return server
        # Fallback to global default server
        if self.default_server and self.default_server in self.servers:
            return self.default_server
        # Last resort: first available server
        if self.servers:
            return list(self.servers.keys())[0]
        return None

    def set_default_server_for_chat(self, chat_id: int, server_name: str) -> bool:
        """Set the default server for a specific chat"""
        if server_name not in self.servers:
            logger.error(f"Server '{server_name}' not found")
            return False

        self.per_chat_servers[chat_id] = server_name
        self._save_config()
        logger.info(f"Set default server for chat {chat_id} to '{server_name}'")
        return True

    def clear_default_server_for_chat(self, chat_id: int) -> bool:
        """Clear the per-chat default server, reverting to global default"""
        if chat_id in self.per_chat_servers:
            del self.per_chat_servers[chat_id]
            self._save_config()
            logger.info(f"Cleared default server for chat {chat_id}")
            return True
        return False

    def get_chat_server_info(self, chat_id: int) -> dict:
        """Get server info for a chat including whether it's using per-chat or global default"""
        per_chat = self.per_chat_servers.get(chat_id)
        if per_chat and per_chat in self.servers:
            return {
                "server": per_chat,
                "is_per_chat": True,
                "global_default": self.default_server
            }
        return {
            "server": self.default_server,
            "is_per_chat": False,
            "global_default": self.default_server
        }

    async def check_server_status(self, name: str) -> dict:
        """
        Check if a server is online and responding using protected endpoint
        Returns detailed status including authentication errors
        """
        if name not in self.servers:
            return {"status": "error", "message": "Server not found"}

        server = self.servers[name]
        base_url = f"http://{server['host']}:{server['port']}"

        logger.debug(f"Checking status for '{name}' at {base_url} with username '{server['username']}'")

        # Create a temporary client for testing (don't cache it)
        # Important: Do NOT use cached clients to ensure we test current credentials
        # Use 3 second timeout for quick status checks
        client = HummingbotAPIClient(
            base_url=base_url,
            username=server['username'],
            password=server['password'],
            timeout=ClientTimeout(total=3, connect=2)  # Quick timeout for status check
        )

        try:
            await client.init()
            # Use protected endpoint to verify both connectivity and authentication
            # This will raise 401 error if credentials are wrong
            await client.accounts.list_accounts()
            logger.info(f"Status check succeeded for '{name}' - server is online")
            return {"status": "online", "message": "Connected and authenticated"}
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Status check failed for '{name}': {error_msg}")

            # Categorize the error with clearer messages
            if "401" in error_msg or "Incorrect username or password" in error_msg:
                return {"status": "auth_error", "message": "Invalid credentials"}
            elif "timeout" in error_msg.lower() or "TimeoutError" in error_msg:
                return {"status": "offline", "message": "Connection timeout - server unreachable"}
            elif "Connection" in error_msg or "Cannot connect" in error_msg or "ConnectionRefused" in error_msg:
                return {"status": "offline", "message": "Cannot reach server"}
            elif "ClientConnectorError" in error_msg or "getaddrinfo" in error_msg:
                return {"status": "offline", "message": "Server unreachable or invalid host"}
            else:
                # Show first 80 chars of error for debugging
                return {"status": "error", "message": f"Error: {error_msg[:80]}"}
        finally:
            # Always close the client
            try:
                await client.close()
            except:
                pass

    async def get_default_client(self) -> HummingbotAPIClient:
        """Get the API client for the default server"""
        if not self.default_server:
            # If no default server, try to use the first available server
            if not self.servers:
                raise ValueError("No servers configured")
            self.default_server = list(self.servers.keys())[0]
            logger.info(f"No default server set, using '{self.default_server}'")

        return await self.get_client(self.default_server)

    async def get_client_for_chat(self, chat_id: int) -> HummingbotAPIClient:
        """Get the API client for a specific chat's default server"""
        server_name = self.get_default_server_for_chat(chat_id)
        if not server_name:
            # Fallback to first available server
            if not self.servers:
                raise ValueError("No servers configured")
            server_name = list(self.servers.keys())[0]
            logger.info(f"No default server for chat {chat_id}, using '{server_name}'")

        return await self.get_client(server_name)

    async def get_client(self, name: Optional[str] = None) -> HummingbotAPIClient:
        """Get or create API client for a server. If name is None, uses default server."""
        if name is None:
            return await self.get_default_client()

        if name not in self.servers:
            raise ValueError(f"Server '{name}' not found")

        server = self.servers[name]

        # Return existing client if available
        if name in self.clients:
            return self.clients[name]

        # Create new client with reasonable timeout
        base_url = f"http://{server['host']}:{server['port']}"
        client = HummingbotAPIClient(
            base_url=base_url,
            username=server['username'],
            password=server['password'],
            timeout=ClientTimeout(total=10, connect=5)
        )

        try:
            await client.init()
            # Test connection
            await client.accounts.list_accounts()
            self.clients[name] = client
            logger.info(f"Connected to server '{name}' at {base_url}")
            return client
        except Exception as e:
            await client.close()
            logger.error(f"Failed to connect to '{name}': {e}")
            raise

    async def _close_client(self, name: str):
        """Close a specific client connection"""
        if name in self.clients:
            try:
                await self.clients[name].close()
                logger.info(f"Closed connection to '{name}'")
            except Exception as e:
                logger.error(f"Error closing client '{name}': {e}")
            finally:
                del self.clients[name]

    async def close_all(self):
        """Close all client connections"""
        for name in list(self.clients.keys()):
            await self._close_client(name)

    async def initialize_all(self):
        """Initialize all servers"""
        for name in self.servers.keys():
            try:
                await self.get_client(name)
            except Exception as e:
                logger.warning(f"Failed to initialize '{name}': {e}")

    async def reload_config(self):
        """Reload configuration from file and clear cached clients"""
        # Close all existing clients since config may have changed
        await self.close_all()
        # Reload the configuration
        self._load_config()
        logger.info("Configuration reloaded from file")


# Global server manager instance
server_manager = ServerManager()


async def get_client(chat_id: int = None):
    """Get the API client for the appropriate server.

    Args:
        chat_id: Optional chat ID to get per-chat server. If None, uses 'local' as fallback.

    Returns:
        HummingbotAPIClient instance

    Raises:
        ValueError: If no servers are configured
    """
    if chat_id is not None:
        return await server_manager.get_client_for_chat(chat_id)
    return await server_manager.get_default_client()


# Example usage
async def main():
    """Example usage of ServerManager"""

    # List all servers
    print("\nConfigured servers:")
    for name, config in server_manager.list_servers().items():
        print(f"  {name}: {config['host']}:{config['port']}")

    # Add a new server
    print("\nAdding new server...")
    server_manager.add_server(
        name="test",
        host="localhost",
        port=8081,
        username="test_user",
        password="test_pass"
    )

    # Modify a server
    print("\nModifying server...")
    server_manager.modify_server(
        name="local",
        port=8080
    )

    # Initialize all servers
    print("\nInitializing servers...")
    await server_manager.initialize_all()

    # Get a specific client
    try:
        client = await server_manager.get_client("local")
        accounts = await client.accounts.list_accounts()
        print(f"\nConnected to 'local' server, accounts: {len(accounts)}")
    except Exception as e:
        print(f"\nFailed to connect: {e}")

    # Delete a server
    print("\nDeleting test server...")
    server_manager.delete_server("test")

    # Clean up
    await server_manager.close_all()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

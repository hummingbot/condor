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

                # Validate default server exists
                if self.default_server and self.default_server not in self.servers:
                    logger.warning(f"Default server '{self.default_server}' not found in servers list")
                    self.default_server = None

                logger.info(f"Loaded {len(self.servers)} servers from {self.config_path}")
                if self.default_server:
                    logger.info(f"Default server: {self.default_server}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            self.servers = {}
            self.default_server = None

    def _save_config(self):
        """Save servers configuration to YAML file"""
        try:
            config = {'servers': self.servers}
            if self.default_server:
                config['default_server'] = self.default_server
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

    async def check_server_status(self, name: str) -> dict:
        """
        Check if a server is online and responding using protected endpoint
        Returns detailed status including authentication errors
        """
        if name not in self.servers:
            return {"status": "error", "message": "Server not found"}

        server = self.servers[name]
        base_url = f"http://{server['host']}:{server['port']}"

        # Create a temporary client for testing (don't cache it)
        client = HummingbotAPIClient(
            base_url=base_url,
            username=server['username'],
            password=server['password'],
            timeout=ClientTimeout(10)  # Shorter timeout for status check
        )

        try:
            await client.init()
            # Use protected endpoint to verify both connectivity and authentication
            await client.accounts.list_accounts()
            await client.close()
            return {"status": "online", "message": "Connected and authenticated"}
        except Exception as e:
            await client.close()
            error_msg = str(e)

            # Categorize the error
            if "401" in error_msg or "Incorrect username or password" in error_msg:
                return {"status": "auth_error", "message": "Invalid credentials"}
            elif "Connection" in error_msg or "Cannot connect" in error_msg:
                return {"status": "offline", "message": "Cannot reach server"}
            elif "timeout" in error_msg.lower():
                return {"status": "offline", "message": "Connection timeout"}
            else:
                return {"status": "error", "message": f"Error: {error_msg[:50]}"}

    async def get_default_client(self) -> HummingbotAPIClient:
        """Get the API client for the default server"""
        if not self.default_server:
            # If no default server, try to use the first available server
            if not self.servers:
                raise ValueError("No servers configured")
            self.default_server = list(self.servers.keys())[0]
            logger.info(f"No default server set, using '{self.default_server}'")

        return await self.get_client(self.default_server)

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

        # Create new client
        base_url = f"http://{server['host']}:{server['port']}"
        client = HummingbotAPIClient(
            base_url=base_url,
            username=server['username'],
            password=server['password'],
            timeout=ClientTimeout(30)
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

    def reload_config(self):
        """Reload configuration from file"""
        self._load_config()


# Global server manager instance
server_manager = ServerManager()


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

"""
Unified Configuration Manager for Condor Bot.
Manages servers, users, permissions, and settings in a single config.yml file.
"""

import logging
import time
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Tuple, Any

import yaml
from aiohttp import ClientTimeout

logger = logging.getLogger(__name__)


class UserRole(str, Enum):
    """User roles in the system"""
    ADMIN = "admin"
    USER = "user"
    PENDING = "pending"
    BLOCKED = "blocked"


class ServerPermission(str, Enum):
    """Permission levels for server access"""
    OWNER = "owner"
    TRADER = "trader"
    VIEWER = "viewer"


PERMISSION_HIERARCHY = {
    ServerPermission.VIEWER: 0,
    ServerPermission.TRADER: 1,
    ServerPermission.OWNER: 2,
}


class ConfigManager:
    """
    Unified configuration manager for Condor Bot.
    Handles servers, users, permissions, and chat defaults in a single YAML file.
    Uses singleton pattern - access via ConfigManager.instance()
    """

    VERSION = 1
    MAX_AUDIT_LOG_ENTRIES = 500

    _instance: Optional['ConfigManager'] = None

    def __init__(self, config_path: str = "config.yml"):
        self.config_path = Path(config_path)
        self.audit_log_path = Path("audit_log.yml")
        self._data: dict = {}
        self._audit_log: list = []
        self._clients: Dict[str, Tuple[Any, float]] = {}  # server_name -> (client, connect_time)
        self._client_ttl = 300  # 5 minutes
        self._load_config()
        self._load_audit_log()

    @classmethod
    def instance(cls, config_path: str = "config.yml") -> 'ConfigManager':
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None

    def _get_admin_from_env(self) -> Optional[int]:
        """Get admin user ID from environment."""
        from utils.config import ADMIN_USER_ID
        return ADMIN_USER_ID

    def _load_config(self):
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            self._init_default_config()
            return

        try:
            with open(self.config_path, 'r') as f:
                self._data = yaml.safe_load(f) or {}

            # Ensure all sections exist
            self._data.setdefault('servers', {})
            self._data.setdefault('default_server', None)
            self._data.setdefault('users', {})
            self._data.setdefault('server_access', {})
            self._data.setdefault('chat_defaults', {})
            # Migrate audit_log from config.yml to separate file (one-time)
            if 'audit_log' in self._data:
                self._audit_log = self._data.pop('audit_log')
                self._save_audit_log()
                self._save_config()  # Save config without audit_log

            # Always trust admin_id from env
            admin_id = self._get_admin_from_env()
            if admin_id:
                self._data['admin_id'] = admin_id
                self._ensure_admin_user(admin_id)

            logger.info(f"Loaded config from {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            self._init_default_config()

    def _init_default_config(self):
        """Initialize with default configuration."""
        admin_id = self._get_admin_from_env()
        self._data = {
            'servers': {},
            'default_server': None,
            'admin_id': admin_id,
            'users': {},
            'server_access': {},
            'chat_defaults': {},
            'version': self.VERSION
        }
        self._audit_log = []
        if admin_id:
            self._ensure_admin_user(admin_id)
        self._save_config()
        logger.info(f"Created new config at {self.config_path}")

    def _ensure_admin_user(self, admin_id: int):
        """Ensure admin user exists in users dict."""
        if admin_id not in self._data['users']:
            self._data['users'][admin_id] = {
                'user_id': admin_id,
                'role': UserRole.ADMIN.value,
                'created_at': time.time(),
                'notes': 'Primary admin from ADMIN_USER_ID'
            }
            self._save_config()

    def _save_config(self):
        """Save configuration to YAML file."""
        try:
            data = {
                'servers': self._data.get('servers', {}),
                'default_server': self._data.get('default_server'),
                'admin_id': self._data.get('admin_id'),
                'users': self._data.get('users', {}),
                'server_access': self._data.get('server_access', {}),
                'chat_defaults': self._data.get('chat_defaults', {}),
                'version': self._data.get('version', self.VERSION)
            }
            with open(self.config_path, 'w') as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            logger.debug(f"Saved config to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise

    def _load_audit_log(self):
        """Load audit log from separate file."""
        if not self.audit_log_path.exists():
            self._audit_log = []
            return

        try:
            with open(self.audit_log_path, 'r') as f:
                data = yaml.safe_load(f) or {}
                self._audit_log = data.get('entries', [])
            logger.debug(f"Loaded {len(self._audit_log)} audit log entries")
        except Exception as e:
            logger.error(f"Failed to load audit log: {e}")
            self._audit_log = []

    def _save_audit_log(self):
        """Save audit log to separate file."""
        try:
            # Trim to max entries
            if len(self._audit_log) > self.MAX_AUDIT_LOG_ENTRIES:
                self._audit_log = self._audit_log[-self.MAX_AUDIT_LOG_ENTRIES:]

            data = {'entries': self._audit_log}
            with open(self.audit_log_path, 'w') as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            logger.debug(f"Saved {len(self._audit_log)} audit log entries")
        except Exception as e:
            logger.error(f"Failed to save audit log: {e}")

    def reload(self):
        """Reload configuration from file."""
        self._load_config()
        self._load_audit_log()

    @property
    def admin_id(self) -> Optional[int]:
        return self._data.get('admin_id')

    # =========================================================================
    # SERVER MANAGEMENT
    # =========================================================================

    def list_servers(self) -> Dict[str, dict]:
        """List all configured servers."""
        return self._data.get('servers', {}).copy()

    def get_server(self, name: str) -> Optional[dict]:
        """Get a specific server configuration."""
        return self._data.get('servers', {}).get(name)

    def add_server(self, name: str, host: str, port: int, username: str,
                   password: str, owner_id: int = None) -> bool:
        """Add a new server."""
        servers = self._data['servers']
        if name in servers:
            logger.error(f"Server '{name}' already exists")
            return False

        servers[name] = {
            'host': host,
            'port': port,
            'username': username,
            'password': password
        }

        # Register ownership
        if owner_id:
            self.register_server_owner(name, owner_id)

        self._save_config()
        logger.info(f"Added server '{name}'")
        return True

    def modify_server(self, name: str, host: str = None, port: int = None,
                      username: str = None, password: str = None) -> bool:
        """Modify an existing server."""
        servers = self._data['servers']
        if name not in servers:
            logger.error(f"Server '{name}' not found")
            return False

        # Clear cached client
        if name in self._clients:
            del self._clients[name]

        if host is not None:
            servers[name]['host'] = host
        if port is not None:
            servers[name]['port'] = port
        if username is not None:
            servers[name]['username'] = username
        if password is not None:
            servers[name]['password'] = password

        self._save_config()
        logger.info(f"Modified server '{name}'")
        return True

    def delete_server(self, name: str, actor_id: int = None) -> bool:
        """Delete a server."""
        servers = self._data['servers']
        if name not in servers:
            logger.error(f"Server '{name}' not found")
            return False

        # Clear cached client
        if name in self._clients:
            del self._clients[name]

        del servers[name]

        # Unregister from access control
        if name in self._data['server_access']:
            del self._data['server_access'][name]

        self._save_config()
        logger.info(f"Deleted server '{name}'")
        return True

    def get_default_server(self) -> Optional[str]:
        """Get the default server name."""
        return self._data.get('default_server')

    def set_default_server(self, name: str) -> bool:
        """Set the default server."""
        if name not in self._data['servers']:
            logger.error(f"Server '{name}' not found")
            return False

        self._data['default_server'] = name
        self._save_config()
        logger.info(f"Set default server to '{name}'")
        return True

    async def get_client(self, name: str = None):
        """Get or create API client for a server."""
        from hummingbot_api_client import HummingbotAPIClient

        if name is None:
            name = self.get_default_server()
            if not name:
                if self._data['servers']:
                    name = list(self._data['servers'].keys())[0]
                else:
                    raise ValueError("No servers configured")

        if name not in self._data['servers']:
            raise ValueError(f"Server '{name}' not found")

        # Return cached client if fresh
        if name in self._clients:
            client, connect_time = self._clients[name]
            if time.time() - connect_time < self._client_ttl:
                self._clients[name] = (client, time.time())  # Refresh
                return client
            else:
                try:
                    await client.close()
                except:
                    pass
                del self._clients[name]

        # Create new client
        server = self._data['servers'][name]
        base_url = f"http://{server['host']}:{server['port']}"
        client = HummingbotAPIClient(
            base_url=base_url,
            username=server['username'],
            password=server['password'],
            timeout=ClientTimeout(total=60, connect=10)
        )

        try:
            await client.init()
            await client.accounts.list_accounts()
            self._clients[name] = (client, time.time())
            logger.info(f"Connected to server '{name}' at {base_url}")
            return client
        except Exception as e:
            await client.close()
            logger.error(f"Failed to connect to '{name}': {e}")
            raise

    async def get_client_for_chat(self, chat_id: int, user_id: int = None, preferred_server: str = None):
        """Get the API client for a user's preferred or first accessible server.

        Priority:
        1. preferred_server (from user preferences/context) if accessible
        2. chat_defaults[chat_id] if accessible
        3. First accessible server for the user
        4. If no user_id, use chat default or any available server
        """
        if user_id:
            accessible = self.get_accessible_servers(user_id)
            if not accessible:
                raise ValueError("No servers available. Ask the admin to share a server with you.")

            # 1. User's preferred server if accessible
            if preferred_server and preferred_server in accessible:
                return await self.get_client(preferred_server)

            # 2. Chat's default server if accessible
            chat_default = self._data.get('chat_defaults', {}).get(chat_id)
            if chat_default and chat_default in accessible:
                return await self.get_client(chat_default)

            # 3. First accessible server
            return await self.get_client(accessible[0])

        # No user_id - use chat default with proper fallbacks
        server_name = self.get_chat_default_server(chat_id)
        if not server_name:
            raise ValueError("No servers configured")
        return await self.get_client(server_name)

    async def check_server_status(self, name: str) -> dict:
        """Check if a server is online."""
        from hummingbot_api_client import HummingbotAPIClient

        if name not in self._data['servers']:
            return {"status": "error", "message": "Server not found"}

        server = self._data['servers'][name]
        base_url = f"http://{server['host']}:{server['port']}"

        client = HummingbotAPIClient(
            base_url=base_url,
            username=server['username'],
            password=server['password'],
            timeout=ClientTimeout(total=3, connect=2)
        )

        try:
            await client.init()
            await client.accounts.list_accounts()
            return {"status": "online", "message": "Connected and authenticated"}
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg:
                return {"status": "auth_error", "message": "Invalid credentials"}
            elif "timeout" in error_msg.lower():
                return {"status": "offline", "message": "Connection timeout"}
            elif "connect" in error_msg.lower():
                return {"status": "offline", "message": "Cannot reach server"}
            else:
                return {"status": "error", "message": f"Error: {error_msg[:80]}"}
        finally:
            try:
                await client.close()
            except:
                pass

    async def close_all_clients(self):
        """Close all cached client connections."""
        for name, (client, _) in list(self._clients.items()):
            try:
                await client.close()
                logger.info(f"Closed connection to '{name}'")
            except Exception as e:
                logger.error(f"Error closing client '{name}': {e}")
        self._clients.clear()

    # =========================================================================
    # USER MANAGEMENT
    # =========================================================================

    def get_user(self, user_id: int) -> Optional[dict]:
        """Get user record."""
        return self._data.get('users', {}).get(user_id)

    def get_user_role(self, user_id: int) -> Optional[UserRole]:
        """Get user's role."""
        user = self.get_user(user_id)
        if user:
            try:
                return UserRole(user['role'])
            except ValueError:
                return None
        return None

    def is_admin(self, user_id: int) -> bool:
        return self.get_user_role(user_id) == UserRole.ADMIN

    def is_approved(self, user_id: int) -> bool:
        role = self.get_user_role(user_id)
        return role in (UserRole.ADMIN, UserRole.USER)

    def register_pending(self, user_id: int, username: str = None) -> bool:
        """Register a new pending user."""
        users = self._data['users']
        if user_id in users:
            return False

        users[user_id] = {
            'user_id': user_id,
            'username': username,
            'role': UserRole.PENDING.value,
            'created_at': time.time()
        }
        self._audit('user_registered', 'user', str(user_id), user_id)
        self._save_config()
        logger.info(f"Registered pending user {user_id}")
        return True

    def approve_user(self, user_id: int, admin_id: int) -> bool:
        """Approve a pending user."""
        users = self._data['users']
        if user_id not in users:
            return False
        if users[user_id]['role'] == UserRole.BLOCKED.value:
            return False

        users[user_id]['role'] = UserRole.USER.value
        users[user_id]['approved_by'] = admin_id
        users[user_id]['approved_at'] = time.time()

        self._audit('user_approved', 'user', str(user_id), admin_id)
        self._save_config()
        logger.info(f"User {user_id} approved by {admin_id}")
        return True

    def reject_user(self, user_id: int, admin_id: int) -> bool:
        """Reject a pending user."""
        users = self._data['users']
        if user_id not in users or users[user_id]['role'] != UserRole.PENDING.value:
            return False

        del users[user_id]
        self._audit('user_rejected', 'user', str(user_id), admin_id)
        self._save_config()
        return True

    def block_user(self, user_id: int, admin_id: int) -> bool:
        """Block a user."""
        users = self._data['users']
        if user_id not in users or user_id == admin_id:
            return False
        if users[user_id]['role'] == UserRole.ADMIN.value:
            return False

        users[user_id]['role'] = UserRole.BLOCKED.value
        self._audit('user_blocked', 'user', str(user_id), admin_id)
        self._save_config()
        return True

    def unblock_user(self, user_id: int, admin_id: int) -> bool:
        """Unblock a user (sets to pending)."""
        users = self._data['users']
        if user_id not in users or users[user_id]['role'] != UserRole.BLOCKED.value:
            return False

        users[user_id]['role'] = UserRole.PENDING.value
        self._audit('user_unblocked', 'user', str(user_id), admin_id)
        self._save_config()
        return True

    def get_pending_users(self) -> list:
        return [u for u in self._data.get('users', {}).values()
                if u.get('role') == UserRole.PENDING.value]

    def get_all_users(self) -> list:
        return list(self._data.get('users', {}).values())

    # =========================================================================
    # SERVER ACCESS CONTROL
    # =========================================================================

    def register_server_owner(self, server_name: str, owner_id: int) -> bool:
        """Register server ownership."""
        access = self._data['server_access']
        if server_name in access:
            return False

        access[server_name] = {
            'owner_id': owner_id,
            'created_at': time.time(),
            'shared_with': {}
        }
        self._audit('server_registered', 'server', server_name, owner_id)
        self._save_config()
        return True

    def ensure_server_registered(self, server_name: str, default_owner_id: int = None) -> bool:
        """Ensure server is registered in access control."""
        if server_name in self._data['server_access']:
            return True

        owner_id = default_owner_id or self.admin_id
        if owner_id:
            self._data['server_access'][server_name] = {
                'owner_id': owner_id,
                'created_at': time.time(),
                'shared_with': {}
            }
            self._save_config()
            return True
        return False

    def get_server_owner(self, server_name: str) -> Optional[int]:
        access = self._data.get('server_access', {}).get(server_name)
        return access.get('owner_id') if access else None

    def get_server_permission(self, user_id: int, server_name: str) -> Optional[ServerPermission]:
        """Get user's permission level for a server."""
        if self.is_admin(user_id):
            return ServerPermission.OWNER

        access = self._data.get('server_access', {}).get(server_name)
        if not access:
            return None

        if access.get('owner_id') == user_id:
            return ServerPermission.OWNER

        perm_str = access.get('shared_with', {}).get(user_id)
        if perm_str:
            try:
                return ServerPermission(perm_str)
            except ValueError:
                return None
        return None

    def has_server_access(self, user_id: int, server_name: str,
                          min_permission: ServerPermission = ServerPermission.VIEWER) -> bool:
        perm = self.get_server_permission(user_id, server_name)
        if perm is None:
            return False
        return PERMISSION_HIERARCHY.get(perm, 0) >= PERMISSION_HIERARCHY.get(min_permission, 0)

    def share_server(self, server_name: str, owner_id: int,
                     target_user_id: int, permission: ServerPermission) -> bool:
        """Share a server with another user."""
        access = self._data.get('server_access', {}).get(server_name)
        if not access:
            return False
        if access.get('owner_id') != owner_id and not self.is_admin(owner_id):
            return False
        if target_user_id == access.get('owner_id'):
            return False
        if not self.is_approved(target_user_id):
            return False

        access.setdefault('shared_with', {})[target_user_id] = permission.value
        self._audit('server_shared', 'server', server_name, owner_id,
                    {'target_user': target_user_id, 'permission': permission.value})
        self._save_config()
        return True

    def revoke_server_access(self, server_name: str, owner_id: int, target_user_id: int) -> bool:
        """Revoke a user's access to a server."""
        access = self._data.get('server_access', {}).get(server_name)
        if not access:
            return False
        if access.get('owner_id') != owner_id and not self.is_admin(owner_id):
            return False

        shared = access.get('shared_with', {})
        if target_user_id not in shared:
            return False

        del shared[target_user_id]
        self._audit('server_access_revoked', 'server', server_name, owner_id,
                    {'target_user': target_user_id})
        self._save_config()
        return True

    def get_server_shared_users(self, server_name: str) -> list:
        """Get list of users a server is shared with."""
        access = self._data.get('server_access', {}).get(server_name)
        if not access:
            return []

        result = []
        for user_id, perm_str in access.get('shared_with', {}).items():
            try:
                result.append((user_id, ServerPermission(perm_str)))
            except ValueError:
                pass
        return result

    def get_accessible_servers(self, user_id: int) -> list:
        """Get all servers a user can access."""
        if self.is_admin(user_id):
            return list(self._data.get('server_access', {}).keys())

        accessible = []
        for server_name, access in self._data.get('server_access', {}).items():
            if access.get('owner_id') == user_id:
                accessible.append(server_name)
            elif user_id in access.get('shared_with', {}):
                accessible.append(server_name)
        return accessible

    def get_owned_servers(self, user_id: int) -> list:
        return [s for s, a in self._data.get('server_access', {}).items()
                if a.get('owner_id') == user_id]

    def get_shared_servers(self, user_id: int) -> list:
        """Get servers shared with user (not owned)."""
        result = []
        for server_name, access in self._data.get('server_access', {}).items():
            if access.get('owner_id') == user_id:
                continue
            perm_str = access.get('shared_with', {}).get(user_id)
            if perm_str:
                try:
                    result.append((server_name, ServerPermission(perm_str)))
                except ValueError:
                    pass
        return result

    def list_accessible_servers(self, user_id: int) -> Dict[str, dict]:
        """List servers accessible by a user with their configs."""
        if self.is_admin(user_id):
            # Auto-register unregistered servers for admin
            for name in self._data['servers']:
                self.ensure_server_registered(name, self.admin_id)
            return self._data['servers'].copy()

        accessible = {}
        for name in self.get_accessible_servers(user_id):
            if name in self._data['servers']:
                accessible[name] = self._data['servers'][name]
        return accessible

    # =========================================================================
    # CHAT DEFAULTS
    # =========================================================================

    def get_chat_default_server(self, chat_id: int) -> Optional[str]:
        """Get the default server for a chat."""
        server = self._data.get('chat_defaults', {}).get(chat_id)
        if server and server in self._data['servers']:
            return server
        # Fallback to global default
        default = self.get_default_server()
        if default and default in self._data['servers']:
            return default
        # Last resort: first server
        if self._data['servers']:
            return list(self._data['servers'].keys())[0]
        return None

    def set_chat_default_server(self, chat_id: int, server_name: str) -> bool:
        """Set the default server for a chat."""
        if server_name not in self._data['servers']:
            return False
        self._data.setdefault('chat_defaults', {})[chat_id] = server_name
        self._save_config()
        return True

    def clear_chat_default_server(self, chat_id: int) -> bool:
        """Clear the default server for a chat."""
        defaults = self._data.get('chat_defaults', {})
        if chat_id in defaults:
            del defaults[chat_id]
            self._save_config()
            return True
        return False

    def get_chat_server_info(self, chat_id: int) -> dict:
        """Get server info for a chat."""
        per_chat = self._data.get('chat_defaults', {}).get(chat_id)
        if per_chat and per_chat in self._data['servers']:
            return {
                "server": per_chat,
                "is_per_chat": True,
                "global_default": self.get_default_server()
            }
        return {
            "server": self.get_default_server(),
            "is_per_chat": False,
            "global_default": self.get_default_server()
        }

    # =========================================================================
    # AUDIT LOG
    # =========================================================================

    def _audit(self, action: str, target_type: str, target_id: str,
               actor_id: int, details: dict = None):
        self._audit_log.append({
            'timestamp': time.time(),
            'actor_id': actor_id,
            'action': action,
            'target_type': target_type,
            'target_id': target_id,
            'details': details
        })
        self._save_audit_log()

    def get_audit_log(self, limit: int = 50) -> list:
        return list(reversed(self._audit_log))[:limit]


# Convenience functions
def get_config_manager() -> ConfigManager:
    """Get the ConfigManager singleton instance."""
    return ConfigManager.instance()


def get_effective_server(chat_id: int, user_data: dict = None) -> str | None:
    """Get the effective default server for a chat, checking both user_data and config.yml.

    Priority:
    1. user_data active_server (from pickle, fast in-memory)
    2. chat_defaults from config.yml (persistent across hard kills)
    3. None if nothing configured

    Args:
        chat_id: The chat ID
        user_data: Optional user_data dict from context

    Returns:
        Server name or None
    """
    from handlers.config.user_preferences import get_active_server

    # First check user_data (pickle - might be lost on hard kill)
    if user_data:
        active = get_active_server(user_data)
        if active:
            return active

    # Fall back to chat_defaults in config.yml (always persisted)
    cm = get_config_manager()
    chat_default = cm._data.get('chat_defaults', {}).get(chat_id)
    if chat_default and chat_default in cm._data.get('servers', {}):
        # Also sync back to user_data for fast future access
        if user_data is not None:
            from handlers.config.user_preferences import set_active_server
            set_active_server(user_data, chat_default)
        return chat_default

    return None


async def get_client(chat_id: int, user_id: int = None, context=None):
    """Get the API client for the user's preferred server."""
    preferred_server = None
    if context is not None:
        # Handle both normal context and job context (where user_data may be None)
        user_data = context.user_data
        if user_data is None:
            user_data = getattr(context, '_user_data', None)

        if user_id is None and user_data is not None:
            user_id = user_data.get('_user_id')
        preferred_server = get_effective_server(chat_id, user_data)

    return await get_config_manager().get_client_for_chat(chat_id, user_id, preferred_server)

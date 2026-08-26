"""
Unified Configuration Manager for Condor Bot.
Manages servers, users, permissions, and settings in a single config.yml file.
"""

import asyncio
import logging
import secrets
import shutil
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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


PERMISSION_HIERARCHY = {
    ServerPermission.TRADER: 0,
    ServerPermission.OWNER: 1,
}

# The user preference that hands a non-admin seat the in-process code runner
# (SEC-151). It is stored under ``user_preferences[<id>]`` like any other
# preference, but it is a *capability grant*: only an admin may set it, and
# ``condor/web/routes/code.py`` reads it as the fallback arm of its gate.
# Declared here so the gate and the thing that grants it share one spelling.
CODE_RUN_PREFERENCE = "code_run"

# Preference keys that are capability grants rather than settings. They live in
# the same ``user_preferences`` map as a UI toggle, so any endpoint that merges
# a caller-supplied dict into that map is a privilege-escalation path unless it
# refuses these first (SEC-250). Writing one goes through its own audited setter
# — ``set_code_run_grant`` for ``code_run`` — never through a bulk merge.
RESERVED_PREFERENCE_KEYS = frozenset({CODE_RUN_PREFERENCE})


class ConfigManager:
    """
    Unified configuration manager for Condor Bot.
    Handles servers, users, permissions, and chat defaults in a single YAML file.
    Uses singleton pattern - access via ConfigManager.instance()
    """

    VERSION = 1
    MAX_AUDIT_LOG_ENTRIES = 500

    _instance: Optional["ConfigManager"] = None

    def __init__(self, config_path: str = "config.yml"):
        self.config_path = Path(config_path)
        self.audit_log_path = Path("audit_log.yml")
        self._data: dict = {}
        self._audit_log: list = []
        self._clients: Dict[str, Tuple[Any, float]] = (
            {}
        )  # server_name -> (client, connect_time)
        self._client_ttl = 300  # 5 minutes
        self._client_verify_interval = 60  # seconds between liveness checks
        self._client_locks: Dict[str, asyncio.Lock] = (
            {}
        )  # per-server lock for get_client
        # Short-lived memo of check_server_status results so the N parallel
        # probes fired by one menu render (and repeated taps) collapse to one
        # probe per server. Kept well under _client_verify_interval.
        self._status_cache: Dict[str, Tuple[dict, float]] = (
            {}
        )  # server_name -> (status_result, checked_at)
        self._status_ttl = 15  # seconds
        self._status_locks: Dict[str, asyncio.Lock] = (
            {}
        )  # per-server lock for check_server_status
        # True when an existing config file could not be read: the in-memory
        # state is empty and MUST NOT be written back over the file on disk.
        self._load_failed = False
        self._load_config()
        self._load_audit_log()

    @classmethod
    def instance(cls, config_path: str = "config.yml") -> "ConfigManager":
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

    @property
    def _backup_path(self) -> Path:
        """Path of the rotating backup written before each save."""
        return self.config_path.with_suffix(self.config_path.suffix + ".bak")

    def _read_config_file(self, path: Path) -> Optional[dict]:
        """Read and parse a config file. Returns None if unreadable."""
        try:
            with open(path, "r") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Failed to load config from {path}: {e}")
            return None

    def _load_config(self):
        """Load configuration from YAML file.

        A read failure is never a write: an existing but unreadable config is
        left untouched on disk (we fall back to the `.bak`, and otherwise run
        with empty in-memory state and refuse to save) so it can be recovered
        by hand.
        """
        if not self.config_path.exists():
            self._init_default_config()
            return

        data = self._read_config_file(self.config_path)

        if data is None and self._backup_path.exists():
            logger.warning(
                f"Config {self.config_path} unreadable, "
                f"attempting recovery from {self._backup_path}"
            )
            data = self._read_config_file(self._backup_path)
            if data is not None:
                logger.info("Successfully recovered config from backup")

        if data is None:
            self._load_failed = True
            self._data = self._default_data()
            self._audit_log = []
            admin_id = self._data.get("admin_id")
            if admin_id:
                self._ensure_admin_user(admin_id)
            logger.error(
                f"Config {self.config_path} is unreadable and no usable backup "
                f"exists. Running with empty in-memory config and REFUSING to "
                f"save, so the file is preserved for manual recovery. "
                f"Fix or remove it and restart."
            )
            return

        self._data = data

        # Ensure all sections exist
        self._data.setdefault("servers", {})
        self._data.setdefault("default_server", None)
        self._data.setdefault("users", {})
        self._data.setdefault("server_access", {})
        self._data.setdefault("chat_defaults", {})
        self._data.setdefault("user_preferences", {})
        self._data.setdefault("telemetry", {})
        self._data.setdefault("sharing", {})
        # Migrate audit_log from config.yml to separate file (one-time)
        if "audit_log" in self._data:
            self._audit_log = self._data.pop("audit_log")
            self._save_audit_log()
            self._save_config()  # Save config without audit_log

        # Always trust admin_id from env
        admin_id = self._get_admin_from_env()
        if admin_id:
            self._data["admin_id"] = admin_id
            self._ensure_admin_user(admin_id)

        logger.info(f"Loaded config from {self.config_path}")

    def _default_data(self) -> dict:
        """Build an empty config structure."""
        return {
            "servers": {},
            "default_server": None,
            "admin_id": self._get_admin_from_env(),
            "users": {},
            "server_access": {},
            "chat_defaults": {},
            "user_preferences": {},
            "telemetry": {},
            "version": self.VERSION,
        }

    def _init_default_config(self):
        """Initialize with default configuration."""
        self._data = self._default_data()
        self._audit_log = []
        admin_id = self._data.get("admin_id")
        if admin_id:
            self._ensure_admin_user(admin_id)
        self._save_config()
        logger.info(f"Created new config at {self.config_path}")

    def _ensure_admin_user(self, admin_id: int):
        """Ensure admin user exists in users dict."""
        if admin_id not in self._data["users"]:
            self._data["users"][admin_id] = {
                "user_id": admin_id,
                "role": UserRole.ADMIN.value,
                "created_at": time.time(),
                "notes": "Primary admin from ADMIN_USER_ID",
            }
            self._save_config()

    def _save_config(self):
        """Save configuration to YAML file."""
        if self._load_failed:
            logger.warning(
                f"Not saving config: {self.config_path} could not be read at "
                f"startup and would be overwritten with empty state"
            )
            return

        try:
            data = {
                "servers": self._data.get("servers", {}),
                "default_server": self._data.get("default_server"),
                "admin_id": self._data.get("admin_id"),
                "users": self._data.get("users", {}),
                "server_access": self._data.get("server_access", {}),
                "chat_defaults": self._data.get("chat_defaults", {}),
                # Omitting this dropped every preference on the next save —
                # including the code_run grant, which _load_config reads back
                # and routes/code.py gates on (ARCH-177).
                "user_preferences": self._data.get("user_preferences", {}),
                "web_jwt_secret": self._data.get("web_jwt_secret"),
                "telemetry": self._data.get("telemetry", {}),
                # Same omission as ARCH-177 above, one section newer: without
                # this the admin's sharing veto, each user's standing answer and
                # the share_secret behind the stable pseudonyms were re-minted
                # on every restart (CORR-244).
                "sharing": self._data.get("sharing", {}),
                "version": self._data.get("version", self.VERSION),
            }
            # Keep a copy of the last known-good file before truncating it,
            # so a partial write can be recovered from on the next load.
            if self.config_path.exists():
                try:
                    shutil.copy2(self.config_path, self._backup_path)
                except OSError as e:
                    logger.warning(f"Failed to back up config: {e}")

            with open(self.config_path, "w") as f:
                yaml.dump(
                    data,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                )
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
            with open(self.audit_log_path, "r") as f:
                data = yaml.safe_load(f) or {}
                self._audit_log = data.get("entries", [])
            logger.debug(f"Loaded {len(self._audit_log)} audit log entries")
        except Exception as e:
            logger.error(f"Failed to load audit log: {e}")
            self._audit_log = []

    def _save_audit_log(self):
        """Save audit log to separate file."""
        try:
            # Trim to max entries
            if len(self._audit_log) > self.MAX_AUDIT_LOG_ENTRIES:
                self._audit_log = self._audit_log[-self.MAX_AUDIT_LOG_ENTRIES :]

            data = {"entries": self._audit_log}
            with open(self.audit_log_path, "w") as f:
                yaml.dump(
                    data,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                )
            logger.debug(f"Saved {len(self._audit_log)} audit log entries")
        except Exception as e:
            logger.error(f"Failed to save audit log: {e}")

    def reload(self):
        """Reload configuration from file."""
        self._load_config()
        self._load_audit_log()

    @property
    def admin_id(self) -> Optional[int]:
        return self._data.get("admin_id")

    # =========================================================================
    # SERVER MANAGEMENT
    # =========================================================================

    def list_servers(self) -> Dict[str, dict]:
        """List all configured servers."""
        return self._data.get("servers", {}).copy()

    def get_server(self, name: str) -> Optional[dict]:
        """Get a specific server configuration."""
        return self._data.get("servers", {}).get(name)

    def add_server(
        self,
        name: str,
        host: str,
        port: int,
        username: str,
        password: str,
        owner_id: int = None,
    ) -> bool:
        """Add a new server."""
        servers = self._data["servers"]
        if name in servers:
            logger.error(f"Server '{name}' already exists")
            return False

        servers[name] = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
        }

        # Register ownership
        if owner_id:
            self.register_server_owner(name, owner_id)

        self._save_config()
        logger.info(f"Added server '{name}'")
        return True

    def modify_server(
        self,
        name: str,
        host: str = None,
        port: int = None,
        username: str = None,
        password: str = None,
    ) -> bool:
        """Modify an existing server."""
        servers = self._data["servers"]
        if name not in servers:
            logger.error(f"Server '{name}' not found")
            return False

        # Clear cached client and any memoized status
        self._invalidate_server_caches(name)

        if host is not None:
            servers[name]["host"] = host
        if port is not None:
            servers[name]["port"] = port
        if username is not None:
            servers[name]["username"] = username
        if password is not None:
            servers[name]["password"] = password

        self._save_config()
        logger.info(f"Modified server '{name}'")
        return True

    def delete_server(self, name: str, actor_id: int = None) -> bool:
        """Delete a server."""
        servers = self._data["servers"]
        if name not in servers:
            logger.error(f"Server '{name}' not found")
            return False

        # Clear cached client and any memoized status
        self._invalidate_server_caches(name)

        del servers[name]

        # Unregister from access control
        if name in self._data["server_access"]:
            del self._data["server_access"][name]

        self._save_config()
        logger.info(f"Deleted server '{name}'")
        return True

    def get_default_server(self) -> Optional[str]:
        """Get the default server name."""
        return self._data.get("default_server")

    def set_default_server(self, name: str) -> bool:
        """Set the default server."""
        if name not in self._data["servers"]:
            logger.error(f"Server '{name}' not found")
            return False

        self._data["default_server"] = name
        self._save_config()
        logger.info(f"Set default server to '{name}'")
        return True

    def get_or_create_web_jwt_secret(self) -> str:
        """Return the web dashboard JWT signing secret, generating one on demand.

        On first use a strong random secret is generated and persisted to
        ``config.yml`` so web sessions survive restarts. The web layer prefers an
        explicit ``WEB_JWT_SECRET`` env var over this value for multi-instance or
        rotation scenarios; this is the zero-config default for everyone else.
        """
        secret = self._data.get("web_jwt_secret")
        if secret:
            return secret
        secret = secrets.token_urlsafe(32)
        self._data["web_jwt_secret"] = secret
        self._save_config()
        logger.info(
            "Generated and persisted a new web dashboard JWT secret in %s",
            self.config_path,
        )
        return secret

    def _invalidate_server_caches(self, name: str):
        """Drop the pooled client and memoized status for a server.

        Called whenever the server's credentials or existence change, so a
        cached client or status can never outlive the config it was built from.
        """
        self._clients.pop(name, None)
        self._status_cache.pop(name, None)

    async def get_client(self, name: str = None):
        """Get or create API client for a server."""
        from hummingbot_api_client import HummingbotAPIClient

        if name is None:
            name = self.get_default_server()
            if not name:
                if self._data["servers"]:
                    name = list(self._data["servers"].keys())[0]
                else:
                    raise ValueError("No servers configured")

        if name not in self._data["servers"]:
            raise ValueError(f"Server '{name}' not found")

        # Fast path (no lock): return cached client if recently verified
        if name in self._clients:
            client, last_verified = self._clients[name]
            if time.time() - last_verified < self._client_verify_interval:
                return client

        # Serialize client creation/verification per server to prevent
        # concurrent coroutines from creating duplicate sessions
        if name not in self._client_locks:
            self._client_locks[name] = asyncio.Lock()

        async with self._client_locks[name]:
            return await self._get_or_create_client(name, HummingbotAPIClient)

    async def _get_or_create_client(self, name: str, HummingbotAPIClient):
        """Inner client acquisition — must be called under _client_locks[name]."""
        # Re-check under lock (another coroutine may have just created it)
        if name in self._clients:
            client, last_verified = self._clients[name]
            if time.time() - last_verified < self._client_verify_interval:
                # Fast path: recently verified
                return client
            elif time.time() - last_verified < self._client_ttl:
                # Needs liveness check
                try:
                    await asyncio.wait_for(client.accounts.list_accounts(), timeout=5)
                    self._clients[name] = (client, time.time())
                    return client
                except Exception:
                    logger.warning(
                        f"Stale connection to '{name}' detected, reconnecting"
                    )
                    try:
                        await client.close()
                    except Exception:
                        pass
                    del self._clients[name]
            else:
                try:
                    await client.close()
                except Exception:
                    pass
                del self._clients[name]

        # Create new client
        server = self._data["servers"][name]
        base_url = f"http://{server['host']}:{server['port']}"
        client = HummingbotAPIClient(
            base_url=base_url,
            username=server["username"],
            password=server["password"],
            timeout=ClientTimeout(total=60, connect=10),
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

    async def get_client_for_chat(
        self, chat_id: int, user_id: int = None, preferred_server: str = None
    ):
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
                raise ValueError(
                    "No servers available. Ask the admin to share a server with you."
                )

            # 1. User's preferred server if accessible
            if preferred_server and preferred_server in accessible:
                return await self.get_client(preferred_server)

            # 2. Chat's default server if accessible
            chat_default = self._data.get("chat_defaults", {}).get(chat_id)
            if chat_default and chat_default in accessible:
                return await self.get_client(chat_default)

            # 3. First accessible server
            return await self.get_client(accessible[0])

        # No user_id — no access list to check against, so the caller's explicit
        # choice wins over the chat default. This branch used to drop
        # preferred_server entirely, which meant a routine run (web, MCP or agent)
        # and any handler that omits user_id ran against the chat default no matter
        # which server was actually selected.
        if preferred_server and preferred_server in self._data["servers"]:
            return await self.get_client(preferred_server)

        server_name = self.get_chat_default_server(chat_id)
        if not server_name:
            raise ValueError("No servers configured")
        return await self.get_client(server_name)

    STATUS_PROBE_TIMEOUT = 3  # seconds — a dead server must not stall a menu

    @staticmethod
    def _classify_status_error(exc: Exception) -> dict:
        """Map a failed liveness probe to the status shown in the menus."""
        error_msg = str(exc)
        if isinstance(exc, asyncio.TimeoutError):
            return {"status": "offline", "message": "Connection timeout"}
        if "401" in error_msg:
            return {"status": "auth_error", "message": "Invalid credentials"}
        if "timeout" in error_msg.lower():
            return {"status": "offline", "message": "Connection timeout"}
        if "connect" in error_msg.lower():
            return {"status": "offline", "message": "Cannot reach server"}
        return {"status": "error", "message": f"Error: {error_msg[:80]}"}

    def _pooled_client(self, name: str):
        """Return the pooled client for a server if it is still within TTL."""
        entry = self._clients.get(name)
        if not entry:
            return None
        client, connected_at = entry
        if time.time() - connected_at >= self._client_ttl:
            return None
        return client

    async def check_server_status(self, name: str) -> dict:
        """Check if a server is online.

        Probes through the pooled client when ConfigManager already holds one,
        and only builds a short-timeout throwaway client when there is none (or
        when the pooled session itself is broken). Results are memoized per
        server for _status_ttl seconds so one menu render costs at most one
        probe per server.
        """
        if name not in self._data["servers"]:
            return {"status": "error", "message": "Server not found"}

        cached = self._status_cache.get(name)
        if cached and time.time() - cached[1] < self._status_ttl:
            return dict(cached[0])

        lock = self._status_locks.setdefault(name, asyncio.Lock())
        async with lock:
            # Re-check: a concurrent probe for this server may have just landed
            cached = self._status_cache.get(name)
            if cached and time.time() - cached[1] < self._status_ttl:
                return dict(cached[0])

            result = await self._probe_server_status(name)
            self._status_cache[name] = (result, time.time())
            return dict(result)

    async def _probe_server_status(self, name: str) -> dict:
        """Run one liveness probe, preferring the pooled client."""
        # Same call shape as condor/fetchers/server_status.py:fetch_server_status
        # so the two liveness probes stay in step.
        client = self._pooled_client(name)
        if client is not None:
            try:
                await asyncio.wait_for(
                    client.accounts.list_accounts(),
                    timeout=self.STATUS_PROBE_TIMEOUT,
                )
                return {"status": "online", "message": "Connected and authenticated"}
            except Exception as e:
                # The pooled session may simply be stale — confirm with a fresh
                # short-timeout client rather than reporting a false outage.
                # The pooled client is never closed here: get_client owns it.
                logger.debug(f"Pooled status probe for '{name}' failed: {e}")

        return await self._probe_server_status_fresh(name)

    async def _probe_server_status_fresh(self, name: str) -> dict:
        """Liveness probe over a throwaway short-timeout client."""
        from hummingbot_api_client import HummingbotAPIClient

        server = self._data["servers"][name]
        base_url = f"http://{server['host']}:{server['port']}"

        client = HummingbotAPIClient(
            base_url=base_url,
            username=server["username"],
            password=server["password"],
            timeout=ClientTimeout(total=3, connect=2),
        )

        try:
            await client.init()
            await client.accounts.list_accounts()
            return {"status": "online", "message": "Connected and authenticated"}
        except Exception as e:
            return self._classify_status_error(e)
        finally:
            try:
                await client.close()
            except Exception:
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
        self._status_cache.clear()

    # =========================================================================
    # USER MANAGEMENT
    # =========================================================================

    # ── Telemetry (FEAT-023) ──
    # Consent and the install's random identity live here because this file is
    # the one durable, process-wide store the MCP subprocess can also read.
    # Nothing in this section is transmitted except `install_id` and `level`;
    # `install_secret` never leaves the machine. See PRIVACY.md.

    def get_telemetry(self) -> dict:
        """The telemetry section. Empty dict on an install that never opted in."""
        section = self._data.get("telemetry")
        return dict(section) if isinstance(section, dict) else {}

    def update_telemetry(self, **changes) -> dict:
        """Merge keys into the telemetry section and persist."""
        section = self._data.setdefault("telemetry", {})
        if not isinstance(section, dict):
            section = {}
            self._data["telemetry"] = section
        section.update(changes)
        self._save_config()
        return dict(section)

    def get_sharing(self) -> dict:
        """The conversation-sharing section (FEAT-054). Empty on a fresh install.

        Deliberately separate from :meth:`get_telemetry`: the two record
        different promises to different people. Telemetry consent is the
        admin's, install-wide, and anonymous counts; sharing holds an admin veto
        plus this install's own random ids, and the content it gates belongs to
        individual users.
        """
        section = self._data.get("sharing")
        return dict(section) if isinstance(section, dict) else {}

    def update_sharing(self, **changes) -> dict:
        """Merge keys into the sharing section and persist."""
        section = self._data.setdefault("sharing", {})
        if not isinstance(section, dict):
            section = {}
            self._data["sharing"] = section
        section.update(changes)
        self._save_config()
        return dict(section)

    def get_user(self, user_id: int) -> Optional[dict]:
        """Get user record."""
        return self._data.get("users", {}).get(user_id)

    def get_user_role(self, user_id: int) -> Optional[UserRole]:
        """Get user's role."""
        user = self.get_user(user_id)
        if user:
            try:
                return UserRole(user["role"])
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
        users = self._data["users"]
        if user_id in users:
            return False

        users[user_id] = {
            "user_id": user_id,
            "username": username,
            "role": UserRole.PENDING.value,
            "created_at": time.time(),
        }
        self._audit("user_registered", "user", str(user_id), user_id)
        self._save_config()
        logger.info(f"Registered pending user {user_id}")
        return True

    def approve_user(self, user_id: int, admin_id: int) -> bool:
        """Approve a pending user."""
        users = self._data["users"]
        if user_id not in users:
            return False
        if users[user_id]["role"] == UserRole.BLOCKED.value:
            return False

        users[user_id]["role"] = UserRole.USER.value
        users[user_id]["approved_by"] = admin_id
        users[user_id]["approved_at"] = time.time()

        self._audit("user_approved", "user", str(user_id), admin_id)
        self._save_config()
        logger.info(f"User {user_id} approved by {admin_id}")
        return True

    def reject_user(self, user_id: int, admin_id: int) -> bool:
        """Reject a pending user."""
        users = self._data["users"]
        if user_id not in users or users[user_id]["role"] != UserRole.PENDING.value:
            return False

        del users[user_id]
        self._audit("user_rejected", "user", str(user_id), admin_id)
        self._save_config()
        return True

    def block_user(self, user_id: int, admin_id: int) -> bool:
        """Block a user."""
        users = self._data["users"]
        if user_id not in users or user_id == admin_id:
            return False
        if users[user_id]["role"] == UserRole.ADMIN.value:
            return False

        users[user_id]["role"] = UserRole.BLOCKED.value
        self._audit("user_blocked", "user", str(user_id), admin_id)
        self._save_config()
        return True

    def unblock_user(self, user_id: int, admin_id: int) -> bool:
        """Unblock a user (sets to pending)."""
        users = self._data["users"]
        if user_id not in users or users[user_id]["role"] != UserRole.BLOCKED.value:
            return False

        users[user_id]["role"] = UserRole.PENDING.value
        self._audit("user_unblocked", "user", str(user_id), admin_id)
        self._save_config()
        return True

    def get_pending_users(self) -> list:
        return [
            u
            for u in self._data.get("users", {}).values()
            if u.get("role") == UserRole.PENDING.value
        ]

    def get_all_users(self) -> list:
        return list(self._data.get("users", {}).values())

    # =========================================================================
    # USER PREFERENCES (persisted in config.yml, shared across TG + Web)
    # =========================================================================

    def get_user_preferences(self, user_id: int) -> dict:
        """Get all preferences for a user. Returns a copy."""
        prefs = self._data.setdefault("user_preferences", {})
        return dict(prefs.get(user_id, {}))

    def get_user_preference(self, user_id: int, key: str, default=None):
        """Get a single preference value."""
        prefs = self._data.get("user_preferences", {}).get(user_id, {})
        return prefs.get(key, default)

    def set_user_preference(self, user_id: int, key: str, value) -> None:
        """Set a single preference value and persist."""
        prefs = self._data.setdefault("user_preferences", {})
        if user_id not in prefs:
            prefs[user_id] = {}
        prefs[user_id][key] = value
        self._save_config()

    def set_user_preferences(self, user_id: int, updates: dict) -> None:
        """Merge multiple preference values and persist.

        This is the bulk path, and the only caller feeds it a dict straight off
        the wire, so it refuses ``RESERVED_PREFERENCE_KEYS`` outright: a
        capability grant shares this map with ordinary settings, and merging one
        in from a request body would hand a user the very grant its audited
        setter exists to control (SEC-250). Grants are written one at a time
        through ``set_user_preference``, which ``set_code_run_grant`` drives.
        """
        reserved = RESERVED_PREFERENCE_KEYS.intersection(updates)
        if reserved:
            raise ValueError(
                f"Reserved preference keys cannot be set in bulk: "
                f"{', '.join(sorted(reserved))}"
            )
        prefs = self._data.setdefault("user_preferences", {})
        if user_id not in prefs:
            prefs[user_id] = {}
        prefs[user_id].update(updates)
        self._save_config()

    def delete_user_preference(self, user_id: int, key: str) -> bool:
        """Delete a preference key. Returns True if it existed."""
        prefs = self._data.setdefault("user_preferences", {})
        user_prefs = prefs.get(user_id)
        if user_prefs and key in user_prefs:
            del user_prefs[key]
            self._save_config()
            return True
        return False

    def has_code_run_grant(self, user_id: int) -> bool:
        """Whether this user carries the explicit ``code_run`` grant.

        Says nothing about admins: being an admin is a separate arm of the gate
        in ``condor/web/routes/code.py``. This answers only "was the capability
        handed to them", which is what an admin UI needs to render.
        """
        return bool(self.get_user_preference(user_id, CODE_RUN_PREFERENCE, False))

    def set_code_run_grant(self, user_id: int, granted: bool, admin_id: int) -> bool:
        """Grant or revoke the ``code_run`` capability. Returns True if changed.

        Handing this out is handing out arbitrary Python in the bot's own
        process — every server's credentials, the web JWT secret, os.environ —
        so it is audited on both edges like ``approve_user``/``block_user``, and
        the caller is expected to have already checked ``is_admin(admin_id)``.

        Revoking deletes the key rather than writing ``False``, so config.yml
        keeps only the users who actually hold the grant. Either way the write
        lands in config.yml immediately and the next request re-reads it, so a
        grant takes effect without restarting the bot.
        """
        if user_id not in self._data.get("users", {}):
            return False
        if self.has_code_run_grant(user_id) == bool(granted):
            return False

        if granted:
            self.set_user_preference(user_id, CODE_RUN_PREFERENCE, True)
        else:
            self.delete_user_preference(user_id, CODE_RUN_PREFERENCE)

        self._audit(
            "code_run_granted" if granted else "code_run_revoked",
            "user",
            str(user_id),
            admin_id,
        )
        return True

    # =========================================================================
    # SERVER ACCESS CONTROL
    # =========================================================================

    def register_server_owner(self, server_name: str, owner_id: int) -> bool:
        """Register server ownership."""
        access = self._data["server_access"]
        if server_name in access:
            return False

        access[server_name] = {
            "owner_id": owner_id,
            "created_at": time.time(),
            "shared_with": {},
        }
        self._audit("server_registered", "server", server_name, owner_id)
        self._save_config()
        return True

    def ensure_server_registered(
        self, server_name: str, default_owner_id: int = None
    ) -> bool:
        """Ensure server is registered in access control."""
        if server_name in self._data["server_access"]:
            return True

        owner_id = default_owner_id or self.admin_id
        if owner_id:
            self._data["server_access"][server_name] = {
                "owner_id": owner_id,
                "created_at": time.time(),
                "shared_with": {},
            }
            self._save_config()
            return True
        return False

    def get_server_owner(self, server_name: str) -> Optional[int]:
        access = self._data.get("server_access", {}).get(server_name)
        return access.get("owner_id") if access else None

    def get_server_permission(
        self, user_id: int, server_name: str
    ) -> Optional[ServerPermission]:
        """Get user's permission level for a server."""
        if self.is_admin(user_id):
            return ServerPermission.OWNER

        access = self._data.get("server_access", {}).get(server_name)
        if not access:
            return None

        if access.get("owner_id") == user_id:
            return ServerPermission.OWNER

        perm_str = access.get("shared_with", {}).get(user_id)
        if perm_str:
            try:
                return ServerPermission(perm_str)
            except ValueError:
                return None
        return None

    def has_server_access(
        self,
        user_id: int,
        server_name: str,
        min_permission: ServerPermission = ServerPermission.TRADER,
    ) -> bool:
        perm = self.get_server_permission(user_id, server_name)
        if perm is None:
            return False
        return PERMISSION_HIERARCHY.get(perm, 0) >= PERMISSION_HIERARCHY.get(
            min_permission, 0
        )

    def share_server(
        self,
        server_name: str,
        owner_id: int,
        target_user_id: int,
        permission: ServerPermission,
    ) -> bool:
        """Share a server with another user."""
        access = self._data.get("server_access", {}).get(server_name)
        if not access:
            return False
        if access.get("owner_id") != owner_id and not self.is_admin(owner_id):
            return False
        if target_user_id == access.get("owner_id"):
            return False
        if not self.is_approved(target_user_id):
            return False

        access.setdefault("shared_with", {})[target_user_id] = permission.value
        self._audit(
            "server_shared",
            "server",
            server_name,
            owner_id,
            {"target_user": target_user_id, "permission": permission.value},
        )
        self._save_config()
        return True

    def revoke_server_access(
        self, server_name: str, owner_id: int, target_user_id: int
    ) -> bool:
        """Revoke a user's access to a server."""
        access = self._data.get("server_access", {}).get(server_name)
        if not access:
            return False
        if access.get("owner_id") != owner_id and not self.is_admin(owner_id):
            return False

        shared = access.get("shared_with", {})
        if target_user_id not in shared:
            return False

        del shared[target_user_id]
        self._audit(
            "server_access_revoked",
            "server",
            server_name,
            owner_id,
            {"target_user": target_user_id},
        )
        self._save_config()
        return True

    def get_server_shared_users(self, server_name: str) -> list:
        """Get list of users a server is shared with."""
        access = self._data.get("server_access", {}).get(server_name)
        if not access:
            return []

        result = []
        for user_id, perm_str in access.get("shared_with", {}).items():
            try:
                result.append((user_id, ServerPermission(perm_str)))
            except ValueError:
                pass
        return result

    def get_accessible_servers(self, user_id: int) -> list:
        """Get all servers a user can access."""
        if self.is_admin(user_id):
            return list(self._data.get("server_access", {}).keys())

        accessible = []
        for server_name, access in self._data.get("server_access", {}).items():
            if access.get("owner_id") == user_id:
                accessible.append(server_name)
            elif user_id in access.get("shared_with", {}):
                accessible.append(server_name)
        return accessible

    def get_owned_servers(self, user_id: int) -> list:
        return [
            s
            for s, a in self._data.get("server_access", {}).items()
            if a.get("owner_id") == user_id
        ]

    def get_shared_servers(self, user_id: int) -> list:
        """Get servers shared with user (not owned)."""
        result = []
        for server_name, access in self._data.get("server_access", {}).items():
            if access.get("owner_id") == user_id:
                continue
            perm_str = access.get("shared_with", {}).get(user_id)
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
            for name in self._data["servers"]:
                self.ensure_server_registered(name, self.admin_id)
            return self._data["servers"].copy()

        accessible = {}
        for name in self.get_accessible_servers(user_id):
            if name in self._data["servers"]:
                accessible[name] = self._data["servers"][name]
        return accessible

    # =========================================================================
    # CHAT DEFAULTS
    # =========================================================================

    def get_chat_default_server(self, chat_id: int) -> Optional[str]:
        """Get the default server for a chat."""
        server = self._data.get("chat_defaults", {}).get(chat_id)
        if server and server in self._data["servers"]:
            return server
        # Fallback to global default
        default = self.get_default_server()
        if default and default in self._data["servers"]:
            return default
        # Last resort: first server
        if self._data["servers"]:
            return list(self._data["servers"].keys())[0]
        return None

    def set_chat_default_server(self, chat_id: int, server_name: str) -> bool:
        """Set the default server for a chat."""
        if server_name not in self._data["servers"]:
            return False
        self._data.setdefault("chat_defaults", {})[chat_id] = server_name
        self._save_config()
        return True

    def clear_chat_default_server(self, chat_id: int) -> bool:
        """Clear the default server for a chat."""
        defaults = self._data.get("chat_defaults", {})
        if chat_id in defaults:
            del defaults[chat_id]
            self._save_config()
            return True
        return False

    def get_chat_server_info(self, chat_id: int) -> dict:
        """Get server info for a chat."""
        per_chat = self._data.get("chat_defaults", {}).get(chat_id)
        if per_chat and per_chat in self._data["servers"]:
            return {
                "server": per_chat,
                "is_per_chat": True,
                "global_default": self.get_default_server(),
            }
        return {
            "server": self.get_default_server(),
            "is_per_chat": False,
            "global_default": self.get_default_server(),
        }

    # =========================================================================
    # AUDIT LOG
    # =========================================================================

    def _audit(
        self,
        action: str,
        target_type: str,
        target_id: str,
        actor_id: int,
        details: dict = None,
    ):
        self._audit_log.append(
            {
                "timestamp": time.time(),
                "actor_id": actor_id,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "details": details,
            }
        )
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
    1. chat_defaults from config.yml — the one place *every* surface writes when
       the user picks a default (Telegram's /servers, the web dashboard, admin
       assignment), so it is the only entry that can be current.
    2. user_data active_server (from pickle, per-user, covers chats that never
       had a default of their own)
    3. None if nothing configured

    The order used to be the other way round, and that made the web dashboard's
    "set default" invisible to a live Telegram session: the web writes
    config.yml, while the pickle in the running bot's ``user_data`` kept the
    server chosen before it and won every lookup until the process restarted.
    The pickle is a cache of the choice, not the choice — so it is read second
    and re-synced from config.yml whenever the two disagree.

    Args:
        chat_id: The chat ID
        user_data: Optional user_data dict from context

    Returns:
        Server name or None
    """
    from condor.preferences import SERVER_PIN_KEY, get_active_server

    # A context built for one server — a routine or an agent run launched
    # against it — carries its own answer and is never re-resolved from a chat's
    # ambient default. Everything else reaching this point holds a preference,
    # which is a cache of a choice config.yml records authoritatively.
    if user_data and user_data.get(SERVER_PIN_KEY):
        pinned = get_active_server(user_data)
        if pinned:
            return pinned

    cm = get_config_manager()
    chat_default = cm._data.get("chat_defaults", {}).get(chat_id)
    if chat_default and chat_default in cm._data.get("servers", {}):
        # Keep the pickle in step, but only on an actual change: the setter
        # persists the whole preference section to config.yml, and this runs on
        # every server lookup.
        if user_data is not None and get_active_server(user_data) != chat_default:
            from condor.preferences import set_active_server

            set_active_server(user_data, chat_default)
        return chat_default

    # No default recorded for this chat — fall back to the user's own last pick.
    if user_data:
        active = get_active_server(user_data)
        if active:
            return active

    return None


async def get_client(chat_id: int, user_id: int = None, context=None):
    """Get the API client for the user's preferred server."""
    preferred_server = None
    if context is not None:
        # Handle both normal context and job context (where user_data may be None)
        user_data = context.user_data
        if user_data is None:
            user_data = getattr(context, "_user_data", None)

        if user_id is None and user_data is not None:
            user_id = user_data.get("_user_id")
        preferred_server = get_effective_server(chat_id, user_data)

    return await get_config_manager().get_client_for_chat(
        chat_id, user_id, preferred_server
    )

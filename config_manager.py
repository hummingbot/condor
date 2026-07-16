"""
Unified Configuration Manager for Condor.
Manages users, permissions, and settings in a single config.yml file.

The Hummingbot server registry (server CRUD, per-server API clients,
server access control, per-chat default servers) was removed with the
Hummingbot deletion (simplification plan §9.2). What remains is user-role
management — auth still depends on it until the auth pass — plus user
preferences, the web JWT secret, and the audit log.
"""

import logging
import secrets
import time
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


class UserRole(str, Enum):
    """User roles in the system"""

    ADMIN = "admin"
    USER = "user"
    PENDING = "pending"
    BLOCKED = "blocked"


class ConfigManager:
    """
    Unified configuration manager for Condor.
    Handles users, roles, and preferences in a single YAML file.
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

    def _load_config(self):
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            self._init_default_config()
            return

        try:
            with open(self.config_path, "r") as f:
                self._data = yaml.safe_load(f) or {}

            # Ensure all sections exist
            self._data.setdefault("users", {})
            self._data.setdefault("user_preferences", {})
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
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            self._init_default_config()

    def _init_default_config(self):
        """Initialize with default configuration."""
        admin_id = self._get_admin_from_env()
        self._data = {
            "admin_id": admin_id,
            "users": {},
            "user_preferences": {},
            "version": self.VERSION,
        }
        self._audit_log = []
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
        try:
            data = {
                "admin_id": self._data.get("admin_id"),
                "users": self._data.get("users", {}),
                "web_jwt_secret": self._data.get("web_jwt_secret"),
                "version": self._data.get("version", self.VERSION),
            }
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
    # WEB JWT SECRET
    # =========================================================================

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
        # Several processes share config.yml (main process + each MCP
        # subprocess), each with its own snapshot loaded at startup. A snapshot
        # without the secret does NOT mean the file has none: another process
        # may have generated one since. Adopt the on-disk value before minting —
        # a second generation here clobbers the file via _save_config() and
        # invalidates every JWT the other processes signed (opaque 401s).
        on_disk = self.reload_web_jwt_secret()
        if on_disk:
            return on_disk
        secret = secrets.token_urlsafe(32)
        self._data["web_jwt_secret"] = secret
        self._save_config()
        logger.info(
            "Generated and persisted a new web dashboard JWT secret in %s",
            self.config_path,
        )
        return secret

    def reload_web_jwt_secret(self) -> Optional[str]:
        """Re-read ``web_jwt_secret`` from config.yml into the snapshot.

        Returns the on-disk secret (or None). Lets a long-lived process pick up
        a secret persisted by a sibling process after this one loaded its
        snapshot, instead of diverging from it.
        """
        try:
            with open(self.config_path, "r") as f:
                on_disk = (yaml.safe_load(f) or {}).get("web_jwt_secret")
        except Exception as e:
            logger.error(f"Failed to re-read web JWT secret: {e}")
            return self._data.get("web_jwt_secret")
        if on_disk:
            self._data["web_jwt_secret"] = on_disk
        return on_disk

    # =========================================================================
    # USER MANAGEMENT
    # =========================================================================

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

    def get_approved_users(self) -> list[int]:
        """All user ids with an approved role (admin or user)."""
        return [
            uid for uid in self._data.get("users", {}) if self.is_approved(uid)
        ]

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
    # USER PREFERENCES (persisted in config.yml)
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
        """Merge multiple preference values and persist."""
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

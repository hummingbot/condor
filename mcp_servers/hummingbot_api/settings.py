"""
Configuration settings for Hummingbot MCP Server
"""

import argparse
import os
from pathlib import Path

import aiohttp
import yaml
from pydantic import BaseModel, Field, field_validator

from mcp_servers.hummingbot_api.exceptions import ConfigurationError

CONFIG_DIR = Path.home() / ".hummingbot_mcp"
SERVER_CONFIG_PATH = CONFIG_DIR / "server.yml"

#: Default tool profile (FEAT-066). ``full`` on purpose: this server is also run
#: standalone (the uvx console script, an external MCP host, the checked-in
#: `.mcp.json`), and a launch with no flag has to keep serving the whole surface.
DEFAULT_TOOL_PROFILE = "full"


def _parse_tool_profile() -> str:
    """``--profile`` off argv, read at import.

    It has to be resolved *here* rather than in ``server._apply_cli_args``: which
    tools exist is decided when the module registers them, which is import time,
    and ``_apply_cli_args`` only runs once ``_run()`` is already starting the
    stdio loop. ``parse_known_args`` so every other flag the spawner passes
    (``--url``, ``--server-name``, ``--bot-id``) stays inert here, and so a run
    under pytest — whose argv is the test runner's — resolves the default.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile", default=DEFAULT_TOOL_PROFILE)
    args, _ = parser.parse_known_args()
    return args.profile


class ServerConfig(BaseModel):
    """Active server configuration"""

    name: str = Field(default="default")
    url: str = Field(default="http://localhost:8000")
    username: str = Field(default="admin")
    password: str = Field(default="admin")

    @field_validator("url", mode="before")
    def validate_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("API URL must start with http:// or https://")
        return v


def _load_server_config() -> ServerConfig:
    """Load server config from ~/.hummingbot_mcp/server.yml, fallback to env vars."""
    if SERVER_CONFIG_PATH.exists():
        try:
            with open(SERVER_CONFIG_PATH) as f:
                data = yaml.safe_load(f) or {}
            return ServerConfig(**data)
        except Exception:
            pass

    return ServerConfig(
        name=os.getenv("HUMMINGBOT_SERVER_NAME", "default"),
        url=os.getenv("HUMMINGBOT_API_URL", "http://localhost:8000"),
        username=os.getenv("HUMMINGBOT_USERNAME", "admin"),
        password=os.getenv("HUMMINGBOT_PASSWORD", "admin"),
    )


def save_server_config(config: ServerConfig):
    """Persist the active server config to disk."""
    CONFIG_DIR.mkdir(exist_ok=True)
    with open(SERVER_CONFIG_PATH, "w") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, sort_keys=False)


class Settings(BaseModel):
    """Application settings"""

    # API Configuration
    api_url: str = Field(default="http://localhost:8000")
    api_username: str = Field(default="admin")
    api_password: str = Field(default="admin")
    server_name: str = Field(default="default")
    default_account: str = Field(default="master_account")

    # Which slice of the tool surface this process registers (FEAT-066). For an
    # ACP-driven seat the mounted surface IS the permission model, so this is a
    # security boundary, not a convenience: see ``server.TOOL_PROFILES``.
    tool_profile: str = Field(default=DEFAULT_TOOL_PROFILE)

    # Connection settings
    connection_timeout: float = Field(default=30.0)
    max_retries: int = Field(default=3)
    retry_delay: float = Field(default=2.0)

    # Logging
    log_level: str = Field(default="INFO")

    @field_validator("api_url", mode="before")
    def validate_api_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("API URL must start with http:// or https://")
        return v

    @field_validator("log_level", mode="before")
    def validate_log_level(cls, v):
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level must be one of: {valid_levels}")
        return v.upper()

    @property
    def client_timeout(self) -> aiohttp.ClientTimeout:
        """Get aiohttp ClientTimeout object"""
        return aiohttp.ClientTimeout(total=self.connection_timeout)

    def reload_from_server_config(self, config: ServerConfig):
        """Reload API settings from a ServerConfig"""
        self.api_url = config.url
        self.api_username = config.username
        self.api_password = config.password
        self.server_name = config.name


def get_settings() -> Settings:
    """Get application settings from server configuration"""
    try:
        server_config = _load_server_config()

        return Settings(
            api_url=server_config.url,
            api_username=server_config.username,
            api_password=server_config.password,
            server_name=server_config.name,
            connection_timeout=float(os.getenv("HUMMINGBOT_TIMEOUT", "30.0")),
            max_retries=int(os.getenv("HUMMINGBOT_MAX_RETRIES", "3")),
            retry_delay=float(os.getenv("HUMMINGBOT_RETRY_DELAY", "2.0")),
            log_level=os.getenv("HUMMINGBOT_LOG_LEVEL", "INFO"),
            tool_profile=_parse_tool_profile(),
        )
    except Exception as e:
        raise ConfigurationError(f"Failed to load configuration: {e}")


# Global settings instance
settings = get_settings()

"""Issue #279: remove silent admin/admin credential defaults and surface server.yml parse errors."""

import asyncio

import pytest

from mcp_servers.hummingbot_api import settings as settings_module
from mcp_servers.hummingbot_api.exceptions import ConfigurationError
from mcp_servers.hummingbot_api.hummingbot_client import HummingbotClient
from mcp_servers.hummingbot_api.settings import (
    ServerConfig,
    Settings,
    _init_module_settings,
    _load_server_config,
    get_settings,
    settings,
)


def test_server_config_requires_explicit_non_empty_credentials():
    """ServerConfig no longer defaults username or password to 'admin'."""
    with pytest.raises(Exception):
        ServerConfig()

    with pytest.raises(Exception):
        ServerConfig(username="", password="secret")

    with pytest.raises(Exception):
        ServerConfig(username="user", password="   ")

    cfg = ServerConfig(username="alice", password="s3cret")
    assert cfg.username == "alice"
    assert cfg.password == "s3cret"


def test_settings_model_does_not_default_to_admin():
    """Settings has empty credentials by default rather than 'admin'/'admin'."""
    s = Settings()
    assert s.api_username == ""
    assert s.api_password == ""


def test_missing_config_and_unset_env_raises_configuration_error(tmp_path, monkeypatch):
    """When ~/.hummingbot_mcp/server.yml is absent and env vars are unset, _load_server_config raises."""
    missing = tmp_path / "server.yml"
    monkeypatch.setattr(settings_module, "SERVER_CONFIG_PATH", missing)
    for var in (
        "HUMMINGBOT_USERNAME",
        "HUMMINGBOT_PASSWORD",
        "HUMMINGBOT_API_USERNAME",
        "HUMMINGBOT_API_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ConfigurationError) as exc:
        _load_server_config()

    assert str(missing) in str(exc.value)

    with pytest.raises(ConfigurationError):
        get_settings()


def test_unparseable_server_yml_raises_configuration_error_naming_file(
    tmp_path, monkeypatch
):
    """A malformed ~/.hummingbot_mcp/server.yml raises ConfigurationError naming the file
    instead of silently falling back to env vars or admin/admin."""
    bad_yml = tmp_path / "server.yml"
    bad_yml.write_text("name: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "SERVER_CONFIG_PATH", bad_yml)
    monkeypatch.setenv("HUMMINGBOT_USERNAME", "env-user")
    monkeypatch.setenv("HUMMINGBOT_PASSWORD", "env-pass")

    with pytest.raises(ConfigurationError) as exc:
        _load_server_config()

    assert str(bad_yml) in str(exc.value)


def test_server_yml_missing_credentials_raises_configuration_error_naming_file(
    tmp_path, monkeypatch
):
    """A server.yml without username/password raises ConfigurationError naming the file."""
    incomplete_yml = tmp_path / "server.yml"
    incomplete_yml.write_text(
        "name: prod\nurl: http://localhost:8000\n", encoding="utf-8"
    )
    monkeypatch.setattr(settings_module, "SERVER_CONFIG_PATH", incomplete_yml)

    with pytest.raises(ConfigurationError) as exc:
        _load_server_config()

    assert str(incomplete_yml) in str(exc.value)


def test_valid_server_yml_loads_cleanly(tmp_path, monkeypatch):
    valid_yml = tmp_path / "server.yml"
    valid_yml.write_text(
        "name: prod\nurl: http://10.0.0.8:8000\nusername: bot\npassword: s3cr3t\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "SERVER_CONFIG_PATH", valid_yml)

    cfg = _load_server_config()
    assert cfg.name == "prod"
    assert cfg.url == "http://10.0.0.8:8000"
    assert cfg.username == "bot"
    assert cfg.password == "s3cr3t"


def test_env_credentials_preserve_leading_and_trailing_spaces(tmp_path, monkeypatch):
    """Passwords with leading or trailing whitespace in env vars are preserved verbatim."""
    missing = tmp_path / "server.yml"
    monkeypatch.setattr(settings_module, "SERVER_CONFIG_PATH", missing)
    monkeypatch.delenv("HUMMINGBOT_API_USERNAME", raising=False)
    monkeypatch.delenv("HUMMINGBOT_API_PASSWORD", raising=False)
    monkeypatch.setenv("HUMMINGBOT_USERNAME", "env-user")
    monkeypatch.setenv("HUMMINGBOT_PASSWORD", "  spaced-pass  ")

    cfg = _load_server_config()
    assert cfg.username == "env-user"
    assert cfg.password == "  spaced-pass  "


def test_hummingbot_client_surfaces_preserved_server_yml_error(tmp_path, monkeypatch):
    """When server.yml is malformed at import time, HummingbotClient.initialize surfaces that file error."""
    bad_yml = tmp_path / "server.yml"
    bad_yml.write_text("name: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "SERVER_CONFIG_PATH", bad_yml)

    fallback_settings = _init_module_settings()
    assert str(bad_yml) in fallback_settings.config_error

    monkeypatch.setattr(settings, "api_username", "")
    monkeypatch.setattr(settings, "api_password", "")
    monkeypatch.setattr(settings, "config_error", fallback_settings.config_error)

    client = HummingbotClient()
    with pytest.raises(ConfigurationError) as exc:
        asyncio.run(client.initialize())

    assert str(bad_yml) in str(exc.value)


def test_hummingbot_client_refuses_to_connect_with_unset_credentials(monkeypatch):
    """HummingbotClient.initialize raises ConfigurationError when credentials are empty."""
    monkeypatch.setattr(settings, "api_username", "")
    monkeypatch.setattr(settings, "api_password", "")
    monkeypatch.setattr(settings, "config_error", "")

    client = HummingbotClient()
    with pytest.raises(ConfigurationError):
        asyncio.run(client.initialize())


def test_telegram_add_server_flow_does_not_offer_admin_defaults():
    """The Telegram add-server wizard no longer offers 'Default: admin' for username or password."""
    from handlers.config.servers import _build_add_server_message

    for field in ("username", "password"):
        _text, markup = _build_add_server_message(
            {"name": "prod", "host": "localhost", "port": 8000}, field
        )
        buttons = [btn for row in markup.inline_keyboard for btn in row]
        default_buttons = [
            btn for btn in buttons if "Default:" in (btn.text or "")
        ]
        assert default_buttons == [], f"{field} still offers a prefilled default button"


def test_doctor_flags_placeholder_username_as_well_as_password():
    """condor.doctor._check_placeholder_credentials flags placeholder usernames too."""
    from condor import doctor

    data = {
        "servers": {
            "local": {
                "host": "localhost",
                "username": "admin",
                "password": "s3cr3t-real-password",
            }
        }
    }
    checks = doctor._check_placeholder_credentials(data)
    assert len(checks) == 1
    assert checks[0].state == doctor.WARN
    assert "local" in checks[0].detail
    assert "placeholder credentials" in checks[0].detail

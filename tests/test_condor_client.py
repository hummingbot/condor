"""Tests for the MCP-server identity auto-bind."""

import asyncio

import config_manager
from mcp_servers.condor import settings as settings_module


class _StubCM:
    def __init__(self, approved):
        self._approved = approved

    def get_approved_users(self):
        return self._approved


def test_ensure_identity_auto_binds_sole_approved_user(monkeypatch):
    """Tier A: exactly one approved user in config.yml → bind identity to it
    (user_id and, when unset, chat_id)."""
    monkeypatch.setattr(settings_module.settings, "user_id", 0)
    monkeypatch.setattr(settings_module.settings, "chat_id", 0)
    monkeypatch.setattr(settings_module, "_config_present", lambda: True)
    monkeypatch.setattr(
        config_manager, "get_config_manager", lambda: _StubCM([456181693])
    )

    assert settings_module.ensure_identity() is True
    assert settings_module.settings.user_id == 456181693
    assert settings_module.settings.chat_id == 456181693


def test_ensure_identity_refuses_ambiguous_or_empty(monkeypatch):
    """Zero or multiple approved users: no bind — a multi-user box must say
    who it is explicitly."""
    monkeypatch.setattr(settings_module, "_config_present", lambda: True)
    for approved in ([], [111, 222]):
        monkeypatch.setattr(settings_module.settings, "user_id", 0)
        monkeypatch.setattr(
            config_manager, "get_config_manager", lambda a=approved: _StubCM(a)
        )
        assert settings_module.ensure_identity() is False
        assert settings_module.settings.user_id == 0


def test_ensure_identity_never_creates_a_config_file(monkeypatch):
    """With no config.yml in cwd, the identity probe must not touch
    ConfigManager at all — instantiating it would CREATE a default config.yml
    as a side effect."""

    def _boom():
        raise AssertionError("ConfigManager must not be instantiated")

    monkeypatch.setattr(settings_module.settings, "user_id", 0)
    monkeypatch.setattr(settings_module, "_config_present", lambda: False)
    monkeypatch.setattr(config_manager, "get_config_manager", _boom)

    assert settings_module.ensure_identity() is False


def test_manage_memory_fails_fast_without_identity(monkeypatch):
    """Memory tools resolve the store from user_id directly (no main-API call
    to 403), so an unresolved identity must return an actionable error instead
    of silently reading/writing user 0's store."""
    from mcp_servers.condor.tools.memory import manage_memory

    monkeypatch.setattr(settings_module.settings, "user_id", 0)
    monkeypatch.setattr(settings_module, "_config_present", lambda: True)
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _StubCM([]))

    result = asyncio.run(manage_memory(action="list"))
    assert "CONDOR_USER_ID" in result["error"]


def test_ensure_identity_keeps_explicit_identity(monkeypatch):
    """An explicitly configured identity is never overridden by auto-bind."""
    monkeypatch.setattr(settings_module.settings, "user_id", 999)
    monkeypatch.setattr(
        config_manager, "get_config_manager", lambda: _StubCM([456181693])
    )
    assert settings_module.ensure_identity() is True
    assert settings_module.settings.user_id == 999

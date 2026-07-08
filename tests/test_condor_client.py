"""Tests for the MCP-server → main-process HTTP client and identity auto-bind."""

import asyncio

import pytest

import config_manager
from mcp_servers.condor import condor_client
from mcp_servers.condor import settings as settings_module
from mcp_servers.condor.exceptions import APIError


class _StubCM:
    def __init__(self, approved):
        self._approved = approved

    def get_approved_users(self):
        return self._approved


def test_call_main_api_fails_fast_without_identity(monkeypatch):
    """With no identity AND an ambiguous config (multiple approved users),
    call_main_api must raise a clear, actionable error instead of minting a
    JWT for user 0 and letting the main process 403 with an opaque
    'Access denied' (the exact failure a stock `claude` session hits with the
    repo's identity-less .mcp.json)."""
    monkeypatch.setattr(condor_client.settings, "user_id", 0)
    monkeypatch.setattr(
        config_manager, "get_config_manager", lambda: _StubCM([111, 222])
    )

    with pytest.raises(APIError, match="CONDOR_USER_ID"):
        asyncio.run(condor_client.call_main_api("GET", "/agents"))


def test_ensure_identity_auto_binds_sole_approved_user(monkeypatch):
    """Tier A: exactly one approved user in config.yml → bind identity to it
    (user_id and, when unset, chat_id)."""
    monkeypatch.setattr(settings_module.settings, "user_id", 0)
    monkeypatch.setattr(settings_module.settings, "chat_id", 0)
    monkeypatch.setattr(
        config_manager, "get_config_manager", lambda: _StubCM([456181693])
    )

    assert settings_module.ensure_identity() is True
    assert settings_module.settings.user_id == 456181693
    assert settings_module.settings.chat_id == 456181693


def test_ensure_identity_refuses_ambiguous_or_empty(monkeypatch):
    """Zero or multiple approved users: no bind — a multi-user box must say
    who it is explicitly."""
    for approved in ([], [111, 222]):
        monkeypatch.setattr(settings_module.settings, "user_id", 0)
        monkeypatch.setattr(
            config_manager, "get_config_manager", lambda a=approved: _StubCM(a)
        )
        assert settings_module.ensure_identity() is False
        assert settings_module.settings.user_id == 0


def test_ensure_identity_keeps_explicit_identity(monkeypatch):
    """An explicitly configured identity is never overridden by auto-bind."""
    monkeypatch.setattr(settings_module.settings, "user_id", 999)
    monkeypatch.setattr(
        config_manager, "get_config_manager", lambda: _StubCM([456181693])
    )
    assert settings_module.ensure_identity() is True
    assert settings_module.settings.user_id == 999

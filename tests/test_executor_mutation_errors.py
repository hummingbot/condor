"""Tests for SEC-116: an executor mutation fails by raising, and leaks nothing.

``create_executor``/``stop_executor`` used to swallow every exception and hand
back ``{"status": "error", "message": str(e)}`` — a dict shaped exactly like a
successful response, carrying aiohttp's stringified error, which embeds the
backend's URL and port. Two things went wrong with that. Callers guessed at
success by substring over the whole dict, so an API rejection worded *"executor
could not be created"* contained "created" and was announced as a live
executor; and the raw string was pasted into a Telegram message and into an
HTTP ``detail``, showing a trader-only user the internal host.

These tests pin the new contract: failure is an exception, success is its
absence, and what reaches a user is assembled from the exception's attributes
rather than its ``str()``.
"""

import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import ClientConnectorError, ClientResponseError, RequestInfo
from fastapi import HTTPException
from multidict import CIMultiDict
from yarl import URL

from condor.fetchers.executors import (
    create_executor,
    describe_executor_error,
    stop_executor,
)
from condor.web.models import CreateExecutorRequest, WebUser
from condor.web.routes.executors import create_executor_endpoint, stop_executor_endpoint

# The internal address a shared-server trader must never be shown.
BACKEND_URL = "http://10.0.0.7:8000/executors/create"
BACKEND_HOST = "10.0.0.7"
BACKEND_PORT = "8000"


def _api_error(status: int, detail: str) -> ClientResponseError:
    """The error the hummingbot client raises: API ``detail`` + backend URL."""
    info = RequestInfo(
        url=URL(BACKEND_URL),
        method="POST",
        headers=CIMultiDict(),
        real_url=URL(BACKEND_URL),
    )
    return ClientResponseError(info, (), status=status, message=detail)


def _transport_error() -> ClientConnectorError:
    """What a backend outage raises: no HTTP answer, host in the string."""
    return ClientConnectorError(
        SimpleNamespace(host=BACKEND_HOST, port=int(BACKEND_PORT), ssl=None),
        OSError(61, "Connection refused"),
    )


class FakeClient:
    """API client whose executor mutations raise whatever they are given."""

    def __init__(self, exc=None, result=None):
        self.executors = self._Executors(exc, result)

    class _Executors:
        def __init__(self, exc, result):
            self._exc = exc
            self._result = result
            self.calls = []

        async def create_executor(self, **kw):
            self.calls.append(kw)
            if self._exc:
                raise self._exc
            return self._result

        async def stop_executor(self, **kw):
            self.calls.append(kw)
            if self._exc:
                raise self._exc
            return self._result


# --- The fetcher contract: failure raises ---


def test_create_executor_propagates_the_client_exception():
    client = FakeClient(exc=_api_error(400, "insufficient balance"))

    with pytest.raises(ClientResponseError):
        asyncio.run(create_executor(client, {"type": "grid_executor"}))


def test_stop_executor_propagates_the_client_exception():
    client = FakeClient(exc=_api_error(404, "executor not found"))

    with pytest.raises(ClientResponseError):
        asyncio.run(stop_executor(client, "ex-1"))


def test_a_successful_mutation_still_returns_the_response():
    client = FakeClient(result={"executor_id": "ex-9"})

    assert asyncio.run(create_executor(client, {})) == {"executor_id": "ex-9"}


# --- describe_executor_error: safe pieces only ---


def test_the_backend_url_is_dropped_from_the_described_error():
    exc = _api_error(400, "insufficient balance")
    # Precondition: the raw string really does carry the backend address.
    assert BACKEND_HOST in str(exc)

    status, message = describe_executor_error(exc)

    assert status == 400
    assert message == "insufficient balance"
    assert BACKEND_HOST not in message and BACKEND_PORT not in message


def test_a_transport_failure_reports_no_status_and_no_host():
    exc = _transport_error()
    assert BACKEND_HOST in str(exc)

    status, message = describe_executor_error(exc)

    assert status is None, "no HTTP answer means no upstream status"
    assert BACKEND_HOST not in message and BACKEND_PORT not in message


def test_an_executor_id_full_of_digits_is_not_classified_as_a_status():
    """The old classifier read '400' out of the message; the status is an attr."""
    exc = _api_error(404, "executor 400-403-400 not found")

    status, _ = describe_executor_error(exc)

    assert status == 404


# --- The web boundary ---


class _FakeCM:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, user_id, name, *a, **kw):
        return True

    async def get_client(self, name):
        return self._client


_USER = WebUser(id=1, role="admin")


@pytest.fixture
def route_env(monkeypatch):
    def _bind(client):
        monkeypatch.setattr(
            "condor.web.routes.executors.get_config_manager", lambda: _FakeCM(client)
        )

    return _bind


def _create(client):
    return asyncio.run(
        create_executor_endpoint(
            name="srv",
            body=CreateExecutorRequest(executor_type="grid_executor", config={}),
            user=_USER,
        )
    )


def test_a_backend_outage_is_a_502_with_no_backend_address(route_env):
    route_env(FakeClient(exc=_transport_error()))

    with pytest.raises(HTTPException) as caught:
        _create(None)

    assert caught.value.status_code == 502, "an unreachable backend is not a 400"
    assert BACKEND_HOST not in caught.value.detail
    assert BACKEND_PORT not in caught.value.detail


def test_an_upstream_rejection_stays_a_400_and_keeps_the_api_message(route_env):
    route_env(FakeClient(exc=_api_error(400, "insufficient balance")))

    with pytest.raises(HTTPException) as caught:
        _create(None)

    assert caught.value.status_code == 400
    assert "insufficient balance" in caught.value.detail
    assert BACKEND_HOST not in caught.value.detail


def test_an_upstream_5xx_is_a_502(route_env):
    route_env(FakeClient(exc=_api_error(503, "backend restarting")))

    with pytest.raises(HTTPException) as caught:
        _create(None)

    assert caught.value.status_code == 502


def test_a_successful_create_still_returns_the_executor_id(route_env):
    route_env(FakeClient(result={"executor_id": "ex-9"}))

    assert _create(None) == {"status": "ok", "executor_id": "ex-9"}


def test_a_failed_stop_does_not_answer_ok(route_env):
    route_env(FakeClient(exc=_transport_error()))

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            stop_executor_endpoint(
                name="srv", executor_id="ex-1", keep_position=False, user=_USER
            )
        )

    assert caught.value.status_code == 502
    assert BACKEND_HOST not in caught.value.detail


# --- The Telegram boundary ---


class _RecordingBot:
    """Captures the text each message/edit put on screen."""

    def __init__(self):
        self.texts: list[str] = []

    async def send_message(self, **kw):
        self.texts.append(kw["text"])
        return SimpleNamespace(message_id=1)

    async def edit_message_text(self, **kw):
        self.texts.append(kw["text"])
        return None

    @property
    def last(self) -> str:
        return self.texts[-1]


def _context(bot):
    return SimpleNamespace(bot=bot, user_data={}, chat_data={})


def _query(bot):
    async def _edit(text, **kw):
        bot.texts.append(text)

    async def _noop(*a, **kw):
        return None

    return SimpleNamespace(
        message=SimpleNamespace(edit_text=_edit, delete=_noop, message_id=1),
        answer=_noop,
    )


def _update(bot):
    return SimpleNamespace(
        callback_query=_query(bot), effective_chat=SimpleNamespace(id=7)
    )


def _grid_deploy(client, monkeypatch):
    """Drive the grid deploy handler against a client, return what it printed."""
    from handlers.executors import grid
    from handlers.executors._shared import set_executor_config

    bot = _RecordingBot()
    ctx = _context(bot)
    set_executor_config(ctx, {"connector_name": "binance", "trading_pair": "SOL-USDC"})

    async def _client(chat_id, user_data):
        return client, "srv"

    monkeypatch.setattr(grid, "get_executors_client", _client)
    asyncio.run(grid.handle_deploy(_update(bot), ctx))
    return bot.last


def test_a_rejection_saying_created_is_reported_as_a_failed_deploy(monkeypatch):
    """The exact wording that used to be announced as a successful deploy."""
    client = FakeClient(
        exc=_api_error(400, "executor could not be created: insufficient balance")
    )

    screen = _grid_deploy(client, monkeypatch)

    assert "Deployed" not in screen, "a rejected deploy was announced as running"
    assert "Error" in screen or "failed" in screen.lower()
    assert BACKEND_HOST not in screen and BACKEND_PORT not in screen


def test_a_successful_deploy_is_still_announced_as_deployed(monkeypatch):
    """The other direction: no exception must keep reporting success."""
    client = FakeClient(result={"executor_id": "ex-9"})

    screen = _grid_deploy(client, monkeypatch)

    assert "Deployed" in screen
    assert "ex\\-9" in screen or "ex-9" in screen


def _stop(client, monkeypatch):
    """Drive the stop-confirm handler against a client, return what it printed."""
    from handlers.executors import menu

    bot = _RecordingBot()
    ctx = _context(bot)
    ctx.user_data["current_executor"] = {"id": "executor-id-long-enough-to-skip-search"}

    async def _client(chat_id, user_data):
        return client, "srv"

    monkeypatch.setattr(menu, "get_executors_client", _client)
    asyncio.run(
        menu.handle_confirm_stop_executor(
            _update(bot), ctx, "executor-id-long-enough-to-skip-search"
        )
    )
    return bot.last


def test_a_rejection_saying_stopped_is_reported_as_a_failed_stop(monkeypatch):
    """ "may have already stopped" contains "stop" — it is not a stop."""
    client = FakeClient(
        exc=_api_error(404, "Executor not found (may have already stopped)")
    )

    screen = _stop(client, monkeypatch)

    assert "Executor Stopped" not in screen, "a failed stop was announced as stopped"
    assert "failed" in screen.lower() or "Error" in screen
    assert BACKEND_HOST not in screen


def test_a_successful_stop_is_still_announced_as_stopped(monkeypatch):
    client = FakeClient(result={"status": "stopped"})

    screen = _stop(client, monkeypatch)

    assert "Executor Stopped" in screen

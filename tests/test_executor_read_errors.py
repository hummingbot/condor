"""Tests for SEC-126: the executor *read* endpoints leak nothing either.

SEC-116 established that a user must never be shown ``str(exc)`` from an
upstream failure — an ``aiohttp.ClientResponseError`` stringifies with the
backend's own URL in it, so on a shared server a trader reads the internal host
and port off a failed request. It wired the two mutation endpoints to
``describe_executor_error`` and left the reads doing ``detail=str(e)``.

The reads are the dashboard's hottest path, so the leak was the more reachable
of the two: any backend blip rendered the internal address into the browser.
These tests pin every read site in ``condor/web/routes/executors.py`` to the
same contract as the mutations — upstream 4xx is a 400, anything else is a 502,
the detail is assembled from the exception's attributes, and the full exception
(address included) still reaches the server log.
"""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from aiohttp import ClientConnectorError, ClientResponseError, RequestInfo
from fastapi import HTTPException
from multidict import CIMultiDict
from yarl import URL

from condor.web.models import WebUser
from condor.web.routes.executors import (
    _SDS_OFFSET_PREFIX,
    clear_position_held,
    get_positions_held,
    list_executors,
    list_executors_page,
)

# The internal address a shared-server trader must never be shown.
BACKEND_URL = "http://10.0.0.7:8000/executors/search"
BACKEND_HOST = "10.0.0.7"
BACKEND_PORT = "8000"


def _api_error(status: int, detail: str) -> ClientResponseError:
    """The error the hummingbot client raises: API ``detail`` + backend URL."""
    info = RequestInfo(
        url=URL(BACKEND_URL),
        method="GET",
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
    """API client whose executor reads raise whatever they are given."""

    def __init__(self, exc):
        self.executors = self._Executors(exc)

    class _Executors:
        def __init__(self, exc):
            self._exc = exc

        async def search_executors(self, **kw):
            raise self._exc

        async def get_positions_summary(self, **kw):
            raise self._exc

        async def clear_position_held(self, **kw):
            raise self._exc


class _FakeCM:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, user_id, name, *a, **kw):
        return True

    async def get_client(self, name):
        return self._client


class _FakeSDS:
    """Server data service whose executor cache is cold and whose fetch fails."""

    def __init__(self, exc):
        self._exc = exc

    def get(self, name, data_type):
        return None

    async def get_or_fetch(self, name, data_type):
        raise self._exc


_USER = WebUser(id=1, role="admin")


@pytest.fixture
def route_env(monkeypatch):
    """Bind both the API client and the SDS to one failing exception."""

    def _bind(exc):
        monkeypatch.setattr(
            "condor.web.routes.executors.get_config_manager",
            lambda: _FakeCM(FakeClient(exc)),
        )
        monkeypatch.setattr(
            "condor.server_data_service.get_server_data_service",
            lambda: _FakeSDS(exc),
        )

    return _bind


# --- Every read site, driven to failure ---


def _list(**overrides):
    """Call the listing endpoint with every Query default spelled out.

    Called as a plain function, an unpassed ``Query(...)`` default arrives as a
    Query object, which is truthy and would silently route to another branch.
    """
    kwargs = {
        "name": "srv",
        "executor_type": "",
        "trading_pair": "",
        "status": "",
        "controller_id": "",
        "limit": 0,
        "user": _USER,
    }
    return asyncio.run(list_executors(**{**kwargs, **overrides}))


def _page(**overrides):
    kwargs = {
        "name": "srv",
        "cursor": "",
        "limit": 50,
        "executor_type": "",
        "trading_pair": "",
        "status": "",
        "controller_id": "",
        "user": _USER,
    }
    return asyncio.run(list_executors_page(**{**kwargs, **overrides}))


def _list_filtered():
    """The direct-to-API branch of the listing endpoint."""
    return _list(status="active")


def _list_cached():
    """The SDS branch of the listing endpoint."""
    return _list()


def _page_offset():
    """The offset-cursor branch: scrolled past a cold cache, walks the API."""
    return _page(cursor=_SDS_OFFSET_PREFIX + "50")


def _page_cursor():
    """The opaque-cursor branch: one filtered search against the API."""
    return _page(status="active")


def _positions():
    return asyncio.run(get_positions_held(name="srv", user=_USER))


def _clear():
    return asyncio.run(
        clear_position_held(
            name="srv",
            connector="binance",
            pair="SOL-USDC",
            controller_id="",
            user=_USER,
        )
    )


READS = [
    pytest.param(_list_filtered, id="list-filtered"),
    pytest.param(_list_cached, id="list-cached"),
    pytest.param(_page_offset, id="page-offset-cursor"),
    pytest.param(_page_cursor, id="page-api-cursor"),
    pytest.param(_positions, id="positions-summary"),
    pytest.param(_clear, id="clear-position"),
]


@pytest.mark.parametrize("call", READS)
def test_a_backend_outage_never_shows_the_backend_address(call, route_env):
    exc = _transport_error()
    # Precondition: the raw string really does carry the internal address.
    assert BACKEND_HOST in str(exc)
    route_env(exc)

    with pytest.raises(HTTPException) as caught:
        call()

    assert caught.value.status_code == 502, "an unreachable backend is not a 400"
    assert BACKEND_HOST not in caught.value.detail
    assert BACKEND_PORT not in caught.value.detail


@pytest.mark.parametrize("call", READS)
def test_an_api_rejection_never_shows_the_backend_address(call, route_env):
    exc = _api_error(400, "unknown controller id")
    assert BACKEND_HOST in str(exc)
    route_env(exc)

    with pytest.raises(HTTPException) as caught:
        call()

    assert caught.value.status_code == 400, "an upstream 4xx is the caller's own"
    assert "unknown controller id" in caught.value.detail
    assert BACKEND_HOST not in caught.value.detail
    assert BACKEND_PORT not in caught.value.detail


@pytest.mark.parametrize("call", READS)
def test_an_upstream_5xx_is_a_502(call, route_env):
    route_env(_api_error(503, "backend restarting"))

    with pytest.raises(HTTPException) as caught:
        call()

    assert caught.value.status_code == 502


@pytest.mark.parametrize("call", READS)
def test_the_full_exception_still_reaches_the_server_log(call, route_env, caplog):
    """The address belongs in the log — this is about what crosses to a client."""
    route_env(_transport_error())

    with caplog.at_level(logging.ERROR, logger="condor.web.routes.executors"):
        with pytest.raises(HTTPException):
            call()

    assert BACKEND_HOST in caplog.text, "diagnostics were lost, not just redacted"


def test_no_read_site_stringifies_the_exception_into_the_detail():
    """The pattern SEC-116 removed must not creep back into this module."""
    from pathlib import Path

    import condor.web.routes.executors as module

    source = Path(module.__file__).read_text()

    assert "detail=str(e)" not in source
    assert "detail=str(exc)" not in source

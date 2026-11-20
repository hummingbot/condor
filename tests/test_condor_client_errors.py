"""``call_main_api`` says which of three things went wrong (MC-11).

A timeout, a refused connection and a non-JSON 500 all used to read "Failed to
reach main process API", and each needs a different next move: wait and check,
retry shortly, or read what the route said. Driven against a throwaway aiohttp
server on a loopback port — never the app.
"""

import asyncio

import pytest
from aiohttp import web

from mcp_servers.condor import condor_client
from mcp_servers.condor.exceptions import (
    APIError,
    APIHTTPError,
    APITimeout,
    APIUnreachable,
)


def _run(monkeypatch, handler, call, *, serve=True):
    """Serve ``handler`` at /api/v1/x and run ``call()`` against it."""
    hits: list[str] = []

    async def counted(request):
        hits.append(request.method)
        return await handler(request)

    async def main():
        app = web.Application()
        app.router.add_route("*", "/api/v1/x", counted)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        if not serve:
            await runner.cleanup()
        monkeypatch.setattr("utils.config.WEB_PORT", port)
        monkeypatch.setattr(condor_client, "_RETRY_BACKOFF", (0.01, 0.01))
        try:
            return await call()
        finally:
            await condor_client._get_session().close()
            if serve:
                await runner.cleanup()

    return asyncio.run(main()), hits


async def _ok(request):
    return web.json_response({"fine": True})


def test_a_json_answer_comes_back_parsed(monkeypatch):
    result, _ = _run(monkeypatch, _ok, lambda: condor_client.call_main_api("GET", "/x"))
    assert result == {"fine": True}


def test_a_timeout_says_the_request_may_still_be_running(monkeypatch):
    async def slow(request):
        await asyncio.sleep(0.5)
        return web.json_response({})

    with pytest.raises(APITimeout) as exc:
        _run(
            monkeypatch,
            slow,
            lambda: condor_client.call_main_api("POST", "/x", {}, timeout=0.05),
        )

    message = str(exc.value)
    assert "0.05s" in message
    assert "may still be executing" in message
    assert "before retrying" in message


def test_a_route_error_carries_its_detail(monkeypatch):
    async def refused(request):
        return web.json_response(
            {"detail": "Cannot delete a running strategy"}, status=400
        )

    with pytest.raises(APIHTTPError) as exc:
        _run(monkeypatch, refused, lambda: condor_client.call_main_api("DELETE", "/x"))

    assert exc.value.status == 400
    assert str(exc.value) == "API error (400): Cannot delete a running strategy"


def test_a_plain_text_500_is_an_http_error_not_unreachable(monkeypatch):
    async def crashed(request):
        return web.Response(text="Internal Server Error", status=500)

    with pytest.raises(APIHTTPError) as exc:
        _run(monkeypatch, crashed, lambda: condor_client.call_main_api("GET", "/x"))

    assert exc.value.status == 500
    assert "Internal Server Error" in str(exc.value)


def test_a_refused_connection_says_restarting_and_retries_a_get(monkeypatch):
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        if delay:  # aiohttp yields with sleep(0); only the backoff is counted
            slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(condor_client.asyncio, "sleep", fake_sleep)

    with pytest.raises(APIUnreachable) as exc:
        _run(
            monkeypatch,
            _ok,
            lambda: condor_client.call_main_api("GET", "/x"),
            serve=False,
        )

    assert "not accepting connections" in str(exc.value)
    assert "was not carried out" in str(exc.value)
    assert len(slept) == 2  # two retries after the first attempt


def test_a_refused_post_is_not_retried(monkeypatch):
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        if delay:
            slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(condor_client.asyncio, "sleep", fake_sleep)

    with pytest.raises(APIUnreachable):
        _run(
            monkeypatch,
            _ok,
            lambda: condor_client.call_main_api("POST", "/x", {}),
            serve=False,
        )

    assert slept == []


def test_every_failure_is_still_an_api_error():
    """Callers that catch the base keep working."""
    assert issubclass(APITimeout, APIError)
    assert issubclass(APIUnreachable, APIError)
    assert issubclass(APIHTTPError, APIError)


def test_the_session_and_the_token_are_reused_across_calls(monkeypatch):
    minted: list[int] = []
    from condor.web import auth

    real = auth.create_jwt

    def counting(user_id, **kw):
        minted.append(user_id)
        return real(user_id, **kw)

    monkeypatch.setattr(auth, "create_jwt", counting)
    monkeypatch.setattr(condor_client, "_token", None)
    sessions: list = []

    async def twice():
        for _ in range(3):
            await condor_client.call_main_api("GET", "/x")
            sessions.append(condor_client._get_session())

    _, hits = _run(monkeypatch, _ok, twice)

    assert hits == ["GET"] * 3
    assert len(minted) == 1
    assert len({id(s) for s in sessions}) == 1

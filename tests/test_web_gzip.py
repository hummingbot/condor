"""JSON is compressed on the way out; the SSE stream is not buffered (XC-11 / WS-07).

``GZipMiddleware`` is the outermost layer of the dashboard app. Two things have
to hold at once, and the second is the one that can break silently:

* a JSON body over the threshold leaves gzip-encoded for a client that accepts
  it, and a small one is left alone;
* ``POST /sessions/{key}/prompt`` — the only streaming route — still reaches the
  client **event by event**. A compressor that waited for more input before
  emitting anything would turn a live turn into one delayed blob.

The streaming half is driven at the ASGI level on purpose: the test client
collects a whole response before handing it back, so it cannot tell a stream
from a buffer. Here the generator is gated, and each event must have crossed
the *entire* middleware stack before the next one is allowed to exist.
"""

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import sessions as sessions_routes

USER = WebUser(id=1, username="u", first_name="U", role="user")


@pytest.fixture
def app():
    from condor.web.app import create_app

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: USER
    return app


def _front(app):
    """Move the route just added ahead of the SPA catch-all."""
    app.router.routes.insert(0, app.router.routes.pop())


# ── JSON ──


def test_a_large_json_response_is_gzipped(app):
    rows = [{"id": n, "name": f"executor-{n}"} for n in range(200)]

    @app.get("/api/v1/_gzip_probe")
    async def _probe():
        return {"rows": rows}

    _front(app)
    res = TestClient(app).get(
        "/api/v1/_gzip_probe", headers={"Accept-Encoding": "gzip"}
    )

    assert res.status_code == 200
    assert res.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in res.headers["vary"]
    assert res.json() == {"rows": rows}
    assert int(res.headers["content-length"]) < len(json.dumps({"rows": rows}))


def test_a_small_json_response_is_left_alone(app):
    @app.get("/api/v1/_gzip_probe")
    async def _probe():
        return {"ok": True}

    _front(app)
    res = TestClient(app).get(
        "/api/v1/_gzip_probe", headers={"Accept-Encoding": "gzip"}
    )

    assert "content-encoding" not in res.headers
    assert res.json() == {"ok": True}


def test_a_client_that_does_not_accept_gzip_gets_plain_json(app):
    @app.get("/api/v1/_gzip_probe")
    async def _probe():
        return {"rows": ["x" * 50] * 100}

    _front(app)
    res = TestClient(app).get(
        "/api/v1/_gzip_probe", headers={"Accept-Encoding": "identity"}
    )

    assert "content-encoding" not in res.headers
    assert len(res.json()["rows"]) == 100


# ── Streaming ──


async def _drive(app, scope, body: bytes, on_message):
    """Run one request straight against the ASGI app, reporting every send."""
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()  # the client stays connected

    async def send(message):
        await on_message(message)

    await app(scope, receive, send)


def _post_scope(path: str, accept_encoding: str) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"accept-encoding", accept_encoding.encode()),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


EVENTS = [f"data: {json.dumps({'n': n, 'pad': 'x' * 600})}\n\n" for n in range(3)]


def test_the_prompt_stream_still_arrives_event_by_event(app, monkeypatch):
    """Each event reaches the client before the next one is even produced."""

    async def scenario():
        produced: list[int] = []
        release = [asyncio.Event() for _ in EVENTS]
        delivered = [asyncio.Event() for _ in EVENTS]

        async def gated_events(_source):
            for n, event in enumerate(EVENTS):
                await release[n].wait()
                produced.append(n)
                yield event

        async def _authorized(*_a, **_k):
            return None

        monkeypatch.setattr(sessions_routes, "event_stream", gated_events)
        monkeypatch.setattr(sessions_routes, "_authorized_info", _authorized)
        monkeypatch.setattr(sessions_routes, "_parse_key", lambda raw: raw)
        monkeypatch.setattr(sessions_routes.runtime, "prompt", lambda *a, **k: None)

        start: dict = {}
        received = bytearray()

        async def on_message(message):
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                received.extend(message.get("body", b""))
                for n, event in enumerate(EVENTS):
                    if event.encode() in received:
                        delivered[n].set()

        request = asyncio.ensure_future(
            _drive(
                app,
                _post_scope("/api/v1/sessions/k/prompt", "gzip, deflate, br"),
                json.dumps({"text": "hi"}).encode(),
                on_message,
            )
        )

        for n in range(len(EVENTS)):
            release[n].set()
            # Delivered in the clear, while every later event is still gated:
            # nothing in the stack is holding it back to fill a buffer.
            await asyncio.wait_for(delivered[n].wait(), 5.0)
            assert produced == list(range(n + 1))

        await asyncio.wait_for(request, 5.0)
        return start, bytes(received)

    start, body = asyncio.run(scenario())

    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert start["status"] == 200
    assert headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in headers, "the SSE stream must not be compressed"
    assert headers.get("x-accel-buffering") == "no"
    assert body.decode() == "".join(EVENTS)


def test_any_other_streamed_response_is_flushed_chunk_by_chunk(app):
    """A streaming route that *is* compressed must still not be held back.

    Nothing but the prompt stream streams today. This pins the property a
    future one would rely on: every chunk is sync-flushed, so what has been
    sent so far always decompresses to what has been produced so far.
    """
    from fastapi.responses import StreamingResponse

    chunks = [("line %d " % n) * 200 for n in range(3)]

    async def scenario():
        release = [asyncio.Event() for _ in chunks]
        progressed = asyncio.Event()

        async def gated():
            for n, chunk in enumerate(chunks):
                await release[n].wait()
                yield chunk

        @app.post("/api/v1/_stream_probe")
        async def _probe():
            return StreamingResponse(gated(), media_type="text/plain")

        _front(app)

        import zlib

        start: dict = {}
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        plain = bytearray()

        async def on_message(message):
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                plain.extend(inflater.decompress(message.get("body", b"")))
                progressed.set()

        request = asyncio.ensure_future(
            _drive(app, _post_scope("/api/v1/_stream_probe", "gzip"), b"", on_message)
        )

        for n in range(len(chunks)):
            release[n].set()
            expected = "".join(chunks[: n + 1]).encode()
            while bytes(plain) != expected:
                progressed.clear()
                await asyncio.wait_for(progressed.wait(), 5.0)

        await asyncio.wait_for(request, 5.0)
        return start, bytes(plain)

    start, plain = asyncio.run(scenario())

    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert headers["content-encoding"] == "gzip"
    assert plain.decode() == "".join(chunks)

"""Network posture (§5.5 / §11): non-loopback bind rejected at startup;
non-loopback Host and foreign WS Origin refused (DNS rebinding); a
cross-origin form POST to a mutating route is rejected without the
per-process token and succeeds with it."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from condor.web.security import (
    CSRF_TOKEN,
    LoopbackPostureMiddleware,
    validate_bind_host,
    websocket_origin_allowed,
)


def _make_app():
    app = FastAPI()
    app.add_middleware(LoopbackPostureMiddleware)

    @app.get("/read")
    async def read():
        return {"ok": True}

    @app.post("/mutate")
    async def mutate():
        return {"mutated": True}

    return app


@pytest.fixture
def client():
    return TestClient(_make_app(), base_url="http://127.0.0.1")


def test_bind_host_rejects_non_loopback():
    validate_bind_host("127.0.0.1")
    validate_bind_host("localhost")
    validate_bind_host("::1")
    with pytest.raises(SystemExit):
        validate_bind_host("0.0.0.0")
    with pytest.raises(SystemExit):
        validate_bind_host("192.168.1.10")
    with pytest.raises(SystemExit):
        validate_bind_host("myhost.local")


def test_non_loopback_host_header_rejected(client):
    assert client.get("/read").status_code == 200
    # DNS rebinding: a hostile page resolves evil.example → 127.0.0.1; the
    # request arrives locally but carries the foreign Host.
    r = client.get("/read", headers={"host": "evil.example"})
    assert r.status_code == 400
    r = client.get("/read", headers={"host": "127.0.0.1:3000"})
    assert r.status_code == 200
    r = client.get("/read", headers={"host": "localhost:3000"})
    assert r.status_code == 200


def test_cross_origin_form_post_rejected_without_token(client):
    """Valid loopback Host, foreign/absent Origin+Referer, no token → 403.
    CORS does not stop a form POST's side effects; this does."""
    # Foreign Origin (the hostile page's form POST).
    r = client.post(
        "/mutate",
        headers={"origin": "https://evil.example"},
        data={"x": "1"},
    )
    assert r.status_code == 403
    # Absent Origin AND absent Referer (non-browser or stripped) → also
    # rejected without the token: fail closed.
    r = client.post("/mutate", data={"x": "1"})
    assert r.status_code == 403
    # Foreign Referer with no Origin.
    r = client.post(
        "/mutate", headers={"referer": "https://evil.example/page"}, data={"x": "1"}
    )
    assert r.status_code == 403


def test_loopback_origin_or_token_allows_post(client):
    r = client.post("/mutate", headers={"origin": "http://localhost:3000"})
    assert r.status_code == 200
    r = client.post("/mutate", headers={"origin": "http://127.0.0.1:3000"})
    assert r.status_code == 200
    # No Origin, loopback Referer.
    r = client.post("/mutate", headers={"referer": "http://127.0.0.1:3000/app"})
    assert r.status_code == 200
    # The dashboard's header path: per-process token.
    r = client.post("/mutate", headers={"x-condor-token": CSRF_TOKEN})
    assert r.status_code == 200
    # Wrong token stays rejected.
    r = client.post("/mutate", headers={"x-condor-token": "nope"})
    assert r.status_code == 403


class _FakeWS:
    def __init__(self, headers):
        self.headers = headers


def test_websocket_origin_validation():
    ok = _FakeWS({"host": "127.0.0.1:3000", "origin": "http://localhost:3000"})
    assert websocket_origin_allowed(ok) is True
    foreign_origin = _FakeWS({"host": "127.0.0.1:3000", "origin": "https://evil.example"})
    assert websocket_origin_allowed(foreign_origin) is False
    foreign_host = _FakeWS({"host": "evil.example", "origin": "http://localhost:3000"})
    assert websocket_origin_allowed(foreign_host) is False
    # Non-browser client: no Origin, loopback Host — allowed.
    no_origin = _FakeWS({"host": "localhost:3000"})
    assert websocket_origin_allowed(no_origin) is True


def test_csrf_token_is_not_the_direct_token(tmp_path, monkeypatch):
    """X-Condor-Token (browser-exposed CSRF) and store/.direct-token
    (execution bootstrap) are separate, independently generated secrets."""
    import condor.executors.capabilities as caps

    monkeypatch.setattr(caps, "DIRECT_TOKEN_PATH", tmp_path / ".direct-token")
    registry = caps.CapabilityRegistry()
    direct = registry.write_direct_token()
    assert direct != CSRF_TOKEN
    # ...and the direct-register path rejects the CSRF token outright.
    with pytest.raises(Exception):
        registry.register_direct(CSRF_TOKEN, "conn1")

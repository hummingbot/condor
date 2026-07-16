"""Chat WS client-id continuity (§5.5 final step, auth pass).

No per-request auth remains — loopback posture (websocket_origin_allowed)
is the sole gate. The optional ``?client=`` query param lets a browser
resume its session slots across reconnects: a missing/malformed value mints
a fresh uuid4 hex; a well-formed one is echoed back and reused.
"""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from condor.web.routes import chat_ws

HEX32 = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        "condor.web.security.websocket_origin_allowed", lambda ws: True
    )
    app = FastAPI()
    app.include_router(chat_ws.router)
    return TestClient(app, base_url="http://127.0.0.1")


def test_resolve_client_id_mints_fresh_hex_when_absent():
    minted = chat_ws._resolve_client_id(None)
    assert HEX32.match(minted)


def test_resolve_client_id_mints_fresh_hex_when_malformed():
    minted = chat_ws._resolve_client_id("not-a-valid-id")
    assert HEX32.match(minted)
    assert minted != "not-a-valid-id"


def test_resolve_client_id_reuses_well_formed_id():
    requested = "a" * 32
    assert chat_ws._resolve_client_id(requested) == requested


def test_two_connects_without_client_get_independent_ids(client):
    with client.websocket_connect("/ws/chat") as ws1:
        first = ws1.receive_json()
    with client.websocket_connect("/ws/chat") as ws2:
        second = ws2.receive_json()

    assert first["event"] == "sessions_list"
    assert second["event"] == "sessions_list"
    assert HEX32.match(first["client_id"])
    assert HEX32.match(second["client_id"])
    assert first["client_id"] != second["client_id"]


def test_reconnect_with_client_id_is_echoed_back(client):
    with client.websocket_connect("/ws/chat") as ws1:
        first = ws1.receive_json()
    client_id = first["client_id"]

    with client.websocket_connect(f"/ws/chat?client={client_id}") as ws2:
        second = ws2.receive_json()

    assert second["client_id"] == client_id

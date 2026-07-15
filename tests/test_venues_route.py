"""Native wallet onboarding route: import seals secrets, never leaks them,
admin-gated (#7)."""

import base64
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from condor.executors import wallets
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import venues

_KEY = base64.b64encode(b"r" * 32).decode()


def _client(tmp_path, monkeypatch, role="admin"):
    monkeypatch.setenv("CONDOR_SECRETS_KEY", _KEY)
    monkeypatch.setattr(wallets, "_VENUES_PATH", tmp_path / "venues.json")
    app = FastAPI()
    app.include_router(venues.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(id=1, role=role)
    return TestClient(app)


def test_import_venue_seals_and_hides_secret(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/venues/hyperliquid", json={
        "fields": {"agent_private_key": "0xSECRET", "account_address": "0xpub"}})
    assert r.status_code == 200, r.text
    assert set(r.json()["fields"]) == {"agent_private_key", "account_address"}
    assert "0xSECRET" not in r.text  # secret never returned
    raw = json.loads((tmp_path / "venues.json").read_text())["hyperliquid"]
    assert raw["agent_private_key"].startswith("enc:v1:")  # sealed on disk
    listed = c.get("/venues").json()["venues"]
    assert "agent_private_key" in listed["hyperliquid"]


def test_import_venue_requires_admin(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, role="user")
    r = c.post("/venues/hyperliquid", json={"fields": {"agent_private_key": "0xk"}})
    assert r.status_code == 403


def test_import_unknown_venue_422(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/venues/nope", json={"fields": {"x": "y"}})
    assert r.status_code == 422

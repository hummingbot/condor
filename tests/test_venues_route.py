"""Account-management routes (§6.2b Phase 3): onboarding derives + probes,
secrets never leak, custody identity is revalidated on edit, busy venues
reject mutations (409), removal warns, default selection works. No network —
probes are monkeypatched."""

import base64
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from solders.keypair import Keypair

import condor.executors.wallets as wallets
from condor.accounts import onboarding
from condor.executors.log import ExecutorLog
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import venues

_KEY = base64.b64encode(b"r" * 32).decode()
HL_KEY = "0x" + "11" * 32
HL_ACCT = "0x" + "ab" * 20
HL_ACCT_2 = "0x" + "cd" * 20


@pytest.fixture()
def client(tmp_path, monkeypatch):
    return _client(tmp_path, monkeypatch)


def _client(tmp_path, monkeypatch, role="admin"):
    monkeypatch.setenv("CONDOR_SECRETS_KEY", _KEY)
    monkeypatch.setattr(wallets, "_VENUES_PATH", tmp_path / "venues.json")
    monkeypatch.setitem(onboarding._PROBERS, "hyperliquid", lambda *a: None)
    monkeypatch.setitem(onboarding._PROBERS, "solana", lambda *a: None)
    # Busy guard reads the durable executor log — point it at an (empty) tmp
    # root so repo-local agent state never bleeds into tests.
    monkeypatch.setattr(venues, "ExecutorLog", lambda: ExecutorLog(tmp_path / "agents"))
    app = FastAPI()
    app.include_router(venues.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(id=1, role=role)
    return TestClient(app)


def _onboard(client, address=HL_ACCT, name="main", venue="hyperliquid"):
    r = client.post(
        f"/venues/{venue}/accounts",
        json={"credentials": {"agent_private_key": HL_KEY, "account_address": address},
              "name": name},
    )
    assert r.status_code == 200, r.text
    return r


def _seed_busy(tmp_path, venue="hyperliquid"):
    elog = ExecutorLog(tmp_path / "agents")
    elog._append("_manual", {
        "ts": time.time(), "event": "opened", "id": "x1", "type": "order_perp",
        "status": "SUBMITTING", "config": {"venue": venue}, "state": {},
    })


# -- onboarding + listing --


def test_onboard_and_list_never_leak_secrets(client):
    r = _onboard(client)
    body = r.json()
    assert body["account"] == {"address": HL_ACCT, "name": "main", "is_default": True}
    assert HL_KEY not in r.text
    listing = client.get("/venues")
    assert HL_KEY not in listing.text and "enc:v1:" not in listing.text
    hl = listing.json()["venues"]["hyperliquid"]
    assert hl["default_account"] == HL_ACCT
    assert hl["accounts"] == [{"address": HL_ACCT, "name": "main", "is_default": True}]


def test_onboard_probe_failure_422(client, monkeypatch):
    monkeypatch.setitem(
        onboarding._PROBERS, "hyperliquid",
        lambda *a: (_ for _ in ()).throw(RuntimeError("venue down")),
    )
    r = client.post(
        "/venues/hyperliquid/accounts",
        json={"credentials": {"agent_private_key": HL_KEY, "account_address": HL_ACCT}},
    )
    assert r.status_code == 422
    assert "NOT enabled" in r.json()["detail"]
    assert client.get("/venues").json()["venues"] == {}


def test_onboard_mismatched_typed_address_422(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    kp, other = Keypair(), Keypair()
    r = client.post(
        "/venues/solana/accounts",
        json={"credentials": {"secret_key_b58": str(kp), "address": str(other.pubkey())}},
    )
    assert r.status_code == 422
    assert "never typed" in r.json()["detail"]


def test_duplicate_display_name_422(client):
    _onboard(client, HL_ACCT, name="main")
    r = client.post(
        "/venues/hyperliquid/accounts",
        json={"credentials": {"agent_private_key": HL_KEY, "account_address": HL_ACCT_2},
              "name": "MAIN"},
    )
    assert r.status_code == 422
    assert "duplicate display name" in r.json()["detail"]


def test_mutations_require_admin(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, role="user")
    body = {"credentials": {"agent_private_key": HL_KEY, "account_address": HL_ACCT}}
    assert client.post("/venues/hyperliquid/accounts", json=body).status_code == 403
    assert client.put(f"/venues/hyperliquid/accounts/{HL_ACCT}", json={"name": "x"}).status_code == 403
    assert client.delete(f"/venues/hyperliquid/accounts/{HL_ACCT}").status_code == 403
    assert client.post("/venues/hyperliquid/default", json={"account": "x"}).status_code == 403
    assert client.get("/venues").status_code == 200  # reads stay available


# -- edit --


def test_edit_credentials_custody_must_match_key(client):
    _onboard(client, HL_ACCT)
    r = client.put(
        f"/venues/hyperliquid/accounts/{HL_ACCT}",
        json={"credentials": {"agent_private_key": HL_KEY, "account_address": HL_ACCT_2}},
    )
    assert r.status_code == 422
    assert "NEW account" in r.json()["detail"]
    # same custody → allowed (rotation: new signer key, same address)
    r = client.put(
        f"/venues/hyperliquid/accounts/{HL_ACCT}",
        json={"credentials": {"agent_private_key": "0x" + "33" * 32,
                              "account_address": HL_ACCT}},
    )
    assert r.status_code == 200, r.text
    assert wallets.load_hyperliquid_creds()["agent_private_key"] == "0x" + "33" * 32


def test_rename_preserves_resolution_by_address(client):
    _onboard(client, HL_ACCT, name="main")
    r = client.put(f"/venues/hyperliquid/accounts/{HL_ACCT}", json={"name": "primary"})
    assert r.status_code == 200
    assert r.json()["account"]["name"] == "primary"
    store = wallets.account_store()
    assert store.resolve("hyperliquid", HL_ACCT).custody_address == HL_ACCT
    assert store.resolve("hyperliquid", "primary").custody_address == HL_ACCT
    # credentials untouched by the rename
    assert wallets.load_hyperliquid_creds("primary")["agent_private_key"] == HL_KEY


def test_edit_unknown_account_404(client):
    assert client.put(
        f"/venues/hyperliquid/accounts/{HL_ACCT}", json={"name": "x"}
    ).status_code == 404


# -- busy guard --


def test_busy_venue_rejects_edit_and_remove(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _onboard(client, HL_ACCT)
    _seed_busy(tmp_path, "hyperliquid")  # durable nonterminal record
    r = client.put(f"/venues/hyperliquid/accounts/{HL_ACCT}", json={"name": "x"})
    assert r.status_code == 409
    assert "nonterminal executors" in r.json()["detail"]
    assert client.delete(f"/venues/hyperliquid/accounts/{HL_ACCT}").status_code == 409
    # a DIFFERENT venue is not blocked
    kp = Keypair()
    r = client.post("/venues/solana/accounts", json={"credentials": {"secret_key_b58": str(kp)}})
    assert r.status_code == 200
    assert client.delete(f"/venues/solana/accounts/{kp.pubkey()}").status_code == 200


def test_terminal_records_do_not_block(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _onboard(client, HL_ACCT)
    elog = ExecutorLog(tmp_path / "agents")
    elog._append("_manual", {
        "ts": time.time(), "event": "closed", "id": "x2", "type": "order_perp",
        "status": "CLOSED", "config": {"venue": "hyperliquid"}, "state": {},
    })
    assert client.put(
        f"/venues/hyperliquid/accounts/{HL_ACCT}", json={"name": "x"}
    ).status_code == 200


# -- remove + default --


def test_remove_warns_and_default_removal_leaves_no_default(client):
    _onboard(client, HL_ACCT, name="main")
    _onboard(client, HL_ACCT_2, name="alt")
    r = client.delete(f"/venues/hyperliquid/accounts/{HL_ACCT}")  # the default
    assert r.status_code == 200
    assert "no longer manage" in r.json()["warning"]
    hl = client.get("/venues").json()["venues"]["hyperliquid"]
    assert hl["default_account"] is None
    assert hl["accounts"] == [{"address": HL_ACCT_2, "name": "alt", "is_default": False}]
    # a spec that omits account now fails at resolve time with a clear error
    from condor.accounts import AccountResolutionError
    with pytest.raises(AccountResolutionError, match="no default_account"):
        wallets.load_hyperliquid_creds()


def test_set_default_by_name(client):
    _onboard(client, HL_ACCT, name="main")
    _onboard(client, HL_ACCT_2, name="alt")
    r = client.post("/venues/hyperliquid/default", json={"account": "alt"})
    assert r.status_code == 200
    assert r.json()["default_account"] == HL_ACCT_2
    assert wallets.load_hyperliquid_creds()["account_address"] == HL_ACCT_2
    assert client.post(
        "/venues/hyperliquid/default", json={"account": "nope"}
    ).status_code == 404


def test_remove_unknown_account_404(client):
    assert client.delete(f"/venues/hyperliquid/accounts/{HL_ACCT}").status_code == 404

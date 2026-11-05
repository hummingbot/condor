"""`GET /settings/hub-device/linked`, the non-admin-safe sibling of
`GET /settings/hub-device` (FEAT-153).

Connecting this install to Botcamp, and reading the full connection state
(profile handle and scope), is admin-only — `_require_hub_admin` in
``condor/web/routes/settings.py``. `/hub`'s Pull agents gate needs to know
whether this install is linked for *every* signed-in seat, not just the
admin, so it reads this route instead: the one bit of that state ("linked"
or not) with nothing else attached.

It answers from ``condor.hub.is_linked`` — local config only, same as the
admin-only full state route's ``connection_state`` (botcamp-site PR #137's
connect-code flow has no poll to drive forward at all, unlike the device
flow it replaced).
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.hub as hub
import condor.web.routes.settings as settings_routes
from condor.hub import ConnectionState
from condor.web.auth import get_current_user
from condor.web.models import WebUser

ADMIN = WebUser(id=1, username="admin", first_name="A", role="admin")
TRADER = WebUser(id=2, username="trader", first_name="T", role="user")


class FakeConfigManager:
    def is_admin(self, user_id):
        return user_id == ADMIN.id


def build(monkeypatch, *, linked: bool = False, state: ConnectionState | None = None):
    cm = FakeConfigManager()
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    monkeypatch.setattr(hub, "is_linked", lambda: linked)
    if state is not None:
        monkeypatch.setattr(hub, "connection_state", lambda: state)
    app = FastAPI()
    app.include_router(settings_routes.router)
    return app


def as_user(app, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_reports_true_once_connected(monkeypatch):
    app = build(monkeypatch, linked=True)
    body = as_user(app, TRADER).get("/settings/hub-device/linked").json()
    assert body == {"linked": True}


def test_reports_false_before_connecting(monkeypatch):
    app = build(monkeypatch, linked=False)
    body = as_user(app, TRADER).get("/settings/hub-device/linked").json()
    assert body == {"linked": False}


def test_open_to_a_non_admin_seat(monkeypatch):
    app = build(monkeypatch, linked=True)
    assert as_user(app, TRADER).get("/settings/hub-device/linked").status_code == 200


def test_carries_nothing_but_the_one_bit(monkeypatch):
    app = build(monkeypatch, linked=True)
    body = as_user(app, TRADER).get("/settings/hub-device/linked").json()
    assert body == {"linked": True}


def test_a_non_admin_seat_never_reaches_the_admin_only_connection_state(monkeypatch):
    """The non-admin-safe route must never call ``connection_state`` — that
    is the admin-only function, gated by ``_require_hub_admin`` on the sibling
    route, not this one."""
    calls = []
    monkeypatch.setattr(
        hub, "connection_state", lambda: calls.append(1) or ConnectionState(status="none")
    )
    app = build(monkeypatch, linked=False)
    as_user(app, TRADER).get("/settings/hub-device/linked")
    assert calls == []


def test_the_full_state_stays_admin_only(monkeypatch):
    app = build(
        monkeypatch,
        linked=True,
        state=ConnectionState(status="linked", profile_handle="@fede"),
    )
    assert as_user(app, TRADER).get("/settings/hub-device").status_code == 403
    assert as_user(app, ADMIN).get("/settings/hub-device").status_code == 200

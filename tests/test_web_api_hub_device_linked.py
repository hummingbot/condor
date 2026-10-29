"""`GET /settings/hub-device/linked`, the non-admin-safe sibling of
`GET /settings/hub-device` (FEAT-153).

Connecting this install to Botcamp, and reading the full device-login state
(profile handle, a pending user code), is admin-only — `_require_hub_admin`
in ``condor/web/routes/settings.py``. `/hub`'s Pull agents gate needs to know
whether this install is linked for *every* signed-in seat, not just the
admin, so it reads this route instead: the one bit of that state ("linked"
or not) with nothing else attached.

It answers from ``condor.hub.is_linked`` — local config, never a poll
(BUG-3 case A: a non-admin hitting this while the admin's device login was
mid-flight used to steal the one-time code exchange and report the admin's
next poll as ``invalid``). ``device_state`` stays what the admin-only full
state route reads.
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.hub as hub
import condor.web.routes.settings as settings_routes
from condor.hub import DeviceLinkState
from condor.web.auth import get_current_user
from condor.web.models import WebUser

ADMIN = WebUser(id=1, username="admin", first_name="A", role="admin")
TRADER = WebUser(id=2, username="trader", first_name="T", role="user")


class FakeConfigManager:
    def is_admin(self, user_id):
        return user_id == ADMIN.id


def build(monkeypatch, *, linked: bool = False, state: DeviceLinkState | None = None):
    cm = FakeConfigManager()
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    monkeypatch.setattr(hub, "is_linked", lambda: linked)
    if state is not None:
        monkeypatch.setattr(hub, "device_state", lambda: state)
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


def test_reports_false_while_a_device_login_is_merely_pending(monkeypatch):
    # A pending device login never sets a token, so is_linked() stays false
    # without this route ever touching the pending poll state (BUG-3 case A).
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


def test_a_non_admin_seat_never_drives_the_pending_poll(monkeypatch):
    """BUG-3 case A: hitting this route must never call ``device_state`` —
    that is the function that polls Botcamp and spends the one-time code.
    """
    calls = []
    monkeypatch.setattr(
        hub, "device_state", lambda: calls.append(1) or DeviceLinkState(status="none")
    )
    app = build(monkeypatch, linked=False)
    as_user(app, TRADER).get("/settings/hub-device/linked")
    assert calls == []


def test_the_full_state_stays_admin_only(monkeypatch):
    app = build(
        monkeypatch,
        linked=True,
        state=DeviceLinkState(status="linked", profile_handle="@fede"),
    )
    assert as_user(app, TRADER).get("/settings/hub-device").status_code == 403
    assert as_user(app, ADMIN).get("/settings/hub-device").status_code == 200

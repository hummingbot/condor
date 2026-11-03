"""The preferences endpoint must not be a back door to the code_run grant.

``user_preferences`` holds ordinary UI settings *and* the ``code_run``
capability grant, and ``PATCH /auth/preferences`` merges a caller-supplied dict
into it. Unfiltered, that let any approved seat write itself the grant and reach
the unsandboxed runner behind ``/code/run``, walking straight past the
admin-only, audited path in ``routes/admin.py`` (SEC-250).

So: reserved keys are refused on the way in, ordinary keys still merge, and the
admin grant path is untouched.
"""

import asyncio

import pytest
from fastapi import HTTPException

import config_manager as cm_module
from condor.web import auth as web_auth
from condor.web.models import WebUser
from condor.web.routes import admin as admin_routes
from condor.web.routes import auth as auth_routes
from condor.web.routes import code as code_routes
from condor.web.routes.admin import CodeRunGrantRequest, set_code_run_grant
from condor.web.routes.auth import (
    PreferencesUpdate,
    get_preferences,
    update_preferences,
)
from condor.web.routes.code import RunCodeRequest, run_code

ADMIN = WebUser(id=1, username="root", role="admin")
SEAT = WebUser(id=2, username="quant", role="user")


@pytest.fixture
def cm(tmp_path, monkeypatch):
    """A real ConfigManager on a throwaway config.yml — persistence included."""
    monkeypatch.chdir(tmp_path)  # audit_log.yml is written relative to cwd
    manager = cm_module.ConfigManager(config_path=str(tmp_path / "config.yml"))
    manager._data["users"] = {
        ADMIN.id: {"user_id": ADMIN.id, "username": "root", "role": "admin"},
        SEAT.id: {"user_id": SEAT.id, "username": "quant", "role": "user"},
    }
    manager._data["user_preferences"] = {}
    manager._audit_log = []
    manager._save_config()
    for module in (cm_module, web_auth, admin_routes, auth_routes, code_routes):
        monkeypatch.setattr(module, "get_config_manager", lambda: manager)
    return manager


def _patch(actor, updates):
    return asyncio.run(
        update_preferences(PreferencesUpdate(updates=updates), user=actor)
    )


# ── the escalation itself ──


def test_a_seat_cannot_self_grant_code_run_through_preferences(cm):
    """The whole point: PATCH is not a second, unaudited grant endpoint."""
    with pytest.raises(HTTPException) as excinfo:
        _patch(SEAT, {"code_run": True})

    assert excinfo.value.status_code == 403
    assert cm.has_code_run_grant(SEAT.id) is False


def test_the_code_run_gate_still_refuses_that_seat_afterwards(cm):
    """Refusing the write is only worth as much as the door it keeps shut."""
    with pytest.raises(HTTPException):
        _patch(SEAT, {"code_run": True})

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(run_code(RunCodeRequest(code="import os"), user=SEAT))

    assert excinfo.value.status_code == 403


def test_a_reserved_key_smuggled_beside_ordinary_ones_takes_nothing_with_it(cm):
    """All-or-nothing: a mixed body must not land its innocent half either."""
    with pytest.raises(HTTPException) as excinfo:
        _patch(SEAT, {"theme": "dark", "code_run": True})

    assert excinfo.value.status_code == 403
    assert cm.has_code_run_grant(SEAT.id) is False
    assert "theme" not in cm.get_user_preferences(SEAT.id)


def test_even_a_falsey_reserved_write_is_refused(cm):
    """A seat revoking its own grant is still a seat writing a capability."""
    cm.set_code_run_grant(SEAT.id, True, admin_id=ADMIN.id)

    with pytest.raises(HTTPException) as excinfo:
        _patch(SEAT, {"code_run": False})

    assert excinfo.value.status_code == 403
    assert cm.has_code_run_grant(SEAT.id) is True


def test_an_admin_gets_no_shortcut_through_preferences_either(cm):
    """One writer for the grant, whoever is asking — admins use the audited route."""
    with pytest.raises(HTTPException) as excinfo:
        _patch(ADMIN, {"code_run": True})

    assert excinfo.value.status_code == 403
    assert cm.has_code_run_grant(ADMIN.id) is False


# ── ordinary preferences are untouched ──


def test_ordinary_preferences_still_merge_and_come_back(cm):
    out = _patch(SEAT, {"theme": "dark", "rows": 25})

    assert out["theme"] == "dark"
    assert out["rows"] == 25
    assert asyncio.run(get_preferences(user=SEAT)) == out


def test_a_later_merge_keeps_the_earlier_keys(cm):
    _patch(SEAT, {"theme": "dark"})
    out = _patch(SEAT, {"rows": 25})

    assert out == {"theme": "dark", "rows": 25}


# ── the manager refuses the bulk write on its own ──


def test_set_user_preferences_refuses_reserved_keys_without_the_route(cm):
    """Belt and braces: a future route must not be able to reopen the hole."""
    with pytest.raises(ValueError):
        cm.set_user_preferences(SEAT.id, {"code_run": True})

    assert cm.has_code_run_grant(SEAT.id) is False


def test_the_audited_admin_path_still_grants(cm):
    """The guard sits on the bulk merge, not on the setter the admin API uses."""
    asyncio.run(
        set_code_run_grant(SEAT.id, CodeRunGrantRequest(granted=True), user=ADMIN)
    )

    assert cm.has_code_run_grant(SEAT.id) is True
    assert code_routes._may_run_code(SEAT.id) is True
    actions = [e["action"] for e in cm.get_audit_log()]
    assert "code_run_granted" in actions

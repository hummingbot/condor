"""Who may hand out the code_run capability, and what handing it out does.

The grant SEC-151 left as the non-admin escape hatch is arbitrary Python in the
bot's own process. ARCH-177 gave it an admin API instead of a hand edit of
config.yml, so the two things worth pinning down are: only an admin can move it,
and moving it actually moves the gate in ``routes/code.py``.
"""

import asyncio

import pytest
from fastapi import HTTPException

import config_manager as cm_module
from condor.web import auth as web_auth
from condor.web.models import WebUser
from condor.web.routes import admin as admin_routes
from condor.web.routes import code as code_routes
from condor.web.routes.admin import CodeRunGrantRequest, list_users, set_code_run_grant

ADMIN = WebUser(id=1, username="root", role="admin")
SEAT = WebUser(id=2, username="quant", role="user")
OTHER = WebUser(id=3, username="intern", role="user")


@pytest.fixture
def cm(tmp_path, monkeypatch):
    """A real ConfigManager on a throwaway config.yml — persistence included."""
    monkeypatch.chdir(tmp_path)  # audit_log.yml is written relative to cwd
    manager = cm_module.ConfigManager(config_path=str(tmp_path / "config.yml"))
    manager._data["users"] = {
        ADMIN.id: {"user_id": ADMIN.id, "username": "root", "role": "admin"},
        SEAT.id: {"user_id": SEAT.id, "username": "quant", "role": "user"},
        OTHER.id: {"user_id": OTHER.id, "username": "intern", "role": "user"},
    }
    manager._data["user_preferences"] = {}
    manager._audit_log = []
    manager._save_config()
    for module in (cm_module, web_auth, admin_routes, code_routes):
        monkeypatch.setattr(module, "get_config_manager", lambda: manager)
    return manager


def _set(actor, target_id, granted):
    return asyncio.run(
        set_code_run_grant(target_id, CodeRunGrantRequest(granted=granted), user=actor)
    )


# ── only an admin may move the grant ──


def test_a_non_admin_cannot_grant_code_run_to_anyone(cm):
    """Hiding the panel is cosmetic; the refusal has to live on the server."""
    with pytest.raises(HTTPException) as excinfo:
        _set(SEAT, OTHER.id, True)

    assert excinfo.value.status_code == 403
    assert cm.has_code_run_grant(OTHER.id) is False


def test_a_non_admin_cannot_grant_code_run_to_themselves(cm):
    """The interesting attack is self-escalation, not granting a colleague."""
    with pytest.raises(HTTPException) as excinfo:
        _set(SEAT, SEAT.id, True)

    assert excinfo.value.status_code == 403
    assert cm.has_code_run_grant(SEAT.id) is False


def test_a_non_admin_cannot_revoke_an_existing_grant(cm):
    cm.set_code_run_grant(SEAT.id, True, admin_id=ADMIN.id)

    with pytest.raises(HTTPException) as excinfo:
        _set(OTHER, SEAT.id, False)

    assert excinfo.value.status_code == 403
    assert cm.has_code_run_grant(SEAT.id) is True


def test_a_non_admin_cannot_even_list_the_users(cm):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(list_users(user=SEAT))

    assert excinfo.value.status_code == 403


def test_the_role_is_reread_not_trusted_from_the_token(cm):
    """A JWT minted while the caller was an admin must not outlive the role."""
    stale = WebUser(id=SEAT.id, username="quant", role="admin")

    with pytest.raises(HTTPException) as excinfo:
        _set(stale, OTHER.id, True)

    assert excinfo.value.status_code == 403


# ── the grant reaches the gate ──


def test_granting_opens_the_code_run_gate_for_that_user(cm):
    assert code_routes._may_run_code(SEAT.id) is False

    _set(ADMIN, SEAT.id, True)

    assert code_routes._may_run_code(SEAT.id) is True
    assert code_routes._may_run_code(OTHER.id) is False, "grant is per user"


def test_revoking_closes_the_gate_again(cm):
    _set(ADMIN, SEAT.id, True)
    out = _set(ADMIN, SEAT.id, False)

    assert out["code_run"] is False
    assert code_routes._may_run_code(SEAT.id) is False


def test_the_grant_lands_in_config_yml_so_no_restart_is_needed(cm):
    """The gate re-reads the manager each call; a reload must agree with it."""
    _set(ADMIN, SEAT.id, True)

    reloaded = cm_module.ConfigManager(config_path=str(cm.config_path))
    assert reloaded.has_code_run_grant(SEAT.id) is True


def test_an_unrelated_save_does_not_drop_the_grant(cm):
    """_save_config whitelists sections; user_preferences was missing from it."""
    _set(ADMIN, SEAT.id, True)
    cm._data["chat_defaults"][SEAT.id] = "prod"  # any other config write
    cm._save_config()

    reloaded = cm_module.ConfigManager(config_path=str(cm.config_path))
    assert reloaded.has_code_run_grant(SEAT.id) is True


def test_revoking_removes_the_key_rather_than_storing_false(cm):
    _set(ADMIN, SEAT.id, True)
    _set(ADMIN, SEAT.id, False)

    assert code_routes.RUN_CODE_PREFERENCE not in cm.get_user_preferences(SEAT.id)


def test_the_gate_still_passes_admins_without_any_grant(cm):
    """ARCH-177 must not have narrowed the admin arm of the SEC-151 gate."""
    assert cm.has_code_run_grant(ADMIN.id) is False
    assert code_routes._may_run_code(ADMIN.id) is True


# ── the admin surface ──


def test_both_edges_of_the_grant_are_audited(cm):
    _set(ADMIN, SEAT.id, True)
    _set(ADMIN, SEAT.id, False)

    entries = [e for e in cm.get_audit_log() if e["action"].startswith("code_run_")]
    assert [e["action"] for e in entries] == ["code_run_revoked", "code_run_granted"]
    assert {e["actor_id"] for e in entries} == {ADMIN.id}
    assert {e["target_id"] for e in entries} == {str(SEAT.id)}


def test_the_user_list_reports_who_holds_the_grant(cm):
    _set(ADMIN, SEAT.id, True)

    rows = {r.user_id: r for r in asyncio.run(list_users(user=ADMIN))}

    assert rows[SEAT.id].code_run is True
    assert rows[OTHER.id].code_run is False
    assert rows[ADMIN.id].is_admin is True
    assert rows[ADMIN.id].code_run is False, "admins pass on their role, not a grant"


def test_granting_to_an_admin_is_refused_as_a_no_op(cm):
    with pytest.raises(HTTPException) as excinfo:
        _set(ADMIN, ADMIN.id, True)

    assert excinfo.value.status_code == 409
    assert cm.has_code_run_grant(ADMIN.id) is False


def test_granting_to_an_unknown_user_is_a_404(cm):
    with pytest.raises(HTTPException) as excinfo:
        _set(ADMIN, 9999, True)

    assert excinfo.value.status_code == 404


def test_setting_the_grant_to_what_it_already_is_writes_no_audit_entry(cm):
    _set(ADMIN, SEAT.id, True)
    _set(ADMIN, SEAT.id, True)

    entries = [e for e in cm.get_audit_log() if e["action"].startswith("code_run_")]
    assert len(entries) == 1

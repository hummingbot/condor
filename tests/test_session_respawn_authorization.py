"""SEC-167: respawn's access check names the caller, not the session's owner.

``POST /sessions/{key}/action`` with ``new``/``switch`` used to ask
``check_server_access`` whether the *session's owner* could reach the requested
server, with ``info.user_id or 0`` standing in when the record carried no owner
at all. Two properties are pinned here:

* the subject of the server check is the **caller** — reaching a session whose
  owner has broader access must not license the owner's permissions; and
* an **ownerless** session is refused outright rather than checked as user 0,
  the same fail-closed treatment SEC-150 gave ownerless routine instances.

The fake config manager below deliberately does *not* grant the admin universal
server access, which the real one does (``get_server_permission`` returns OWNER
for any admin). That is what makes caller and owner distinguishable at all: with
the real semantics the two subjects agree for every caller who can reach
``_respawn``, so the bug is latent rather than exploitable — which is exactly
why it needs a test to stay fixed.
"""

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from condor.runtime import SessionInfo, SessionKey
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import sessions as routes

OWNER = WebUser(id=111, username="owner", first_name="O", role="user")
STRANGER = WebUser(id=222, username="stranger", first_name="S", role="user")
ADMIN = WebUser(id=999, username="admin", first_name="A", role="admin")

# Only OWNER is granted it, so a check that passes for ADMIN can only have
# asked about OWNER.
OWNER_ONLY_SERVER = "prod"

KEY = f"web:{OWNER.id}:main"


class FakeConfigManager:
    def is_admin(self, user_id):
        return user_id == ADMIN.id

    def has_server_access(self, user_id, server_name, *a, **k):
        return user_id == OWNER.id and server_name == OWNER_ONLY_SERVER


def _info(user_id: int | None) -> SessionInfo:
    return SessionInfo(
        key=KEY,
        agent_key="claude-code",
        user_id=user_id,
        surface="web",
        slot="main",
        server_name="local",
        alive=True,
    )


@pytest.fixture
def env(monkeypatch):
    """Stub the runtime so only the authorization layer is under test."""
    destroyed: list[SessionKey] = []

    async def _get_info(key):
        return _info(OWNER.id)

    async def _destroy(key):
        destroyed.append(key)
        return True

    monkeypatch.setattr(routes.runtime, "get_info", _get_info)
    monkeypatch.setattr(routes.runtime, "destroy", _destroy)
    monkeypatch.setattr(routes, "get_config_manager", lambda: FakeConfigManager())
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    return destroyed


def _client(user: WebUser) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _switch(user: WebUser, **body):
    return _client(user).post(
        f"/sessions/{KEY}/action",
        json={"action": "switch", **body},
    )


def test_a_stranger_cannot_respawn_someone_elses_session(env):
    """The ownership gate runs before anything is torn down."""
    resp = _switch(STRANGER, server_name=OWNER_ONLY_SERVER)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Not your session"
    assert env == [], "a refused respawn must leave the session running"


def test_a_stranger_cannot_respawn_even_without_naming_a_server(env):
    """``new`` on a foreign session is refused too, server or no server."""
    resp = _client(STRANGER).post(f"/sessions/{KEY}/action", json={"action": "new"})

    assert resp.status_code == 403
    assert env == []


def test_the_owner_passes_the_server_check_on_their_own_server(env, monkeypatch):
    """Sanity: the guard is not simply refusing everyone."""
    spawned = {}

    async def _create_session(spec, user_data=None):
        spawned["spec"] = spec
        return _info(OWNER.id)

    monkeypatch.setattr(routes.runtime, "create_session", _create_session)
    monkeypatch.setattr(routes, "remember_model_choice", lambda *a, **k: None)
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})

    resp = _switch(OWNER, server_name=OWNER_ONLY_SERVER)

    assert resp.status_code == 200
    assert spawned["spec"].server_name == OWNER_ONLY_SERVER
    assert env == [SessionKey.parse(KEY)]


def test_the_server_check_names_the_caller_not_the_session_owner(env):
    """An admin reaching a foreign session is held to their own access.

    ADMIN clears ``_require_ownership`` (admins see every session) but has no
    grant on ``OWNER_ONLY_SERVER``. Before SEC-167 the check asked about
    ``info.user_id`` — the owner, who does — and the switch went through.
    """
    resp = _switch(ADMIN, server_name=OWNER_ONLY_SERVER)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "No access"
    assert env == [], "the check must run before the teardown"


def test_an_ownerless_session_is_refused_not_checked_as_user_zero(env, monkeypatch):
    """``info.user_id or 0`` asked about a placeholder id; now it refuses.

    An ownerless record only reaches ``_respawn`` through an admin, and a
    respawn re-creates the session — spec owner, user_data, remembered model
    and transcript are all keyed by ``user_id``, so there is no honest subject
    to create it for.
    """
    asked: list[int] = []

    def _spy(user_id, server_name):
        asked.append(user_id)
        raise AssertionError("must not reach the server check")

    monkeypatch.setattr(routes, "check_server_access", _spy)

    async def _orphan(key):
        return _info(None)

    monkeypatch.setattr(routes.runtime, "get_info", _orphan)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            routes._respawn(
                SessionKey.parse(KEY),
                _info(None),
                routes.SessionAction(action="switch", server_name=OWNER_ONLY_SERVER),
                ADMIN,
            )
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Session has no owner"
    assert asked == [], "no placeholder id may be handed to the access check"
    assert env == []


def test_an_ownerless_session_is_refused_over_http_too(env, monkeypatch):
    """Same refusal through the route, for the admin who can reach it."""

    async def _orphan(key):
        return _info(None)

    monkeypatch.setattr(routes.runtime, "get_info", _orphan)

    resp = _switch(ADMIN, server_name=OWNER_ONLY_SERVER)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Session has no owner"
    assert env == []

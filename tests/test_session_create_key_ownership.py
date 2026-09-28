"""SEC-637: ``POST /sessions`` holds ``spec.key`` to the caller.

The route forced ``spec.user_id`` to the caller and gated ``spec.server_name``,
but the key went straight to the runtime, which looks it up in the process-wide
registry with no owner check. A differing ``agent_key`` makes the live session
non-reusable, so it is destroyed (busy or not) and respawned under the caller's
``user_id`` — and Telegram keeps prompting ``tg:<victim>``, so the victim's next
message lands on the attacker's tools and conversation.

Pinned here: a live session under a key may only be replaced by its recorded
owner or an admin; a fresh key must name the caller as its owner; and the
refusal lands before the runtime (and so before any destroy) is reached.
"""

import asyncio

import pytest
from fastapi import HTTPException

from condor.runtime import SessionInfo, SessionKey, SessionSpec
from condor.runtime import sessions as session_module
from condor.web.models import WebUser
from condor.web.routes import sessions as routes

VICTIM = WebUser(id=111, username="victim", first_name="V", role="user")
ATTACKER = WebUser(id=222, username="attacker", first_name="A", role="user")
ADMIN = WebUser(id=999, username="admin", first_name="Ad", role="admin")


class _Cm:
    def is_admin(self, user_id):
        return user_id == ADMIN.id


class _Client:
    def __init__(self, log):
        self._log = log

    async def stop(self):
        self._log.append("client.stop")


class _LiveSession:
    """Just enough of AgentSession for the registry lookup and a teardown."""

    def __init__(self, key: str, user_id: int | None, log: list):
        self.key = key
        self.user_id = user_id
        self.agent_key = "claude-code"
        self.client = _Client(log)
        self._log = log

    def info(self) -> SessionInfo:
        parsed = SessionKey.parse(self.key)
        return SessionInfo(
            key=self.key,
            agent_key=self.agent_key,
            user_id=self.user_id,
            surface=parsed.surface,
            slot=parsed.slot,
            server_name="local",
            alive=True,
        )

    async def destroy(self):
        self._log.append("destroy")


@pytest.fixture
def env(monkeypatch):
    """Stub config + runtime.create_session; leave get_info on the real registry."""
    torn: list[str] = []
    created: list[SessionSpec] = []

    monkeypatch.setattr(routes, "get_config_manager", lambda: _Cm())
    monkeypatch.setattr(session_module, "_sessions", {})

    async def _create(spec, **kwargs):
        created.append(spec)
        return SessionInfo(
            key=spec.key,
            agent_key=spec.agent_key,
            user_id=spec.user_id,
            surface=SessionKey.parse(spec.key).surface,
            alive=True,
        )

    monkeypatch.setattr(routes.runtime, "create_session", _create)
    monkeypatch.setattr(
        "condor.preferences.load_user_data_for", lambda user_id: {}, raising=False
    )

    def register(key: str, user_id: int | None):
        session_module._sessions[key] = _LiveSession(key, user_id, torn)

    return register, created, torn


def _post(key: str, user: WebUser, agent_key: str = "claude-code", **kw):
    spec = SessionSpec(key=key, agent_key=agent_key, platform="web", **kw)
    return asyncio.run(routes.create_session(spec, user=user))


# ── A live session under another user's key ──


@pytest.mark.parametrize("agent_key", ["claude-code", "gemini", ""])
def test_stranger_cannot_replace_a_live_telegram_session(env, agent_key):
    """Same, differing (the unconditional destroy) and empty agent keys alike."""
    register, created, torn = env
    key = f"tg:{VICTIM.id}"
    register(key, VICTIM.id)

    with pytest.raises(HTTPException) as exc:
        _post(key, ATTACKER, agent_key=agent_key)

    assert exc.value.status_code == 403
    assert created == []
    assert torn == []
    assert session_module._sessions[key].user_id == VICTIM.id


def test_owner_segment_is_not_enough_for_a_session_recorded_to_someone_else(env):
    """Ownership follows the recorded user_id, like every other route on the key."""
    register, created, torn = env
    key = f"web:{ATTACKER.id}:main"
    register(key, VICTIM.id)

    with pytest.raises(HTTPException) as exc:
        _post(key, ATTACKER)

    assert exc.value.status_code == 403
    assert created == [] and torn == []


def test_owner_can_replace_their_own_live_session(env):
    register, created, _ = env
    key = f"tg:{VICTIM.id}"
    register(key, VICTIM.id)

    _post(key, VICTIM, agent_key="gemini")

    assert [s.key for s in created] == [key]
    assert created[0].user_id == VICTIM.id


def test_admin_can_replace_another_users_live_session(env):
    register, created, _ = env
    key = f"tg:{VICTIM.id}"
    register(key, VICTIM.id)

    _post(key, ADMIN, agent_key="gemini")

    assert len(created) == 1
    # user_id is still forced to the admin when the admin did not pass one.
    assert created[0].user_id == ADMIN.id


# ── A fresh key ──


@pytest.mark.parametrize("key", [f"tg:{VICTIM.id}", f"web:{VICTIM.id}:s1"])
def test_stranger_cannot_mint_a_fresh_key_owned_by_someone_else(env, key):
    _, created, _ = env

    with pytest.raises(HTTPException) as exc:
        _post(key, ATTACKER)

    assert exc.value.status_code == 403
    assert created == []


@pytest.mark.parametrize("key", [f"tg:{ATTACKER.id}", f"web:{ATTACKER.id}:s1"])
def test_caller_can_mint_their_own_fresh_key(env, key):
    _, created, _ = env

    info = _post(key, ATTACKER)

    assert info.key == key
    assert [s.user_id for s in created] == [ATTACKER.id]


def test_admin_can_mint_a_fresh_key_for_another_user(env):
    _, created, _ = env

    _post(f"web:{VICTIM.id}:s1", ADMIN, user_id=VICTIM.id)

    assert [(s.key, s.user_id) for s in created] == [(f"web:{VICTIM.id}:s1", VICTIM.id)]


def test_legacy_key_is_checked_and_forwarded_in_canonical_form(env):
    _, created, _ = env

    with pytest.raises(HTTPException) as exc:
        _post(f"web_{VICTIM.id}_s1", ATTACKER)
    assert exc.value.status_code == 403

    _post(f"web_{ATTACKER.id}_s1", ATTACKER)
    assert [s.key for s in created] == [f"web:{ATTACKER.id}:s1"]


def test_malformed_key_is_a_400_from_the_parser(env):
    _, created, _ = env

    with pytest.raises(HTTPException) as exc:
        _post("not-a-key", ATTACKER)

    assert exc.value.status_code == 400
    assert exc.value.detail.startswith("Malformed session key")
    assert created == []

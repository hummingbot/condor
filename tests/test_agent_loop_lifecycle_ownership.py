"""Loop lifecycle routes act only on the caller's own engines (SEC-251).

``/stop``, ``/shutdown``, ``/pause`` and ``/resume`` looked their target up in
the process-global engine registry and acted on whatever came back: with an
``agent_id`` the ``{slug}/{sslug}`` path was ignored entirely, and without one
every engine of that strategy was hit regardless of who started it. Since
``/shutdown`` winds down live positions on the owner's server credentials, any
approved user (or a prompt-injected chat agent using the MCP ``control_agent``
tool with its own JWT) could force-liquidate someone else's running loop.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents import engine as engine_module
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

OWNER = WebUser(id=111, username="a", first_name="A", role="user")
OTHER = WebUser(id=222, username="b", first_name="B", role="user")
ADMIN = WebUser(id=999, username="root", first_name="Root", role="admin")

VERBS = ["stop", "shutdown", "pause", "resume"]


class FakeEngine:
    """Stands in for a running TickEngine; records what was done to it."""

    def __init__(self, agent_id: str, user_id: int):
        self.agent_id = agent_id
        self.user_id = user_id
        self.is_running = True
        self.calls: list[str] = []

    async def stop(self):
        self.calls.append("stop")

    async def _run_shutdown(self, reason: str):
        self.calls.append("shutdown")

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")


class FakeConfigManager:
    def is_admin(self, user_id):
        return user_id == ADMIN.id


@pytest.fixture
def engine(monkeypatch) -> FakeEngine:
    """A single loop of brigado/scalp, started by OWNER."""
    eng = FakeEngine("agent-1", OWNER.id)
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(
        engine_module, "get_engine", lambda aid: eng if aid == eng.agent_id else None
    )
    monkeypatch.setattr(routes, "_get_engines_for", lambda slug, sslug: [eng])
    return eng


def _client(user: WebUser) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _post(user: WebUser, verb: str, agent_id: str | None = None):
    url = f"/agents/brigado/strategies/scalp/{verb}"
    if agent_id:
        url += f"?agent_id={agent_id}"
    return _client(user).post(url)


@pytest.mark.parametrize("verb", VERBS)
def test_naming_someone_elses_engine_is_refused(engine, verb):
    res = _post(OTHER, verb, engine.agent_id)

    assert res.status_code == 403
    assert engine.calls == [], "another user's loop was left untouched"


@pytest.mark.parametrize("verb", VERBS)
def test_the_broadcast_branch_never_reaches_a_foreign_engine(engine, verb):
    """No agent_id: B's request must find nothing rather than hit A's loop."""
    res = _post(OTHER, verb)

    assert res.status_code == 404
    assert engine.calls == []


@pytest.mark.parametrize("verb", VERBS)
def test_the_owner_still_controls_their_own_loop(engine, verb):
    assert _post(OWNER, verb, engine.agent_id).status_code == 200
    assert _post(OWNER, verb).status_code == 200

    assert engine.calls == [verb, verb]


@pytest.mark.parametrize("verb", VERBS)
def test_an_admin_still_reaches_every_loop(engine, verb):
    assert _post(ADMIN, verb, engine.agent_id).status_code == 200
    assert _post(ADMIN, verb).status_code == 200

    assert engine.calls == [verb, verb]


@pytest.mark.parametrize("verb", VERBS)
def test_an_unowned_restored_loop_is_admin_only(engine, verb):
    """``_owner_of`` can restore a pre-user_id session as user 0 — nobody's."""
    engine.user_id = 0

    assert _post(OWNER, verb, engine.agent_id).status_code == 403
    assert _post(OWNER, verb).status_code == 404
    assert engine.calls == []

    assert _post(ADMIN, verb, engine.agent_id).status_code == 200
    assert engine.calls == [verb]

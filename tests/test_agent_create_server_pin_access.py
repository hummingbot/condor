"""Creating an Agent cannot pin it to a server the caller has no access to (SEC-594).

``POST /agents`` passed ``req.server_name`` straight into ``AgentStore.create``
while the very next route, ``PATCH /agents/{slug}/config``, gated the identical
field. Credentials were never at risk — every resolution site re-checks
existence *and* reach — but ``condor/runtime/binding.py`` reports the stored pin
verbatim as ``SessionBinding.server_name``, so an ungated create produced an
Agent that names a foreign account in the chat header and in ``AgentSummary``
while its tools trade on the caller's own.

The tests assert the rule on both writes of the same field, so the two routes
cannot drift apart again.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents.agent import AgentStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")

MINE = "mine-prod"
THEIRS = "someone-else"


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def get_server(self, server_name):
        return {"name": server_name} if server_name in (MINE, THEIRS) else None

    def has_server_access(self, user_id, server_name, *a, **k):
        return user_id == USER.id and server_name == MINE


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    # The guard lives in condor.web.auth (SEC-147), which binds
    # get_config_manager at import time — patch it there too.
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr("condor.preferences.get_active_agent_key", lambda uid: "")
    return tmp_path


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def test_creating_an_agent_pinned_to_someone_elses_server_is_refused(env):
    res = _client().post("/agents", json={"name": "Borrowed", "server_name": THEIRS})

    assert res.status_code == 403
    assert res.json()["detail"] == "No access"
    # And nothing was written: the gate runs before AgentStore.create.
    assert AgentStore().get("borrowed") is None
    assert not (env / "borrowed").exists()


def test_creating_an_agent_on_my_own_server_still_works(env):
    res = _client().post("/agents", json={"name": "Mine", "server_name": MINE})

    assert res.status_code == 200
    assert AgentStore().get("mine").server_name == MINE


def test_creating_an_agent_with_no_pin_is_untouched(env):
    res = _client().post("/agents", json={"name": "Unpinned"})

    assert res.status_code == 200
    assert AgentStore().get("unpinned") is not None


def test_create_and_patch_config_enforce_the_same_rule_on_the_same_field(env):
    """The pin is one field; both doors to it must answer identically."""
    AgentStore().create(name="Existing", description="d")
    client = _client()

    assert client.post(
        "/agents", json={"name": "New", "server_name": THEIRS}
    ).status_code == (
        client.patch(
            "/agents/existing/config", json={"server_name": THEIRS}
        ).status_code
    )
    assert (
        client.patch(
            "/agents/existing/config", json={"server_name": THEIRS}
        ).status_code
        == 403
    )
    # The foreign pin never reached storage through either door.
    assert AgentStore().get("existing").server_name in (None, "")

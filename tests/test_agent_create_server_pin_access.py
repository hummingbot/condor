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
    # The raw AGENT.md route is a third door to the same field (SEC-693).
    assert (
        client.put(
            "/agents/existing",
            json={"content": f"---\nname: Existing\nserver_name: {THEIRS}\n---\n"},
        ).status_code
        == 403
    )
    # The foreign pin never reached storage through any door.
    assert AgentStore().get("existing").server_name in (None, "")


# ── SEC-693: the raw-markdown and strategy-config writers of the same pin ──


def _agent_md(server_name: str | None) -> str:
    pin = f"server_name: {server_name}\n" if server_name is not None else ""
    return f"---\nname: Existing\ndescription: d\n{pin}---\n\nBody.\n"


def test_put_agent_md_naming_a_foreign_server_is_refused_and_writes_nothing(env):
    agent = AgentStore().create(name="Existing", description="d")
    before = (agent.home / "AGENT.md").read_bytes()

    res = _client().put("/agents/existing", json={"content": _agent_md(THEIRS)})

    assert res.status_code == 403
    assert (agent.home / "AGENT.md").read_bytes() == before
    assert AgentStore().get("existing").server_name in (None, "")


@pytest.mark.parametrize("pin", [MINE, None, ""])
def test_put_agent_md_with_own_or_no_pin_writes(env, pin):
    AgentStore().create(name="Existing", description="d")

    res = _client().put("/agents/existing", json={"content": _agent_md(pin)})

    assert res.status_code == 200
    assert AgentStore().get("existing").server_name == (pin or "")


def test_put_agent_md_resending_the_stored_foreign_pin_is_a_round_trip(env):
    """Re-saving a file someone else legitimately pinned must not 403."""
    AgentStore().create(name="Existing", description="d", server_name=THEIRS)

    res = _client().put("/agents/existing", json={"content": _agent_md(THEIRS)})

    assert res.status_code == 200
    assert AgentStore().get("existing").server_name == THEIRS


def _existing_agent(env):
    AgentStore().create(name="Existing", description="d")
    return env / "existing" / "strategies"


def test_create_strategy_with_a_foreign_server_is_refused_and_creates_nothing(env):
    strategies = _existing_agent(env)

    res = _client().post(
        "/agents/existing/strategies",
        json={"name": "Grid", "config": {"server_name": THEIRS}},
    )

    assert res.status_code == 403
    assert not (strategies / "grid").exists()


@pytest.mark.parametrize("config", [{"server_name": MINE}, {}])
def test_create_strategy_with_own_or_no_pin_works(env, config):
    strategies = _existing_agent(env)

    res = _client().post(
        "/agents/existing/strategies", json={"name": "Grid", "config": config}
    )

    assert res.status_code == 200
    assert (strategies / "grid" / "strategy.md").exists()


def _strategy(env, server_name: str | None = None):
    from condor.agents.strategy import StrategyStore

    _existing_agent(env)
    defaults = {"server_name": server_name} if server_name else {}
    return StrategyStore().create(
        agent_slug="existing", name="Grid", default_config=defaults
    )


def test_update_strategy_config_with_a_foreign_server_is_refused(env):
    strategy = _strategy(env)
    config_yml = strategy.home / "config.yml"
    before = config_yml.read_bytes() if config_yml.exists() else None

    res = _client().put(
        "/agents/existing/strategies/grid/config",
        json={"config": {"server_name": THEIRS}},
    )

    assert res.status_code == 403
    assert (config_yml.read_bytes() if config_yml.exists() else None) == before


@pytest.mark.parametrize("stored,sent", [(THEIRS, THEIRS), (None, MINE)])
def test_update_strategy_config_with_stored_or_own_pin_works(env, stored, sent):
    strategy = _strategy(env, stored)

    res = _client().put(
        "/agents/existing/strategies/grid/config",
        json={"config": {"server_name": sent}},
    )

    assert res.status_code == 200
    assert routes._strategy_server(strategy.home, strategy.default_config) == sent


def _strategy_md(server_name: str) -> str:
    return (
        "---\nname: Grid\ndefault_config:\n"
        f"  server_name: {server_name}\n---\n\nPlaybook.\n"
    )


def test_put_strategy_md_naming_a_foreign_server_is_refused(env):
    strategy = _strategy(env)
    md = strategy.home / "strategy.md"
    before = md.read_bytes()

    res = _client().put(
        "/agents/existing/strategies/grid", json={"content": _strategy_md(THEIRS)}
    )

    assert res.status_code == 403
    assert md.read_bytes() == before


@pytest.mark.parametrize("stored,sent", [(THEIRS, THEIRS), (None, MINE)])
def test_put_strategy_md_with_stored_or_own_pin_writes(env, stored, sent):
    from condor.agents.strategy import StrategyStore

    _strategy(env, stored)

    res = _client().put(
        "/agents/existing/strategies/grid", json={"content": _strategy_md(sent)}
    )

    assert res.status_code == 200
    assert StrategyStore().get("existing", "grid").default_config["server_name"] == sent

"""A config write says what it reached, not just that it succeeded (CORR-732).

GitHub issue #275: "Several times during the race, an operator edited a config,
saw 'updated', and assumed the running agent had picked it up. It hadn't."

``update_strategy_config`` wrote ``config.yml`` and answered
``{"updated": True, "config": {...}}``. That is true of the *file* and silent
about the *process*: a ``TickEngine`` keeps the config dict it was constructed
with, and only ``restart_on_boot`` is ever pushed into a live engine. The
behaviour is deliberate — a trading loop must not have its limits swapped
mid-tick — so the defect was never the behaviour, it was that the answer read
identically whether a session was left drifting or there was none to drift.

The same shape appears one level out, on a connector write: hummingbot-api says
``restart_required`` and Condor returned it inside an opaque ``result`` nothing
read. Both halves are pinned here, plus the one write that *does* reach a live
engine, so the distinction cannot quietly collapse again.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.settings as settings_routes
from condor.runtime.loops import LoopSupervisor
from condor.runtime.registry_file import read_status
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")
SERVER = "alpha"


# ── The strategy config write ──


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def get_server(self, server_name):
        return None

    def has_server_access(self, user_id, server_name, *a, **k):
        return False


@pytest.fixture
def strategy(tmp_path, monkeypatch):
    """A strategy on disk with no server pin, so nothing has to resolve."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    from condor.agents.agent import AgentStore
    from condor.agents.strategy import StrategyStore

    AgentStore().create(name="Brigado", created_by=USER.id)
    return StrategyStore().create(agent_slug="brigado", name="MM", created_by=USER.id)


def _engine(session_dir, *, num: int, restart_on_boot: bool = False, status="running"):
    """A stand-in carrying only what the registry and ``record`` read."""
    from types import SimpleNamespace

    return SimpleNamespace(
        agent_id=f"brigado.mm_{num}",
        agent=SimpleNamespace(slug="brigado"),
        strategy=SimpleNamespace(slug="mm"),
        session_num=num,
        session_dir=session_dir,
        chat_id=1,
        user_id=USER.id,
        status=status,
        is_running=status == "running",
        journal=SimpleNamespace(tick_count=3),
        config={"restart_on_boot": restart_on_boot},
    )


def _with_engines(monkeypatch, *engines):
    """Register engines so the count comes through the real ``for_strategy()``."""
    supervisor = LoopSupervisor()
    for engine in engines:
        supervisor._engines[engine.agent_id] = engine
    monkeypatch.setattr("condor.runtime.loops.get_supervisor", lambda: supervisor)
    return supervisor


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def _put_config(config):
    return _client().put(
        "/agents/brigado/strategies/mm/config", json={"config": config}
    )


def test_the_write_reports_the_live_sessions_it_did_not_reach(
    strategy, tmp_path, monkeypatch
):
    """Two live sessions: the response says so, and says it reached neither."""
    supervisor = _with_engines(
        monkeypatch,
        _engine(tmp_path / "s1", num=1),
        _engine(tmp_path / "s2", num=2),
    )

    res = _put_config({"frequency_sec": 120})

    assert res.status_code == 200
    body = res.json()
    assert body["updated"] is True
    assert body["applies_to_running"] is False
    # The count is exactly what for_strategy() answers, not a second walk.
    assert body["live_engines"] == len(supervisor.for_strategy("brigado", "mm")) == 2
    assert body["message"] == "Saved; restart 2 sessions to apply."
    # And the file really was written — the report is about reach, not refusal.
    assert body["config"]["frequency_sec"] == 120


def test_with_no_live_session_the_response_says_so(strategy, monkeypatch):
    """Nothing is drifting, and the answer is not the same one a drift gives."""
    _with_engines(monkeypatch)

    body = _put_config({"frequency_sec": 90}).json()

    assert body["live_engines"] == 0
    assert body["applies_to_running"] is False
    assert (
        body["message"] == "Saved. No session is running, so the next one starts on it."
    )
    assert "restart" not in body["message"]


def test_one_live_session_is_counted_in_the_singular(strategy, tmp_path, monkeypatch):
    _with_engines(monkeypatch, _engine(tmp_path / "s1", num=1))

    body = _put_config({"frequency_sec": 60}).json()

    assert body["live_engines"] == 1
    assert body["message"] == "Saved; restart 1 session to apply."


def test_a_paused_session_counts_because_it_resumes_on_its_old_config(
    strategy, tmp_path, monkeypatch
):
    """``for_strategy()`` includes a paused engine, and so must the count.

    A paused loop resumes with the dict it was constructed with, so it is
    exactly as drifted as a running one — telling the operator "0 sessions"
    would be the silence this item exists to remove.
    """
    _with_engines(monkeypatch, _engine(tmp_path / "s1", num=1, status="paused"))

    body = _put_config({"frequency_sec": 30}).json()

    assert body["live_engines"] == 1
    assert body["applies_to_running"] is False


# ── The one write that does reach a live engine ──


def test_restart_on_boot_still_reaches_live_engines(strategy, tmp_path, monkeypatch):
    """The exception stays an exception: this flag is pushed, and re-recorded.

    Nothing here changes for CORR-732 — it is asserted beside the config write
    precisely so that "a write never reaches a running session" cannot be
    over-generalized into breaking the one that does.
    """
    session_dir = tmp_path / "s1"
    session_dir.mkdir(parents=True)
    engine = _engine(session_dir, num=1, restart_on_boot=False)
    _with_engines(monkeypatch, engine)

    res = _client().post(
        "/agents/brigado/strategies/mm/restart-on-boot", json={"enabled": True}
    )

    assert res.status_code == 200
    assert res.json() == {"restart_on_boot": True, "applied_to_running": 1}
    assert engine.config["restart_on_boot"] is True
    assert read_status(session_dir)["restart_on_boot"] is True


# ── The connector write, one level out ──


class FakeAccounts:
    def __init__(self, result):
        self.result = result

    async def add_credential(self, account_name, connector_name, credentials):
        return self.result

    async def delete_credential(self, account_name, connector_name):
        return self.result


class FakeSettingsConfigManager:
    def __init__(self, client):
        self._client = client

    def get_server_permission(self, user_id, server_name):
        from config_manager import ServerPermission

        return ServerPermission.OWNER if server_name == SERVER else None

    def has_server_access(self, user_id, server_name, min_permission=None):
        return server_name == SERVER

    def is_admin(self, user_id):
        return False

    async def get_client(self, server_name):
        return self._client


@pytest.fixture
def connector_write(monkeypatch):
    """A credentials client whose upstream answer the test chooses."""
    from types import SimpleNamespace

    client = SimpleNamespace(accounts=FakeAccounts({"ok": True}))
    cm = FakeSettingsConfigManager(client)
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    monkeypatch.setattr(
        settings_routes,
        "get_server_data_service",
        lambda: SimpleNamespace(invalidate=lambda *a, **k: None),
    )
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return SimpleNamespace(http=TestClient(app), accounts=client.accounts)


def _add(connector_write):
    return connector_write.http.post(
        "/settings/credentials",
        params={"server": SERVER},
        json={"connector_name": "binance", "credentials": {"api_key": "k"}},
    )


def test_a_credential_add_surfaces_the_servers_restart_required(connector_write):
    connector_write.accounts.result = {"message": "ok", "restart_required": True}

    body = _add(connector_write).json()

    assert body["added"] is True
    assert body["restart_required"] is True
    # Still passed through verbatim too, so no caller loses what it already read.
    assert body["result"]["restart_required"] is True


def test_a_credential_delete_surfaces_it_as_well(connector_write):
    connector_write.accounts.result = {"restart_required": True}

    body = connector_write.http.delete(
        "/settings/credentials/binance", params={"server": SERVER}
    ).json()

    assert body["deleted"] is True
    assert body["restart_required"] is True


@pytest.mark.parametrize(
    "result",
    [{"ok": True}, {"restart_required": False}, None, "accepted"],
    ids=["absent", "false", "no-body", "not-a-dict"],
)
def test_a_server_that_does_not_say_so_reads_as_no_restart_needed(
    connector_write, result
):
    """An older API server, or one that answers with no body, is not a warning."""
    connector_write.accounts.result = result

    assert _add(connector_write).json()["restart_required"] is False

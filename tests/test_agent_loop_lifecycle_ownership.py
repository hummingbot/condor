"""Loop lifecycle routes act only on the caller's own engines (SEC-251).

``/stop``, ``/shutdown``, ``/pause`` and ``/resume`` looked their target up in
the process-global engine registry and acted on whatever came back: with an
``agent_id`` the ``{slug}/{sslug}`` path was ignored entirely, and without one
every engine of that strategy was hit regardless of who started it. Since
``/shutdown`` winds down live positions on the owner's server credentials, any
approved user (or a prompt-injected chat agent using the MCP ``control_agent``
tool with its own JWT) could force-liquidate someone else's running loop.
"""

from types import SimpleNamespace

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


# ── ARCH-689: one targeting helper for all four verbs ──


@pytest.fixture
def no_engines(monkeypatch):
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(engine_module, "get_engine", lambda aid: None)
    monkeypatch.setattr(routes, "_get_engines_for", lambda slug, sslug: [])


def test_every_verb_gives_the_same_not_found_detail(no_engines):
    details = set()
    for verb in VERBS:
        res = _post(OWNER, verb)
        assert res.status_code == 404
        details.add(res.json()["detail"])

    assert details == {"No running strategy found"}


def test_pausing_a_finished_engine_by_id_uses_the_broadcast_wording(engine):
    engine.is_running = False

    by_id = _post(OWNER, "pause", engine.agent_id)
    broadcast = _post(OWNER, "pause")

    assert by_id.status_code == broadcast.status_code == 404
    assert by_id.json()["detail"] == broadcast.json()["detail"]
    assert engine.calls == []


def test_pausing_a_paused_but_running_engine_by_id_still_succeeds(engine):
    """``TickEngine.is_running`` ignores ``_paused``: a second pause is a no-op 200."""
    assert _post(OWNER, "pause", engine.agent_id).status_code == 200
    assert _post(OWNER, "pause", engine.agent_id).status_code == 200

    assert engine.calls == ["pause", "pause"]


@pytest.mark.parametrize("verb", VERBS)
def test_the_broadcast_reaches_every_instance_of_the_strategy(monkeypatch, verb):
    """The dashboard never names an instance, so every verb must hit them all."""
    first = FakeEngine("agent-1", OWNER.id)
    second = FakeEngine("agent-2", OWNER.id)
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(routes, "_get_engines_for", lambda slug, sslug: [first, second])

    assert _post(OWNER, verb).status_code == 200

    assert first.calls == [verb]
    assert second.calls == [verb]


# ── SEC-638: a live loop's inputs are its owner's to edit ──
#
# A running loop re-reads its learnings, scratch state, skills and mutes every
# tick and trades on its owner's credentials, so a write into any of them by
# another user is an instruction to that owner's run. Definitions stay shared
# (SEC-617): with no live engine, anyone may write as before.


class _FakeSupervisor:
    def record(self, engine, status):
        pass


@pytest.fixture
def run(tmp_path, monkeypatch):
    """brigado/scalp on disk with a live loop started by OWNER."""
    from condor.agents.agent import AgentStore
    from condor.agents.strategy import StrategyStore

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr("condor.preferences.get_active_agent_key", lambda uid: "")
    AgentStore().create(name="Brigado", description="d")
    strategy = StrategyStore().create(agent_slug="brigado", name="Scalp")
    (strategy.home / "learnings.md").write_text("owner's learnings\n")

    eng = FakeEngine("brigado.scalp_1", OWNER.id)
    eng.config = {}
    eng.status = "running"
    engines = [eng]
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(routes, "_get_engines_for", lambda slug, sslug: list(engines))
    monkeypatch.setattr(
        "condor.runtime.loops.get_supervisor", lambda: _FakeSupervisor()
    )
    return SimpleNamespace(home=strategy.home, root=tmp_path, engines=engines)


STRATEGY_WRITES = [
    ("put", "/agents/brigado/strategies/scalp/learnings", {"content": "10x short"}),
    ("post", "/agents/brigado/strategies/scalp/state", {"key": "k", "value": "v"}),
    ("put", "/agents/brigado/strategies/scalp/config", {"config": {"x": 1}}),
    ("put", "/agents/brigado/strategies/scalp", {"content": "---\nname: S\n---\nB"}),
    ("post", "/agents/brigado/strategies/scalp/restart-on-boot", {"enabled": True}),
]

AGENT_WRITES = [
    (
        "post",
        "/agents/brigado/skills",
        {"name": "Dump", "description": "d", "when_to_use": "w", "body": "sell"},
    ),
    ("put", "/agents/brigado/skills/dump", {"body": "sell all"}),
    ("delete", "/agents/brigado/skills/dump", None),
    ("post", "/agents/brigado/skill-proposals/accept", None),
    ("put", "/agents/brigado/mutes", {"kind": "skill", "name": "x", "muted": True}),
    ("put", "/agents/brigado", {"content": "---\nname: Brigado\n---\nGo long"}),
]


def _send(user: WebUser, method: str, url: str, body):
    client = _client(user)
    if body is None:
        return getattr(client, method)(url)
    return client.request(method.upper(), url, json=body)


def _snapshot(run) -> dict:
    from condor.agents.config import load_full_config
    from condor.runtime.state import list_state, namespace_for_session

    return {
        "learnings": (run.home / "learnings.md").read_text(),
        "state": list_state(namespace_for_session("brigado.scalp")),
        "config": load_full_config(run.home),
        "files": sorted(
            (str(p.relative_to(run.root)), p.read_bytes())
            for p in run.root.rglob("*")
            if p.is_file()
        ),
    }


@pytest.mark.parametrize("method,url,body", STRATEGY_WRITES + AGENT_WRITES)
def test_another_user_cannot_write_into_a_live_loop(run, method, url, body):
    before = _snapshot(run)

    res = _send(OTHER, method, url, body)

    assert res.status_code == 403, res.text
    assert "stop it first" in res.json()["detail"]
    assert _snapshot(run) == before


@pytest.mark.parametrize("method,url,body", STRATEGY_WRITES)
def test_a_paused_loop_still_blocks_another_user(run, method, url, body):
    run.engines[0].is_running = False

    assert _send(OTHER, method, url, body).status_code == 403


@pytest.mark.parametrize("user", [OWNER, ADMIN])
@pytest.mark.parametrize("method,url,body", STRATEGY_WRITES)
def test_the_owner_and_an_admin_still_write_into_the_live_loop(
    run, user, method, url, body
):
    assert _send(user, method, url, body).status_code == 200


def test_the_owner_still_curates_the_agent_a_live_loop_reads(run):
    for method, url, body in AGENT_WRITES:
        if url.endswith("/accept"):
            continue  # nothing pending: the store's own 400, past the guard
        res = _send(OWNER, method, url, body)
        assert res.status_code == 200, (url, res.text)


@pytest.mark.parametrize("method,url,body", STRATEGY_WRITES + AGENT_WRITES[:1])
def test_with_no_live_loop_definitions_stay_shared(run, method, url, body):
    run.engines.clear()

    assert _send(OTHER, method, url, body).status_code == 200


def test_the_learnings_write_lands_once_the_foreign_loop_is_gone(run):
    url = "/agents/brigado/strategies/scalp/learnings"
    assert _send(OTHER, "put", url, {"content": "b"}).status_code == 403

    run.engines.clear()

    assert _send(OTHER, "put", url, {"content": "b"}).status_code == 200
    assert (run.home / "learnings.md").read_text() == "b"


def test_no_supervisor_is_no_live_run(run, monkeypatch):
    def _boom(slug, sslug):
        raise RuntimeError("no supervisor")

    monkeypatch.setattr(routes, "_get_engines_for", _boom)
    url = "/agents/brigado/strategies/scalp/learnings"

    assert _send(OTHER, "put", url, {"content": "b"}).status_code == 200


# The MCP journal tool writes learnings.md without going through HTTP.


@pytest.fixture
def mcp_run(tmp_path, monkeypatch):
    from mcp_servers.condor.settings import settings

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    eng = SimpleNamespace(
        user_id=OWNER.id,
        is_experiment=False,
        session_dir=session_dir,
        strategy=SimpleNamespace(home=tmp_path),
    )
    monkeypatch.setattr(engine_module, "get_engine", lambda aid: eng)
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    seat = SimpleNamespace(
        set=lambda uid: monkeypatch.setattr(settings, "user_id", uid)
    )
    seat.home = tmp_path
    seat.session_dir = session_dir
    return seat


@pytest.mark.parametrize(
    "entry_type,kwargs",
    [
        ("learning", {}),
        ("state", {}),
        ("canvas", {"tick": 3, "section": "plan"}),
        # Actions come back as the prompt's recent decisions.
        ("action", {"tick": 3}),
    ],
)
def test_mcp_journal_write_into_a_foreign_live_run_is_refused(
    mcp_run, entry_type, kwargs
):
    from mcp_servers.condor.tools.trading_agent import journal_write

    mcp_run.set(OTHER.id)

    result = journal_write("brigado.scalp_1", entry_type, "close all", **kwargs)

    assert result == {"error": "Not your agent"}
    assert list(mcp_run.home.rglob("*.md")) == []


@pytest.mark.parametrize("user", [OWNER, ADMIN])
def test_mcp_journal_write_by_the_owner_or_an_admin_appends(mcp_run, user):
    from mcp_servers.condor.tools.trading_agent import journal_write

    mcp_run.set(user.id)

    assert journal_write("brigado.scalp_1", "learning", "tight spreads") == {
        "written": True
    }
    assert "tight spreads" in (mcp_run.home / "learnings.md").read_text()


def test_mcp_journal_write_into_an_unowned_restored_run_is_unchanged(mcp_run):
    from mcp_servers.condor.tools.trading_agent import journal_write

    engine_module.get_engine("x").user_id = 0
    mcp_run.set(OTHER.id)

    assert journal_write("brigado.scalp_1", "learning", "n") == {"written": True}

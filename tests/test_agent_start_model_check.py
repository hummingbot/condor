"""A model that cannot run is refused at start, and a single tick that fails says so.

The start route answered ``started: true`` before the engine had touched the
model, so ``control_agent(start)`` with a key naming an unsaved custom endpoint
reported success. The dry run then failed its only tick where nobody could see
it: no dry-run file (experiments keep no journal), no notice (``_notify`` only
ever used a live bot, which neither caller passes) and a final state of
*completed*.
"""

import asyncio
import time

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents import engine as engine_module
from condor.agents.agent import Agent, AgentStore
from condor.agents.sessions_index import list_experiments
from condor.agents.strategy import Strategy, StrategyStore
from condor.runtime.loops import LoopSupervisor
from condor.runtime.registry_file import LoopState
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")
BROKEN = "custom@__broken_test_endpoint__:__broken_test_model__"


# ── The start route ──


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def get_server(self, server_name):
        return None

    def has_server_access(self, *a, **k):
        return False


class FakeEngine:
    spawned: list[dict] = []

    def __init__(self, agent, strategy, config, chat_id, user_id):
        self.agent_id = "brigado.scalp_e1"
        self.session_num = 1
        FakeEngine.spawned.append(config)

    async def start(self):
        return None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    # No saved custom endpoints for anyone, and no env fallback to hide that.
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda uid: {})
    monkeypatch.delenv("CUSTOM_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(engine_module, "TickEngine", FakeEngine)
    FakeEngine.spawned = []
    AgentStore().create(name="Brigado", description="BRL market making")
    StrategyStore().create(agent_slug="brigado", name="Scalp")

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def _start(client, config):
    return client.post(
        "/agents/brigado/strategies/scalp/start",
        json={"config": {"execution_mode": "dry_run", **config}},
    )


def test_an_unsaved_custom_endpoint_is_refused_before_any_engine_exists(client):
    resp = _start(client, {"agent_key": BROKEN})

    assert resp.status_code == 422
    assert "No saved endpoint named '__broken_test_endpoint__'" in resp.json()["detail"]
    assert FakeEngine.spawned == []


@pytest.mark.parametrize(
    "key, reason",
    [
        ("nonsense:model", "unknown model provider 'nonsense'"),
        ("openrouter", "no model id"),
        ("custom@venice", "no model id"),
    ],
)
def test_a_key_no_client_can_run_is_refused(client, key, reason):
    """``resolve_acp`` would have run Claude Code in place of these."""
    resp = _start(client, {"agent_key": key})

    assert resp.status_code == 422
    assert reason in resp.json()["detail"]
    assert FakeEngine.spawned == []


@pytest.mark.parametrize(
    "key", ["", "claude-code", "claude-acp:opus", "codex", "ollama:llama3.1"]
)
def test_runnable_keys_still_start(client, key):
    resp = _start(client, {"agent_key": key} if key else {})

    assert resp.status_code == 200, resp.text
    assert resp.json()["started"] is True


def test_an_explicit_base_url_stands_in_for_the_saved_endpoint(client):
    """The engine lets ``model_base_url`` win over the named endpoint; so must the check."""
    resp = _start(
        client, {"agent_key": BROKEN, "model_base_url": "http://127.0.0.1:9/v1"}
    )

    assert resp.status_code == 200, resp.text


# ── The engine ──


def _async(result):
    async def go(*args, **kwargs):
        return result

    return go


async def _empty_stream():
    return
    yield  # pragma: no cover -- makes this an async generator


class _FakeClient:
    def __init__(self, fail_with: Exception | None = None):
        self.fail_with = fail_with

    async def start(self):
        if self.fail_with:
            raise self.fail_with

    async def stop(self):
        return None


@pytest.fixture
def supervisor(monkeypatch):
    sup = LoopSupervisor()
    finals: list[str] = []
    real_unregister = sup.unregister

    def spy(agent_id, final_state=LoopState.STOPPED):
        finals.append(final_state)
        real_unregister(agent_id, final_state)

    sup.unregister = spy
    sup.finals = finals
    monkeypatch.setattr(engine_module, "_supervisor", lambda: sup)
    return sup


def _engine(tmp_path, monkeypatch, *, mode, chat_id=1, client=None):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    strategy = Strategy(agent_slug="brigado", name="Scalp")
    strategy.home.mkdir(parents=True, exist_ok=True)
    engine = engine_module.TickEngine(
        agent=Agent(slug="brigado", name="Brigado", agent_key=BROKEN),
        strategy=strategy,
        config={"execution_mode": mode, "frequency_sec": 0},
        chat_id=chat_id,
        user_id=42,
    )
    notices: list[str] = []

    async def notify(message):
        notices.append(message)

    monkeypatch.setattr(engine, "_notify", notify)
    monkeypatch.setattr(engine, "_get_client", _async(object()))
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine, "_create_client", _async(client or _FakeClient()))
    monkeypatch.setattr(engine, "_collect_stream", lambda *a, **k: _empty_stream())
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    return engine, notices


def _run(engine):
    async def go():
        await engine.start()
        await engine._task

    asyncio.run(go())


def test_a_dry_run_whose_model_fails_is_a_failed_run(tmp_path, monkeypatch, supervisor):
    failing = _FakeClient(
        RuntimeError("No base URL configured for the custom provider.")
    )
    engine, notices = _engine(tmp_path, monkeypatch, mode="dry_run", client=failing)

    _run(engine)

    # A dry-run file exists and the Runs rail reads it as failed.
    [run] = list_experiments(engine.strategy.home)
    assert run["error"] is True
    assert run["agent_key"] == BROKEN
    # The owner hears it failed — once, and not that it completed.
    assert notices == [
        f"Agent {engine.agent_id}: Dry run failed: "
        "No base URL configured for the custom provider."
    ]
    assert supervisor.finals == [LoopState.ERROR]
    assert engine._last_stop_reason == "error"
    assert supervisor.all() == {}


def test_a_healthy_dry_run_still_completes(tmp_path, monkeypatch, supervisor):
    engine, notices = _engine(tmp_path, monkeypatch, mode="dry_run")

    _run(engine)

    [run] = list_experiments(engine.strategy.home)
    assert run["error"] is False
    assert notices == [f"Agent {engine.agent_id}: Dry run complete."]
    assert supervisor.finals == [LoopState.COMPLETED]


def test_a_loop_tells_its_owner_once_per_distinct_error(
    tmp_path, monkeypatch, supervisor
):
    engine, notices = _engine(tmp_path, monkeypatch, mode="loop")
    errors = iter(["model down", "model down", "model down", "server gone"])

    async def failing_tick():
        engine._last_tick_at = time.time()
        err = next(errors, None)
        if err == "server gone":
            engine._running = False  # last tick: let the loop return
        raise RuntimeError(err)

    monkeypatch.setattr(engine, "_tick", failing_tick)

    _run(engine)

    assert notices == [
        f"Agent {engine.agent_id} tick error: model down",
        f"Agent {engine.agent_id} tick error: server gone",
    ]
    # Every failure still reaches the journal, repeated or not.
    assert engine.journal._path.read_text().count("model down") == 3


def test_a_block_is_announced_once_not_every_tick(tmp_path, monkeypatch, supervisor):
    from condor.agents.risk import RiskState

    engine, notices = _engine(tmp_path, monkeypatch, mode="loop")
    monkeypatch.setattr(
        engine.risk,
        "get_state",
        lambda tracker: RiskState(is_blocked=True, block_reason="max drawdown"),
    )

    for _ in range(3):
        asyncio.run(engine._tick())

    assert notices == [f"Agent {engine.agent_id} blocked: max drawdown"]


def test_notify_reaches_the_owner_of_a_dashboard_launch(tmp_path, monkeypatch):
    """chat_id 0 is a web launch; the notice goes to the owner's own chat."""
    engine = engine_module.TickEngine.__new__(engine_module.TickEngine)
    engine.chat_id, engine.user_id, engine.agent_id = 0, 42, "brigado.scalp_1"
    sent: list[dict] = []

    class Recorder:
        async def send_message(self, **kw):
            sent.append(kw)

    monkeypatch.setattr(
        "condor.agents.delegate.resolve_bot", lambda bot=None: Recorder()
    )

    asyncio.run(engine_module.TickEngine._notify(engine, "hello"))

    assert sent == [{"chat_id": 42, "text": "hello"}]

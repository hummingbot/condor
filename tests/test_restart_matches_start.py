"""CORR-728 — a restart is the same thing a start is.

Issue #274, from the Agent Builder Cup: agents came back from a restart on a
different server and a different model, a bare restart *added* a session so two
engines shared one LLM budget, a restart brought back a server name that no
longer existed, and "restarted" was reported before anything had run.

All four are one property — ``LoopSupervisor._restart`` diverging from the
start route — so they are pinned together here:

* pins (``server_name``, ``agent_key``, ``model_base_url``) come from the dead
  run, everything else from the config as it stands now;
* the start route's server and model checks are made before a restart commits;
* a restart replaces the live engine for its strategy rather than adding one;
* success is reported on an observed first tick, not on ``create_task``.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents import engine as engine_module
from condor.agents.agent import Agent, AgentStore
from condor.agents.config import (
    PIN_FIELDS,
    load_session_pins,
    pin_snapshot,
    save_full_config,
)
from condor.agents.strategy import Strategy, StrategyStore
from condor.runtime.loops import LoopSupervisor
from condor.runtime.registry_file import LoopState, read_status
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")
KNOWN_SERVERS = {"mine-prod"}


# ──────────────────────────────────────────────────────────────────────
# The pin set, and the two places a restart can read it from
# ──────────────────────────────────────────────────────────────────────


def test_the_pin_set_is_defined_once():
    """Re-guessing which fields are pins per call site is how they diverge."""
    assert PIN_FIELDS == ("server_name", "agent_key", "model_base_url")


def test_an_empty_pin_is_not_a_pin():
    """ "" means *inherit*, so recording it would shadow a default set since."""
    assert pin_snapshot(
        {"server_name": "mine-prod", "agent_key": "", "model_base_url": None}
    ) == {"server_name": "mine-prod"}
    assert pin_snapshot({"frequency_sec": 60}) == {}


def test_the_supervisor_snapshots_the_pins_next_to_the_journal(tmp_path):
    """``record`` is the freshest copy on disk: rewritten every tick."""
    supervisor = LoopSupervisor()
    engine = SimpleNamespace(
        agent_id="brigado.scalp_1",
        agent=SimpleNamespace(slug="brigado"),
        strategy=SimpleNamespace(slug="scalp"),
        session_num=1,
        session_dir=tmp_path,
        chat_id=555,
        user_id=USER.id,
        journal=SimpleNamespace(tick_count=3),
        config={
            "server_name": "mine-prod",
            "agent_key": "claude-acp:opus",
            "model_base_url": "",
            "frequency_sec": 60,
        },
    )

    supervisor.record(engine, LoopState.RUNNING)

    assert read_status(tmp_path)["pins"] == {
        "server_name": "mine-prod",
        "agent_key": "claude-acp:opus",
    }


def test_the_session_config_is_read_for_a_status_that_predates_the_snapshot(tmp_path):
    """The per-session copy is where a start request's overrides land."""
    save_full_config(
        tmp_path, {"server_name": "mine-prod", "agent_key": "codex", "max_ticks": 7}
    )

    assert load_session_pins(tmp_path) == {
        "server_name": "mine-prod",
        "agent_key": "codex",
    }
    assert load_session_pins(tmp_path / "nowhere") == {}


# ──────────────────────────────────────────────────────────────────────
# _restart: pins from the dead run, config from the file
# ──────────────────────────────────────────────────────────────────────


class FakeConfigManager:
    def get_server(self, server_name):
        return {"name": server_name} if server_name in KNOWN_SERVERS else None

    async def get_client_for_chat(self, chat_id, user_id=None):
        return None  # no orphan-executor scan in these tests


class RecordingEngine:
    """Stands in for TickEngine: records the config it was built with.

    Registers and unregisters with ``supervisor`` the way the real engine's
    ``start``/``_finish`` do, because what several of these tests measure is
    how many engines one strategy ends up with.
    """

    built: list[dict] = []
    pinned: list[bool] = []
    first_tick_problem = ""
    supervisor: "LoopSupervisor | None" = None

    def __init__(
        self,
        agent,
        strategy,
        config,
        chat_id,
        user_id,
        session_num=2,
        server_pinned=False,
    ):
        self.agent = agent
        self.strategy = strategy
        self.config = config
        self.server_pinned = server_pinned
        self.agent_id = f"{agent.slug}.{strategy.slug}_{session_num}"
        self.session_num = session_num
        self.session_dir = None  # nothing for ``record`` to write
        self.started = False
        self.stopped = False
        RecordingEngine.built.append(config)
        RecordingEngine.pinned.append(server_pinned)

    async def start(self):
        self.started = True
        if RecordingEngine.supervisor is not None:
            RecordingEngine.supervisor.register(self)

    async def stop(self):
        self.stopped = True
        if RecordingEngine.supervisor is not None:
            RecordingEngine.supervisor.unregister(self.agent_id)
        return True

    async def wait_first_tick(self, timeout):
        return RecordingEngine.first_tick_problem


@pytest.fixture
def strategy_on_disk(tmp_path, monkeypatch):
    """A real Agent + Strategy under a throwaway agents root."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(engine_module, "TickEngine", RecordingEngine)
    RecordingEngine.built = []
    RecordingEngine.pinned = []
    RecordingEngine.first_tick_problem = ""
    RecordingEngine.supervisor = None
    AgentStore().create(name="Brigado", description="BRL market making")
    StrategyStore().create(agent_slug="brigado", name="Scalp")
    return StrategyStore().get("brigado", "scalp")


@pytest.fixture
def supervisor(strategy_on_disk):
    """A supervisor the stand-in engine registers with, as the real one does."""
    sup = LoopSupervisor()
    RecordingEngine.supervisor = sup
    yield sup
    RecordingEngine.supervisor = None


def _status(**extra):
    status = {
        "state": LoopState.RUNNING,
        "boot_id": "00000000-dead-beef-0000-000000000000",
        "agent_id": "brigado.scalp_1",
        "agent_slug": "brigado",
        "strategy_slug": "scalp",
        "session_num": 1,
        "chat_id": 555,
        "user_id": USER.id,
        "tick": 14,
        "restart_on_boot": True,
    }
    status.update(extra)
    return status


def test_a_restart_comes_back_on_the_pinned_server_and_model(
    strategy_on_disk, supervisor
):
    """The start request's overrides live nowhere ``load_full_config`` reads."""
    save_full_config(strategy_on_disk.home, {"server_name": "strategy-default"})

    assert asyncio.run(
        supervisor._restart(
            _status(pins={"server_name": "mine-prod", "agent_key": "claude-acp:opus"})
        )
    )

    config = RecordingEngine.built[-1]
    assert config["server_name"] == "mine-prod"
    assert config["agent_key"] == "claude-acp:opus"


def test_a_config_edited_while_the_session_was_dead_is_still_picked_up(
    strategy_on_disk, supervisor
):
    """The revalidation intent at ``loops.py`` survives the pin overlay.

    The user may have edited the config *precisely because* the last run
    misbehaved, so a restart re-reads it rather than replaying the dead
    session's copy. Only the pins are carried across.
    """
    save_full_config(
        strategy_on_disk.home,
        {"server_name": "strategy-default", "frequency_sec": 5, "max_ticks": 9},
    )

    asyncio.run(supervisor._restart(_status(pins={"server_name": "mine-prod"})))

    config = RecordingEngine.built[-1]
    assert (config["frequency_sec"], config["max_ticks"]) == (5, 9)
    assert config["restart_on_boot"] is True


def test_the_dead_sessions_own_config_is_the_fallback(
    strategy_on_disk, supervisor, tmp_path
):
    """A session recorded before the snapshot existed still keeps its pins."""
    save_full_config(strategy_on_disk.home, {"server_name": "strategy-default"})
    session_dir = tmp_path / "session_1"
    session_dir.mkdir()
    save_full_config(session_dir, {"server_name": "mine-prod", "agent_key": "codex"})

    asyncio.run(supervisor._restart(_status(), session_dir))

    config = RecordingEngine.built[-1]
    assert config["server_name"] == "mine-prod"
    assert config["agent_key"] == "codex"


# ──────────────────────────────────────────────────────────────────────
# _restart makes the checks _start makes
# ──────────────────────────────────────────────────────────────────────


def test_a_pinned_server_that_no_longer_exists_refuses_the_restart(
    strategy_on_disk, supervisor
):
    """What operators saw: a restart bringing back a deleted server name."""
    with pytest.raises(RuntimeError, match="server 'gone-prod' no longer exists"):
        asyncio.run(supervisor._restart(_status(pins={"server_name": "gone-prod"})))

    assert RecordingEngine.built == []


def test_a_model_that_cannot_run_refuses_the_restart(strategy_on_disk, supervisor):
    """The same 422 the start route gives, before a session is allocated."""
    with pytest.raises(RuntimeError, match="unknown model provider 'nonsense'"):
        asyncio.run(
            supervisor._restart(
                _status(
                    pins={"server_name": "mine-prod", "agent_key": "nonsense:model"}
                )
            )
        )

    assert RecordingEngine.built == []


def test_an_inherited_server_name_that_resolves_to_nothing_still_restarts(
    strategy_on_disk, supervisor
):
    """``_start``'s own rule: only a name this run *chose* must resolve.

    An inherited one (a strategy default) falls through to the owner's
    accessible servers, which is scoped already — refusing it here would strand
    every install whose strategies name a server that has since been renamed.
    """
    save_full_config(strategy_on_disk.home, {"server_name": "local"})

    assert asyncio.run(supervisor._restart(_status()))
    assert RecordingEngine.built[-1]["server_name"] == "local"
    assert RecordingEngine.pinned[-1] is False, "inherited, so the loop stays lenient"


def test_a_carried_pin_pins_the_restarted_loop_too(strategy_on_disk, supervisor):
    """CORR-729: the restart's own distinction has to outlive the restart.

    ``_check_restartable`` holds a carried pin to existence once, up front; the
    loop then runs for days, and without this flag ``_resolve_server`` would go
    back to dropping that name for whatever else the chain reached.
    """
    assert asyncio.run(supervisor._restart(_status(pins={"server_name": "mine-prod"})))

    assert RecordingEngine.built[-1]["server_name"] == "mine-prod"
    assert RecordingEngine.pinned[-1] is True


# ──────────────────────────────────────────────────────────────────────
# A restart replaces its predecessor
# ──────────────────────────────────────────────────────────────────────


def _settled_session(root: Path, sslug: str, session_num: int, **status_extra) -> Path:
    session_dir = (
        root / "brigado" / "loops" / sslug / "sessions" / f"session_{session_num}"
    )
    session_dir.mkdir(parents=True)
    (session_dir / "journal.md").write_text("# Journal\n\n## Decisions\n\n")
    (session_dir / "status.json").write_text(
        json.dumps(
            _status(strategy_slug=sslug, session_num=session_num, **status_extra)
        )
    )
    return session_dir


def test_a_restart_replaces_the_live_engine_instead_of_adding_one(
    strategy_on_disk, supervisor, tmp_path
):
    """Two engines for one strategy shared one LLM budget — the race's contention."""
    live = RecordingEngine(
        Agent(slug="brigado", name="Brigado"),
        Strategy(agent_slug="brigado", name="Scalp"),
        {},
        0,
        USER.id,
        session_num=1,
    )
    asyncio.run(live.start())
    _settled_session(tmp_path, "scalp", 1)
    RecordingEngine.built = []

    report = asyncio.run(supervisor.reconcile_boot(tmp_path))

    assert live.stopped is True
    assert len(RecordingEngine.built) == 1
    assert len(supervisor.for_strategy("brigado", "scalp")) == 1
    assert [run.strategy_slug for run in report.restarted] == ["scalp"]


def test_two_settled_sessions_of_one_strategy_are_restarted_once(
    strategy_on_disk, supervisor, tmp_path
):
    """``reconcile_boot`` starts one session per strategy, not one per leftover."""
    _settled_session(tmp_path, "scalp", 1)
    _settled_session(tmp_path, "scalp", 2)

    asyncio.run(supervisor.reconcile_boot(tmp_path))

    assert len(RecordingEngine.built) == 1
    assert len(supervisor.for_strategy("brigado", "scalp")) == 1


def test_a_restart_whose_first_tick_failed_is_reported_and_leaves_nothing_running(
    strategy_on_disk, tmp_path
):
    """ "Restarted" used to mean "a task was created"."""
    supervisor = LoopSupervisor()
    RecordingEngine.first_tick_problem = "No API client available"
    _settled_session(tmp_path, "scalp", 1)

    report = asyncio.run(supervisor.reconcile_boot(tmp_path))

    assert report.restarted == []
    assert len(report.errors) == 1
    assert "No API client available" in report.errors[0]
    assert supervisor.for_strategy("brigado", "scalp") == []


# ──────────────────────────────────────────────────────────────────────
# The first tick itself
# ──────────────────────────────────────────────────────────────────────


def _real_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    strategy = Strategy(agent_slug="acme", name="Scalp")
    strategy.home.mkdir(parents=True, exist_ok=True)
    supervisor = LoopSupervisor()
    monkeypatch.setattr(engine_module, "_supervisor", lambda: supervisor)
    engine = engine_module.TickEngine(
        agent=Agent(slug="acme", name="Acme"),
        strategy=strategy,
        config={
            "execution_mode": "loop",
            "frequency_sec": 30,
            "canvas_enabled": False,
        },
        chat_id=1,
        user_id=USER.id,
    )

    async def quiet(*a, **k):
        return None

    monkeypatch.setattr(engine, "_notify", quiet)
    return engine


def test_a_clean_first_tick_answers_nothing_is_wrong(tmp_path, monkeypatch):
    engine = _real_engine(tmp_path, monkeypatch)
    ticks: list[int] = []

    async def tick():
        ticks.append(1)

    monkeypatch.setattr(engine, "_tick", tick)

    async def go():
        await engine.start()
        problem = await engine.wait_first_tick(5)
        await engine.stop()
        return problem

    assert asyncio.run(go()) == ""
    assert ticks == [1]


def test_a_first_tick_that_raised_is_what_the_caller_hears(tmp_path, monkeypatch):
    engine = _real_engine(tmp_path, monkeypatch)

    async def boom():
        raise RuntimeError("model down")

    monkeypatch.setattr(engine, "_tick", boom)

    async def go():
        await engine.start()
        problem = await engine.wait_first_tick(5)
        await engine.stop()
        return problem

    assert asyncio.run(go()) == "model down"


def test_a_tick_still_thinking_at_the_bound_counts_as_came_up(tmp_path, monkeypatch):
    """A tick can honestly take minutes; the bound must not hold a request."""
    engine = _real_engine(tmp_path, monkeypatch)

    async def slow():
        await asyncio.sleep(30)

    monkeypatch.setattr(engine, "_tick", slow)

    async def go():
        await engine.start()
        problem = await engine.wait_first_tick(0.05)
        await engine.stop()
        return problem

    assert asyncio.run(go()) == ""


def test_a_run_that_ended_before_ticking_releases_its_waiter(tmp_path, monkeypatch):
    """Nobody waits on a loop that is already over."""
    engine = _real_engine(tmp_path, monkeypatch)

    async def never_ticks(freq, mode):
        return None

    monkeypatch.setattr(engine, "_run_ticks", never_ticks)

    async def go():
        await engine.start()
        return await engine.wait_first_tick(5)

    assert asyncio.run(go()) == "the run ended before its first tick"


# ──────────────────────────────────────────────────────────────────────
# The start route answers on the same evidence
# ──────────────────────────────────────────────────────────────────────


class RouteEngine:
    """The start route's TickEngine: answers for its first tick."""

    problem = ""
    last: "RouteEngine | None" = None

    def __init__(self, agent, strategy, config, chat_id, user_id, server_pinned=False):
        self.agent_id = "brigado.scalp_3"
        self.session_num = 3
        self.stopped = False
        self.server_pinned = server_pinned
        RouteEngine.last = self

    async def start(self):
        return None

    async def stop(self):
        self.stopped = True
        return True

    async def wait_first_tick(self, timeout):
        return RouteEngine.problem


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(engine_module, "TickEngine", RouteEngine)
    RouteEngine.problem = ""
    RouteEngine.last = None
    AgentStore().create(name="Brigado", description="BRL market making")
    StrategyStore().create(agent_slug="brigado", name="Scalp")

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def _post_start(client):
    return client.post(
        "/agents/brigado/strategies/scalp/start",
        json={"config": {"execution_mode": "loop"}},
    )


def test_the_start_route_answers_started_on_an_observed_tick(client):
    resp = _post_start(client)

    assert resp.status_code == 200, resp.text
    assert resp.json()["started"] is True


def test_the_start_route_refuses_a_run_whose_first_tick_failed(client):
    RouteEngine.problem = "model down"

    resp = _post_start(client)

    assert resp.status_code == 502
    assert "model down" in resp.json()["detail"]
    # And nothing is left ticking behind the failure it just reported.
    assert RouteEngine.last.stopped is True

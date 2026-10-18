"""A running session has to say what it is actually running (FEAT-136).

GitHub #275: "it took log archaeology to see that a process was still running a
start-time config." [[CORR-732]] made the *write* say it had not reached the
process; these pin the other half — the *running session* saying what it is
using:

- the effective-vs-file diff over the pin/limit set, with both values;
- ``process_started_at`` and ``boot_id`` on ``/meta/env``, so "has this been
  restarted since I edited that" is answerable;
- the model the ACP adapter reports it is on, which is #221's third ask. A pin
  that did not apply used to leave the bridge on its own default with nothing
  but a log line to say so; since [[TASK-002]] that session does not start at
  all, so this reads back only models the bridge confirmed.
"""

from types import SimpleNamespace

import pytest

from condor.agents.config import DRIFT_FIELDS, PIN_FIELDS, config_drift
from condor.web.routes import agents as agents_routes

# ── The diff ──


def test_a_changed_pin_is_reported_with_both_values():
    drift = config_drift(
        {"server_name": "brigado", "agent_key": "claude-code"},
        {"server_name": "moneymaker", "agent_key": "claude-code"},
    )

    # Both values, not a boolean: what the operator needs is what a restart
    # would change.
    assert drift == {"server_name": {"running": "brigado", "file": "moneymaker"}}


def test_an_unchanged_config_reports_no_drift():
    same = {"server_name": "brigado", "agent_key": "claude-code", "risk_limits": {}}

    assert config_drift(same, dict(same)) == {}


def test_a_difference_outside_the_pin_limit_set_is_not_drift():
    """A cosmetic edit must not badge a session as running stale config."""
    drift = config_drift(
        {"server_name": "brigado", "trading_context": "quote the book"},
        {"server_name": "brigado", "trading_context": "quote the book, carefully"},
    )

    assert drift == {}
    assert "trading_context" not in DRIFT_FIELDS


def test_a_changed_risk_limit_is_reported_per_key():
    drift = config_drift(
        {"risk_limits": {"max_leverage": 5.0, "max_open_executors": 5}},
        {"risk_limits": {"max_leverage": 20.0, "max_open_executors": 5}},
    )

    # One changed limit reads as one changed limit, not as two blobs to diff
    # by eye.
    assert drift == {"risk_limits.max_leverage": {"running": 5.0, "file": 20.0}}


def test_a_pydantic_risk_limits_model_compares_against_the_file_dict():
    from condor.agents.config import RiskLimitsConfig

    limits = RiskLimitsConfig(max_leverage=3.0)
    drift = config_drift(
        {"risk_limits": limits},
        {"risk_limits": {**limits.model_dump(), "max_leverage": 9.0}},
    )

    assert drift == {"risk_limits.max_leverage": {"running": 3.0, "file": 9.0}}


def test_a_field_the_running_session_does_not_report_is_not_drift():
    """A caller that did not read a field is not a session that disagrees."""
    assert config_drift({}, {"server_name": "brigado"}) == {}


def test_the_drift_set_is_the_pin_set_plus_the_limits():
    """One definition, so the drift set and the restart overlay cannot diverge."""
    assert DRIFT_FIELDS == (*PIN_FIELDS, "risk_limits")


# ── The diff, on the wire ──


def _engine(**info):
    base = {
        "agent_id": "brigado.brl_mm_4",
        "session_num": 4,
        "status": "running",
        "tick_count": 3,
        "daily_pnl": 0.0,
        "server_name": "brigado",
        "agent_key": "claude-acp:opus",
        "risk_limits": {"max_leverage": 5.0},
        "model_base_url": "",
    }
    base.update(info)
    return SimpleNamespace(
        get_info=lambda: base,
        journal=None,
        session_dir=None,
        agent_id=base["agent_id"],
    )


def test_a_live_instance_reports_the_drift_against_the_file():
    inst = agents_routes._instance_from_engine(
        _engine(),
        {},
        {
            "server_name": "moneymaker",
            "agent_key": "claude-acp:opus",
            "risk_limits": {"max_leverage": 5.0},
            "model_base_url": "",
        },
    )

    assert inst.config_drift == {
        "server_name": {"running": "brigado", "file": "moneymaker"}
    }


def test_a_live_instance_whose_file_is_unchanged_reports_no_drift():
    file_config = {
        "server_name": "brigado",
        "agent_key": "claude-acp:opus",
        "risk_limits": {"max_leverage": 5.0},
        "model_base_url": "",
        # A key outside the set, changed: still not drift.
        "trading_context": "something else entirely",
    }
    inst = agents_routes._instance_from_engine(_engine(), {}, file_config)

    assert inst.config_drift == {}


def test_a_caller_that_did_not_load_the_file_reports_no_drift():
    """The status-only callers must not pay a config read to get an empty dict."""
    inst = agents_routes._instance_from_engine(_engine(), {})

    assert inst.config_drift == {}


# ── The effective model ──


def test_a_live_instance_reports_the_model_the_adapter_answered():
    inst = agents_routes._instance_from_engine(
        _engine(agent_key="claude-acp:opus", active_model_id="claude-opus-4-8"), {}
    )

    # The pin is what was asked for; this is the answer.
    assert inst.agent_key == "claude-acp:opus"
    assert inst.active_model_id == "claude-opus-4-8"


def test_a_pin_that_did_not_apply_is_visible_as_the_adapter_default():
    """The pin audit #221 asks for: requested ≠ reported means it did not take."""
    inst = agents_routes._instance_from_engine(
        _engine(agent_key="claude-acp:fable", active_model_id="sonnet"), {}
    )

    assert inst.active_model_id == "sonnet"
    assert "fable" not in inst.active_model_id


def test_an_engine_with_no_adapter_answer_reports_empty_not_the_pin():
    """Empty is a real state — never the requested pin echoed back as the answer."""
    inst = agents_routes._instance_from_engine(_engine(), {})

    assert inst.active_model_id == ""


class _QuietClient:
    """A client that answers nothing, with whatever the adapter reported."""

    def __init__(self, active_model_id=None):
        if active_model_id is not None:
            self.active_model_id = active_model_id

    async def start(self):
        return None

    async def stop(self):
        return None

    async def prompt_stream(self, prompt):
        return
        yield  # pragma: no cover -- makes this an async generator


def _ticked_engine(tmp_path, monkeypatch, client, **config):
    """One ``_run_model`` over ``client``, and the engine it left behind."""
    import asyncio

    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.risk import RiskState
    from condor.agents.strategy import Strategy

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    strategy = Strategy(agent_slug="brigado", name="Grid")
    strategy.home.mkdir(parents=True, exist_ok=True)
    engine = TickEngine(
        agent=Agent(slug="brigado", name="Brigado"),
        strategy=strategy,
        config={"execution_mode": "loop", **config},
        chat_id=1,
        user_id=1,
    )

    async def _create(*a, **kw):
        return client

    monkeypatch.setattr(engine, "_create_client", _create)
    asyncio.run(engine._run_model("prompt", RiskState(), client=object()))
    return engine


def test_a_tick_records_the_adapter_answer_off_the_client_it_started(
    tmp_path, monkeypatch
):
    """``get_info`` reads the engine's record, not the client it outlives."""
    engine = _ticked_engine(
        tmp_path,
        monkeypatch,
        _QuietClient("claude-opus-4-8"),
        agent_key="claude-acp:opus",
    )

    # The per-tick client is already stopped and dropped by here.
    assert engine._active_client is None
    assert engine._active_model_id == "claude-opus-4-8"
    assert engine.get_info()["active_model_id"] == "claude-opus-4-8"


def test_a_backend_with_no_adapter_to_ask_records_nothing(tmp_path, monkeypatch):
    """A pydantic-ai client reports no model id, and must not read as a lie."""
    engine = _ticked_engine(
        tmp_path, monkeypatch, _QuietClient(), agent_key="ollama:llama3.1"
    )

    assert engine._active_model_id == ""
    assert engine.get_info()["active_model_id"] == ""


def test_select_model_refuses_a_pin_the_bridge_does_not_offer():
    """The audit never has to report a pin that missed: such a session never runs.

    This test used to assert the opposite — that an unmatched pin left the
    adapter on its own ``currentValue`` and the audit was the only way to find
    out. [[TASK-002]] removed that session: the pin now fails before the first
    turn, so every model this audit reports is one the bridge confirmed.
    """
    import asyncio

    from condor.acp.client import ACPClient, ModelPinError

    client = ACPClient.__new__(ACPClient)
    client.model = "fable"
    client.active_model_id = None
    client._session_id = "s1"
    # What ``stop()`` reads on the failure path: a refused pin kills the bridge.
    client._peer = SimpleNamespace(cancel_all=lambda: None)
    client._read_task = client._stderr_task = None
    client._process = None
    session = {
        "configOptions": [
            {
                "id": "model",
                "category": "model",
                "currentValue": "sonnet",
                "options": [{"value": "sonnet", "name": "Sonnet"}],
            }
        ]
    }

    with pytest.raises(ModelPinError) as err:
        asyncio.run(client._select_model(session))

    assert "'fable'" in str(err.value)
    assert "sonnet" in str(err.value)  # what it does offer


# ── Process identity ──


@pytest.mark.asyncio
async def test_meta_env_reports_the_process_start_time_and_boot_id():
    import time

    from condor.runtime.registry_file import BOOT_ID
    from condor.web.routes.meta import get_env

    env = await get_env(user=SimpleNamespace(id=1))

    assert env["boot_id"] == BOOT_ID
    started = env["process_started_at"]
    # A wall clock, not the monotonic anchor it is taken beside: it has to be
    # readable as a date, and it has to be in the past.
    assert isinstance(started, float)
    assert 0 < started <= time.time()
    # The fields it already had are untouched.
    assert set(env) >= {"version", "branch", "python", "os", "arch", "in_docker"}


def test_the_process_start_time_has_one_definition():
    from condor.telemetry import context

    assert context.process_started_at() == context.process_started_at()
    assert context.process_started_at() == context._started_at_epoch


# ── The diff, through the route that serves it ──


@pytest.fixture()
def strategy(tmp_path, monkeypatch):
    """A strategy with no server pin of its own and no sessions yet."""
    from collections import OrderedDict

    home = tmp_path / "ag" / "strategies" / "st"
    home.mkdir(parents=True)
    strat = SimpleNamespace(
        home=home,
        slug="st",
        agent_slug="ag",
        name="st",
        description="",
        default_config={},
        default_trading_context="",
        created_by=1,
    )
    monkeypatch.setattr(agents_routes, "_get_strategy", lambda slug, sslug: strat)
    monkeypatch.setattr(agents_routes, "_get_engines_for", lambda slug, sslug: [])
    monkeypatch.setattr(agents_routes, "_PERF_CACHE", {})
    monkeypatch.setattr(agents_routes, "_CLOSED_PERF_CACHE", OrderedDict())
    return strat


def _detail(strategy):
    import asyncio

    return asyncio.run(
        agents_routes.get_strategy(
            "ag", "st", user=SimpleNamespace(id=1, username="u1")
        )
    )


def test_the_strategy_route_diffs_the_live_engine_against_the_file(
    strategy, monkeypatch
):
    """The file changed under a running session; the response says so.

    The engine reports the config as it loaded it, with one field as it was
    *before* the edit — which is exactly what an engine holds: it copies the
    config at start and nothing swaps it mid-tick (CORR-732).
    """
    from condor.agents.config import load_full_config

    (strategy.home / "config.yml").write_text("server_name: moneymaker\n")
    at_start = load_full_config(strategy.home, {})
    engine = _engine(
        agent_id="ag.st_4",
        session_num=4,
        **{**at_start, "server_name": "brigado"},
    )
    monkeypatch.setattr(agents_routes, "_get_engines_for", lambda a, s: [engine])

    detail = _detail(strategy)

    assert detail.instances[0].config_drift == {
        "server_name": {"running": "brigado", "file": "moneymaker"}
    }
    # The file is what the response already reports as the config, so the two
    # halves of the answer come from one read.
    assert detail.config["server_name"] == "moneymaker"


def test_the_strategy_route_reports_no_drift_for_an_untouched_file(
    strategy, monkeypatch
):
    (strategy.home / "config.yml").write_text(
        "server_name: brigado\n"
        "agent_key: 'claude-acp:opus'\n"
        "model_base_url: ''\n"
        "risk_limits:\n"
        "  max_leverage: 5.0\n"
        # Outside the pin/limit set and deliberately different.
        "trading_context: 'rewritten since the session started'\n"
    )
    from condor.agents.config import load_full_config

    engine = _engine(
        agent_id="ag.st_4", session_num=4, **load_full_config(strategy.home, {})
    )
    monkeypatch.setattr(agents_routes, "_get_engines_for", lambda a, s: [engine])

    detail = _detail(strategy)

    assert detail.instances[0].config_drift == {}


def test_every_pin_is_diffable_because_the_engine_reports_all_three():
    """A pin a status does not carry is a pin a diff can never badge.

    Calls the real ``TickEngine.get_info`` over a stub self, so this is the
    method the route reads and not a fixture agreeing with itself.
    """
    from condor.agents.engine import TickEngine

    config = {
        "server_name": "brigado",
        "agent_key": "claude-acp:opus",
        "model_base_url": "http://127.0.0.1:1234/v1",
        "risk_limits": {"max_leverage": 5.0},
    }
    stub = SimpleNamespace(
        _last_skill_data={},
        config=config,
        journal=None,
        agent_id="ag.st_4",
        strategy=SimpleNamespace(name="st", slug="st"),
        session_num=4,
        status="running",
        _agent_key=lambda: config["agent_key"],
        _active_model_id="claude-opus-4-8",
        _last_tick_at=0.0,
        _last_reading_at=0.0,
        _last_successful_action_at=0.0,
        _last_tick_duration=0.0,
        _last_error="",
        # The throttle accounting the status also carries ([[FEAT-138]]); this
        # stub answers for every read ``get_info`` makes, by design.
        _throttled_venues=lambda: [],
        session_dir=None,
        is_experiment=False,
    )

    info = TickEngine.get_info(stub)

    assert set(PIN_FIELDS) <= set(info)
    assert config_drift(info, {**config, "model_base_url": "http://other/v1"}) == {
        "model_base_url": {
            "running": "http://127.0.0.1:1234/v1",
            "file": "http://other/v1",
        }
    }
    assert info["active_model_id"] == "claude-opus-4-8"

"""Run identity & storage (§7.1): opaque ULID run ids, run_started metadata,
engine lifecycle → run_ended, simplified agent-level learnings, consult runs,
run_once mapping, and risk-baseline seeding."""

import asyncio

import pytest

from condor.agents import agent as agent_module
from condor.agents.agent import Agent
from condor.agents.learnings import append_learning, read_learnings
from condor.agents.runstore import RunStore, is_run_id, set_run_store


def _patch_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    set_run_store(RunStore(root=tmp_path))


def _run_store():
    from condor.agents.runstore import get_run_store

    return get_run_store()


# ── engine: run identity + run_once mapping ──


def _make_engine(tmp_path, monkeypatch, config):
    from condor.agents.engine import TickEngine

    _patch_roots(monkeypatch, tmp_path)
    agent = Agent(slug="acme", name="Acme", agent_key="claude-code")
    agent.agent_dir.mkdir(parents=True, exist_ok=True)
    return TickEngine(agent=agent, config=config)


def test_run_once_becomes_max_ticks_1_session(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch, {"execution_mode": "run_once"})
    # run_once is an ordinary tick run capped at one tick — on the record.
    assert engine.is_experiment is False
    assert engine.config["execution_mode"] == "loop"
    assert engine.config["max_ticks"] == 1
    # The run id is an opaque ULID — no {slug}_{N} grammar survives.
    assert is_run_id(engine.agent_id)
    assert engine.session_num == 1

    events = _run_store().read_events("acme", engine.agent_id)
    started = events[0]
    assert started["type"] == "run_started"
    assert started["payload"]["agent_slug"] == "acme"
    assert started["payload"]["kind"] == "session"
    # Both spec hashes recorded with the run (§5.3)
    assert started["payload"]["resolved_spec_hash"]
    # The frozen effective config rides on run_started (no config.yml file).
    assert started["payload"]["frozen_spec"]["config"]["max_ticks"] == 1


def test_experiment_kind_recorded(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch, {"execution_mode": "experiment"})
    assert engine.is_experiment is True
    assert is_run_id(engine.agent_id)
    meta = _run_store().run_meta("acme", engine.agent_id)
    assert meta["kind"] == "experiment"
    assert meta["display_seq"] == 1


def test_display_seq_increments_across_kinds(tmp_path, monkeypatch):
    e1 = _make_engine(tmp_path, monkeypatch, {})
    from condor.agents.engine import TickEngine

    agent = Agent(slug="acme", name="Acme", agent_key="claude-code")
    e2 = TickEngine(
        agent=agent, config={"execution_mode": "experiment"}
    )
    assert (e1.session_num, e2.session_num) == (1, 2)
    assert e1.agent_id != e2.agent_id


def test_engine_falls_back_to_agent_risk_baseline(tmp_path, monkeypatch):
    from condor.agents.engine import TickEngine

    _patch_roots(monkeypatch, tmp_path)
    agent = Agent(
        slug="acme", name="Acme", agent_key="claude-code",
        risk_limits={"max_position_size_quote": 250.0, "max_open_executors": 2},
        denomination="USDC",
    )
    agent.agent_dir.mkdir(parents=True, exist_ok=True)
    engine = TickEngine(agent=agent, config={})
    assert engine.risk.limits.max_position_size_quote == 250.0
    assert engine.risk.limits.max_open_executors == 2

    # An explicit config risk_limits wins over the baseline (stricter-only
    # enforcement happens in lifecycle.start_session, not the engine).
    engine2 = TickEngine(
        agent=agent,
        config={"risk_limits": {"max_position_size_quote": 100.0}},
    )
    assert engine2.risk.limits.max_position_size_quote == 100.0


def test_engine_stop_ends_run(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch, {})

    async def scenario():
        await engine.start()
        await engine.stop()

    asyncio.run(scenario())
    meta = _run_store().run_meta("acme", engine.agent_id)
    assert meta["status"] == "stopped"
    assert meta["ended_at"]


def test_directives_are_durable_events(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch, {})
    engine.inject_directive("stand down on SOL")
    events = _run_store().read_events("acme", engine.agent_id)
    dirs = [e for e in events if e["type"] == "directive"]
    assert dirs and dirs[-1]["payload"] == {
        "text": "stand down on SOL",
        "acked": False,
    }


# ── learnings: flat agent-level curated list ──


def test_learnings_flat_append_and_cap(tmp_path):
    agent_dir = tmp_path / "acme"
    agent_dir.mkdir()
    append_learning(agent_dir, "JTO book is thin after 22:00 UTC")
    append_learning(agent_dir, "grid fills lag on OKX")
    text = read_learnings(agent_dir)
    assert "JTO book is thin after 22:00 UTC" in text
    assert "grid fills lag on OKX" in text

    from condor.agents.learnings import MAX_LEARNINGS

    for i in range(MAX_LEARNINGS + 5):
        append_learning(agent_dir, f"note {i}")
    lines = read_learnings(agent_dir).splitlines()
    assert len(lines) == MAX_LEARNINGS
    assert "JTO book" not in read_learnings(agent_dir)  # oldest evicted


# ── consult: records a kind=consult run, answer returns inline ──


def test_consult_records_a_run(tmp_path, monkeypatch):
    from condor.agents import consult as consult_module
    from condor.agents import run as run_module
    from condor.agents.run import RunResult

    _patch_roots(monkeypatch, tmp_path)
    agent_dir = tmp_path / "oracle"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\nname: oracle\nwhen_to_consult: always\n---\n\nBody.\n"
    )

    async def fake_run(agent, prompt, **kw):
        return RunResult(text="the answer", events=[{"type": "text", "text": "the answer"}])

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    answer = asyncio.run(
        consult_module.run_consult(
            slug="oracle", task="q"
        )
    )
    assert answer == "the answer"
    runs = _run_store().list_runs("oracle", kind="consult")
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["task"] == "q"
    events = _run_store().read_events("oracle", runs[0]["run_id"])
    results = [e for e in events if e["type"] == "state_snapshot"]
    assert results and results[-1]["payload"]["result"] == "the answer"


# ── lifecycle: risk baseline seeding (unchanged semantics) ──


def test_start_seeds_agent_risk_baseline(tmp_path, monkeypatch):
    """start_session must resolve risk_limits as request config >
    AGENT.md default_config > AGENT.md baseline > schema defaults — the
    baseline must not be masked by normalize_config\'s 500/5 schema defaults
    (regression: a live experiment showed 500/5 for a 0/0 agent)."""
    from condor.agents.lifecycle import start_session as lifecycle_start

    _patch_roots(monkeypatch, tmp_path)
    agent_dir = tmp_path / "watcher"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\nname: watcher\n"
        "risk_limits:\n  max_position_size_quote: 0\n  max_open_executors: 0\n"
        "denomination: USD\n"
        "default_config:\n  frequency_sec: 120\n"
        "---\n\nBody.\n"
    )

    captured = {}

    class _FakeEngine:
        def __init__(self, *, agent, config, kind_override="", scheduled_for=""):
            captured["config"] = config
            self.agent_id = "01JZX5B7Q2K4N8P1T3V5W7Y9ZB"
            self.session_num = 1

        async def start(self):
            pass

    import condor.agents.engine as engine_module

    monkeypatch.setattr(engine_module, "TickEngine", _FakeEngine)

    result = asyncio.run(
        lifecycle_start("watcher", config={"execution_mode": "experiment"})
    )
    assert result["started"] is True
    assert captured["config"]["risk_limits"] == {
        "max_position_size_quote": 0,
        "max_open_executors": 0,
    }

    # A default_config-level risk_limits wins over the bare baseline seed.
    (agent_dir / "AGENT.md").write_text(
        "---\nname: watcher\n"
        "risk_limits:\n  max_position_size_quote: 0\n  max_open_executors: 0\n"
        "denomination: USD\n"
        "default_config:\n  risk_limits:\n    max_position_size_quote: 50\n"
        "---\n\nBody.\n"
    )
    asyncio.run(lifecycle_start("watcher", config={"execution_mode": "experiment"}))
    assert captured["config"]["risk_limits"] == {"max_position_size_quote": 50}

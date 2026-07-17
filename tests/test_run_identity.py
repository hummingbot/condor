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
    e2 = TickEngine(agent=agent, config={"execution_mode": "experiment"})
    assert (e1.session_num, e2.session_num) == (1, 2)
    assert e1.agent_id != e2.agent_id


def test_engine_falls_back_to_agent_risk_baseline(tmp_path, monkeypatch):
    from condor.agents.engine import TickEngine

    _patch_roots(monkeypatch, tmp_path)
    agent = Agent(
        slug="acme",
        name="Acme",
        agent_key="claude-code",
        risk_limits={"max_position_size_quote": 250.0, "max_open_executors": 2},
        denomination="USDC",
    )
    agent.agent_dir.mkdir(parents=True, exist_ok=True)
    engine = TickEngine(agent=agent, config={})
    assert engine.risk.limits.max_position_size_quote == 250.0
    assert engine.risk.limits.max_open_executors == 2
    assert engine.frozen_spec.account_ref is not None
    started = _run_store().read_events("acme", engine.agent_id)[0]
    assert started["payload"]["frozen_spec"]["account_ref"] == (
        engine.frozen_spec.account_ref.as_dict()
    )

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


def test_learnings_append_replace_and_full_error(tmp_path):
    """The Hermes-style lifecycle (§7 of insight-flow-simplification): a full
    list ERRORS instead of silently evicting, and replaces= consolidates."""
    from condor.agents.learnings import MAX_LEARNINGS

    agent_dir = tmp_path / "acme"
    agent_dir.mkdir()
    append_learning(agent_dir, "JTO book is thin after 22:00 UTC")
    append_learning(agent_dir, "grid fills lag on OKX")
    text = read_learnings(agent_dir)
    assert "JTO book is thin after 22:00 UTC" in text
    assert "grid fills lag on OKX" in text

    # Consolidation rewrites exactly one matching entry in place.
    append_learning(agent_dir, "JTO book thin 21:00-24:00 UTC", replaces="JTO book")
    text = read_learnings(agent_dir)
    assert "JTO book thin 21:00-24:00 UTC" in text
    assert "after 22:00" not in text
    assert len(text.splitlines()) == 2

    # Zero or ambiguous matches are loud errors, not guesses.
    with pytest.raises(ValueError, match="matches no learning"):
        append_learning(agent_dir, "x", replaces="no such entry")
    append_learning(agent_dir, "JTO spreads widen on weekends")
    with pytest.raises(ValueError, match="matches 2 learnings"):
        append_learning(agent_dir, "x", replaces="JTO")

    # At the cap, plain appends error — knowledge is never silently evicted.
    for i in range(MAX_LEARNINGS - 3):
        append_learning(agent_dir, f"note {i}")
    with pytest.raises(ValueError, match="learnings full"):
        append_learning(agent_dir, "one too many")
    lines = read_learnings(agent_dir).splitlines()
    assert len(lines) == MAX_LEARNINGS
    assert "JTO book thin 21:00-24:00 UTC" in read_learnings(agent_dir)  # kept

    # replaces= still works at the cap — that IS the consolidation path.
    append_learning(agent_dir, "note 0 superseded by venue fix", replaces="note 0")
    assert "superseded by venue fix" in read_learnings(agent_dir)
    assert len(read_learnings(agent_dir).splitlines()) == MAX_LEARNINGS


def test_record_learning_tool_is_agent_scoped(tmp_path, monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import memory as memory_tool

    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "acme").mkdir()

    monkeypatch.setattr(settings, "agent_slug", "acme")
    result = asyncio.run(memory_tool.record_learning("JTO book thins after 22:00 UTC"))
    assert result["recorded"] == "JTO book thins after 22:00 UTC"
    assert result["total"] == 1
    assert "JTO book thins" in read_learnings(tmp_path / "acme")

    # Chat tier (no agent) gets a loud error, not a silent global write.
    monkeypatch.setattr(settings, "agent_slug", "")
    assert "error" in asyncio.run(memory_tool.record_learning("orphan fact"))


# ── tick continuity: decision/state snapshots + prompt persistence ──


def test_tick_emits_decision_state_and_prompt(tmp_path, monkeypatch):
    import hashlib

    from condor.agents import engine as engine_module
    from condor.agents.projections import run_projection
    from condor.agents.run import RunResult

    engine = _make_engine(tmp_path, monkeypatch, {})
    engine.agent.denomination = "SOL"

    async def fake_run_agent(*args, **kwargs):
        return RunResult(text="ENTER FLEA 0.05 SOL — m5/h1 both positive\n\ndetail…")

    async def no_providers():
        return {}

    monkeypatch.setattr(engine_module, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "_collect_provider_state", no_providers)
    asyncio.run(engine._tick())

    events = _run_store().read_events("acme", engine.agent_id)
    run_path = _run_store().find_run_path(engine.agent_id)
    artifacts = next(d for d in run_path.parent.iterdir() if d.is_dir())

    # Frozen prefix persisted once as prompt.md; the tick event carries the
    # per-tick suffix + a sha256 of the full assembled prompt — and the two
    # halves verifiably reconstruct it (no directives in this test).
    started = [e for e in events if e["type"] == "tick_started"][-1]
    suffix = started["payload"]["prompt_suffix"]
    assert "[TICK INFO]" in suffix
    prefix = (artifacts / "prompt.md").read_text()
    assert "[CURRENT CONFIG]" in prefix
    full = f"{prefix}\n\n{suffix}"
    assert started["payload"]["prompt_sha256"] == (
        hashlib.sha256(full.encode()).hexdigest()
    )

    # Mutable context inputs get a baseline context_changed on tick 1.
    ctx = [e for e in events if e["type"] == "context_changed"]
    assert len(ctx) == 1 and set(ctx[0]["payload"]) == {
        "learnings",
        "user_memory",
        "skills",
    }

    snap = [e for e in events if e["type"] == "state_snapshot"][-1]
    assert snap["payload"]["decision"] == "ENTER FLEA 0.05 SOL — m5/h1 both positive"
    assert snap["payload"]["state"].startswith("Last tick: #1 | ")
    assert "SOL" in snap["payload"]["state"]

    # journal.md: the generated one-line-per-tick view.
    journal = (artifacts / "journal.md").read_text()
    assert "tick #1" in journal and "ENTER FLEA" in journal

    # The projection feeds these back into the next tick's prompt sections.
    proj = run_projection(events)
    assert "ENTER FLEA" in proj["recent_decisions"]
    assert proj["state"].startswith("Last tick: #1")

    # A second tick with unchanged context emits NO new context event.
    asyncio.run(engine._tick())
    events = _run_store().read_events("acme", engine.agent_id)
    assert len([e for e in events if e["type"] == "context_changed"]) == 1


def test_bridge_model_notice_never_becomes_a_decision(tmp_path, monkeypatch):
    """A degenerate tick whose whole response is the ACP bridge's model-switch
    notice must not feed [RECENT DECISIONS] (observed in run
    01KXNZZGWN2FM3X7N66VBAMWKH tick 31)."""
    from condor.agents import engine as engine_module
    from condor.agents.projections import run_projection
    from condor.agents.run import RunResult

    engine = _make_engine(tmp_path, monkeypatch, {})

    async def fake_run_agent(*args, **kwargs):
        return RunResult(text="Model switched to claude-sonnet-4-6.")

    async def no_providers():
        return {}

    monkeypatch.setattr(engine_module, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "_collect_provider_state", no_providers)
    asyncio.run(engine._tick())

    events = _run_store().read_events("acme", engine.agent_id)
    snap = [e for e in events if e["type"] == "state_snapshot"][-1]
    assert snap["payload"]["decision"] == ""
    assert "Model switched" in snap["payload"]["response"]  # still on record
    assert run_projection(events)["recent_decisions"] == ""


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
        return RunResult(
            text="the answer", events=[{"type": "text", "text": "the answer"}]
        )

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    answer = asyncio.run(consult_module.run_consult(slug="oracle", task="q"))
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

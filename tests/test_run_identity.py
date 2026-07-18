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


def test_experiment_is_not_a_run_kind(tmp_path, monkeypatch):
    """Experiments are in-memory simulations (condor.agents.experiment) —
    the RunStore must refuse to open a run for them."""
    _patch_roots(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="unknown run kind"):
        _run_store().start_run("acme", "experiment", payload={})


def test_display_seq_increments_across_kinds(tmp_path, monkeypatch):
    e1 = _make_engine(tmp_path, monkeypatch, {})
    sched_id = _run_store().start_run("acme", "scheduled", payload={})
    from condor.agents.engine import TickEngine

    agent = Agent(slug="acme", name="Acme", agent_key="claude-code")
    e2 = TickEngine(agent=agent, config={})
    assert e1.session_num == 1
    assert _run_store().run_meta("acme", sched_id)["display_seq"] == 2
    assert e2.session_num == 3
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


def test_tick_writes_one_md_and_feeds_working_memory(tmp_path, monkeypatch):
    import hashlib

    from condor.agents import engine as engine_module
    from condor.agents.run import RunResult

    engine = _make_engine(tmp_path, monkeypatch, {})
    engine.agent.denomination = "SOL"

    prompts: list[str] = []

    async def fake_run_agent(agent, prompt, **kwargs):
        # run_agent owns the prompt.md write (via prompt_companion, the engine's
        # frozen prefix); the fake stands in for that single owner.
        from condor.agents.run import persist_prompt_companion

        persist_prompt_companion(kwargs["agent_id"], kwargs["prompt_companion"])
        prompts.append(prompt)
        return RunResult(text="ENTER FLEA 0.05 SOL — m5/h1 both positive\n\ndetail…")

    async def no_providers():
        return {}

    monkeypatch.setattr(engine_module, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "_collect_provider_state", no_providers)
    asyncio.run(engine._tick())

    run_dir = _run_store().find_run_path(engine.agent_id).parent

    # The tick's whole record is ONE organized markdown file: tick started
    # (prompt suffix + hash), context baseline, state snapshot, completion.
    tick_md = (run_dir / "1.md").read_text()
    assert tick_md.startswith("# Tick 1 — acme run ")
    assert "## Tick started" in tick_md
    assert "[TICK INFO]" in tick_md  # per-tick prompt suffix, verbatim
    assert "## context_changed" in tick_md  # baseline recorded on tick 1
    assert "## State snapshot" in tick_md
    assert "ENTER FLEA 0.05 SOL — m5/h1 both positive" in tick_md
    assert "Last tick: #1 | " in tick_md
    assert "## Tick completed" in tick_md

    # prompt.md holds the frozen prefix; the sha in the tick file covers the
    # full assembled prompt exactly as sent to run_agent.
    prefix = (run_dir / "prompt.md").read_text()
    assert "[CURRENT CONFIG]" in prefix
    assert prompts[0].startswith(prefix)
    assert hashlib.sha256(prompts[0].encode()).hexdigest() in tick_md

    # The jsonl stays the machine stream — no tick events on it.
    events = _run_store().read_events("acme", engine.agent_id)
    assert [e["type"] for e in events] == ["run_started"]

    # journal.md: the generated one-line-per-tick view.
    journal = (run_dir / "journal.md").read_text()
    assert "tick #1" in journal and "ENTER FLEA" in journal

    # Second tick: its own file; in-memory working context feeds the prompt
    # sections; unchanged context records no new context section.
    asyncio.run(engine._tick())
    tick2 = (run_dir / "2.md").read_text()
    assert "## Tick started" in tick2
    assert "context_changed" not in tick2
    assert "[RECENT DECISIONS" in prompts[1]
    assert "ENTER FLEA 0.05 SOL" in prompts[1]
    assert "Last tick: #1" in prompts[1]  # [CURRENT STATUS]


def test_bridge_model_notice_never_becomes_a_decision(tmp_path, monkeypatch):
    """A degenerate tick whose whole response is the ACP bridge's model-switch
    notice must not feed [RECENT DECISIONS] (observed in run
    01KXNZZGWN2FM3X7N66VBAMWKH tick 31)."""
    from condor.agents import engine as engine_module
    from condor.agents.run import RunResult

    engine = _make_engine(tmp_path, monkeypatch, {})

    async def fake_run_agent(*args, **kwargs):
        return RunResult(text="Model switched to claude-sonnet-4-6.")

    async def no_providers():
        return {}

    monkeypatch.setattr(engine_module, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "_collect_provider_state", no_providers)
    asyncio.run(engine._tick())

    # Not a decision — the working memory stays empty...
    assert engine._decisions == []
    assert engine._last_decision == ""
    # ...but the response is still on the tick's record.
    tick_md = (
        _run_store().find_run_path(engine.agent_id).parent / "1.md"
    ).read_text()
    assert "Model switched" in tick_md
    assert "**Decision:**" not in tick_md


# ── complete_run: the brain's own "job finished" signal ──


def test_agent_self_stops_when_it_declares_task_complete(tmp_path, monkeypatch):
    """declare_complete during a tick ends the run AFTER that tick as
    completed, before the max_ticks budget, and the user gets the final
    report (agent summary + closing state)."""
    from condor.agents import engine as engine_module
    from condor.agents.run import RunResult

    engine = _make_engine(
        tmp_path, monkeypatch, {"frequency_sec": 0, "max_ticks": 5}
    )

    async def fake_run_agent(*args, **kwargs):
        engine.declare_complete("portfolio rebalanced, 2 swaps executed")
        return RunResult(text="Done — within 1% of targets.")

    async def no_providers():
        return {}

    notified: list[str] = []

    async def fake_notify(text, **kw):
        notified.append(text)

    monkeypatch.setattr(engine_module, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "_collect_provider_state", no_providers)
    monkeypatch.setattr("condor.notifications.notify", fake_notify)

    async def scenario():
        await engine.start()
        await asyncio.wait_for(engine._task, timeout=10)

    asyncio.run(scenario())

    meta = _run_store().run_meta("acme", engine.agent_id)
    assert meta["status"] == "completed"
    assert (
        meta["reason"]
        == "agent declared task complete: portfolio rebalanced, 2 swaps executed"
    )
    assert engine._tick_count == 1  # ended before the 5-tick budget
    report = notified[-1]
    assert "🏁" in report and "portfolio rebalanced" in report
    assert "pnl=" in report  # closing state rides along


def test_complete_run_requires_a_live_run():
    from condor.agents.lifecycle import LifecycleError
    from condor.agents.service import AgentService

    with pytest.raises(LifecycleError) as exc:
        AgentService().complete_run("01JZX5B7Q2K4N8P1T3V5W7Y9ZB")
    assert exc.value.status == 404


# ── consult: answer returns inline, NOTHING persists ──


def test_consult_answers_inline_and_persists_nothing(tmp_path, monkeypatch):
    from condor.agents import consult as consult_module
    from condor.agents import run as run_module
    from condor.agents.run import RunResult

    _patch_roots(monkeypatch, tmp_path)
    agent_dir = tmp_path / "oracle"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\nname: oracle\nwhen_to_consult: always\n---\n\nBody.\n"
    )

    captured = {}

    async def fake_run(agent, prompt, **kw):
        captured["agent_id"] = kw.get("agent_id")
        return RunResult(
            text="the answer", events=[{"type": "text", "text": "the answer"}]
        )

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    answer = asyncio.run(consult_module.run_consult(slug="oracle", task="q"))
    assert answer == "the answer"
    # An ephemeral id keyed the approval queue / capability...
    assert is_run_id(captured["agent_id"])
    # ...but nothing landed on disk: no run stream, no consults dir.
    assert _run_store().list_runs("oracle") == []
    assert {p.name for p in agent_dir.iterdir()} == {"AGENT.md"}


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

    result = asyncio.run(lifecycle_start("watcher"))
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
    asyncio.run(lifecycle_start("watcher"))
    assert captured["config"]["risk_limits"] == {"max_position_size_quote": 50}


def test_start_session_rejects_experiment_configs(tmp_path, monkeypatch):
    """dry_run / execution_mode=experiment are the experiment verb's request
    markers — start_session must bounce them with a pointer, not launch."""
    from condor.agents.lifecycle import LifecycleError, start_session

    _patch_roots(monkeypatch, tmp_path)
    for config in ({"dry_run": True}, {"execution_mode": "experiment"}):
        with pytest.raises(LifecycleError) as exc:
            asyncio.run(start_session("whoever", config=config))
        assert exc.value.status == 422
        assert "dry-run" in exc.value.message or "dry_run" in exc.value.message

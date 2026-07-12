"""Unit tests for the refactor-01b identity & storage layer.

Session ids are ``{slug}_{N}`` / ``{slug}_e{N}`` resolving to agent-level
dirs; allocation is mkdir-atomic; meta.yml carries kind/strategy/status;
learnings pool at the agent with strategy-prefix provenance; run_once maps to
an ordinary tick session with max_ticks=1.
"""

import asyncio
import threading

import yaml

from condor.agents import agent as agent_module
from condor.agents import journal as journal_module
from condor.agents import strategy as strategy_module
from condor.agents.agent import Agent
from condor.agents.journal import (
    JournalManager,
    allocate_session_dir,
    finalize_session_meta,
    prune_sessions,
    read_session_meta,
    resolve_agent_dirs,
    split_agent_id,
    write_session_meta,
)


def _patch_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(journal_module, "_DATA_ROOT", tmp_path)


# ── id parsing ──


def test_split_agent_id_single_format():
    assert split_agent_id("mm_expert_3") == ("mm_expert", 3, False)
    assert split_agent_id("mm_expert_e2") == ("mm_expert", 2, True)
    # slugs may contain digits/underscores — only the LAST _N is the session
    assert split_agent_id("agent_7_3") == ("agent_7", 3, False)
    assert split_agent_id("noseparator") is None
    assert split_agent_id("trailing_") is None


def test_resolve_agent_dirs(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    agent_dir = tmp_path / "mm_expert"
    (agent_dir / "sessions" / "session_3").mkdir(parents=True)

    session_dir, base = resolve_agent_dirs("mm_expert_3")
    assert session_dir == agent_dir / "sessions" / "session_3"
    assert base == agent_dir

    # Experiments have no session dir
    session_dir, base = resolve_agent_dirs("mm_expert_e1")
    assert session_dir is None
    assert base == agent_dir

    # Unknown agent → (None, None)
    assert resolve_agent_dirs("ghost_1") == (None, None)
    # Legacy composite ids no longer resolve
    assert resolve_agent_dirs("mm_expert.pmm_1") == (None, None)


# ── allocation ──


def test_allocate_session_dir_sequential_and_concurrent(tmp_path):
    agent_dir = tmp_path / "acme"
    n1, d1 = allocate_session_dir(agent_dir)
    n2, d2 = allocate_session_dir(agent_dir)
    assert (n1, n2) == (1, 2)
    assert d1.name == "session_1" and d2.name == "session_2"

    # Concurrent allocation never hands out the same number (mkdir is the lock).
    got = []
    def alloc():
        got.append(allocate_session_dir(agent_dir)[0])
    threads = [threading.Thread(target=alloc) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(got)) == 8


# ── meta.yml ──


def test_session_meta_roundtrip_and_finalize(tmp_path):
    _, session_dir = allocate_session_dir(tmp_path / "acme")
    write_session_meta(session_dir, {"kind": "tick_loop", "strategy": "pmm", "status": "running"})
    meta = read_session_meta(session_dir)
    assert meta["kind"] == "tick_loop"
    assert meta["strategy"] == "pmm"

    finalize_session_meta(session_dir, "stopped")
    meta = read_session_meta(session_dir)
    assert meta["status"] == "stopped"
    assert meta["ended_at"]

    # Metaless husk → {} (readers tolerate)
    _, husk = allocate_session_dir(tmp_path / "acme")
    assert read_session_meta(husk) == {}


def test_prune_sessions_only_touches_matching_kind(tmp_path):
    agent_dir = tmp_path / "acme"
    for kind in ("consult", "tick_loop", "consult", "consult"):
        _, d = allocate_session_dir(agent_dir)
        write_session_meta(d, {"kind": kind, "status": "done"})
    removed = prune_sessions(agent_dir, kind="consult", keep=1)
    assert removed == 2
    remaining = sorted(d.name for d in (agent_dir / "sessions").iterdir())
    # tick session (2) untouched; newest consult (4) kept
    assert remaining == ["session_2", "session_4"]


# ── learnings provenance ──


def test_learnings_pool_at_agent_with_strategy_prefix(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    agent_dir = tmp_path / "acme"
    _, session_dir = allocate_session_dir(agent_dir)

    jm = JournalManager("acme_1", session_dir=session_dir, agent_dir=agent_dir)
    jm.append_learning("JTO book is thin after 22:00 UTC", category="market", strategy="pmm_v2")
    jm.append_learning("grid fills lag on OKX", category="execution")

    text = (agent_dir / "learnings.md").read_text()
    assert "[pmm_v2] JTO book is thin after 22:00 UTC" in text
    # No prefix without a strategy scope (delegation/consult learnings)
    assert "grid fills lag on OKX" in text
    assert "[pmm_v2] grid fills" not in text

    # Second session of a DIFFERENT strategy reads the same pool
    _, s2 = allocate_session_dir(agent_dir)
    jm2 = JournalManager("acme_2", session_dir=s2, agent_dir=agent_dir)
    assert "JTO book is thin" in jm2.read_learnings()


# ── engine: run_once mapping + agent-level storage ──


def _make_engine(tmp_path, monkeypatch, config):
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    _patch_roots(monkeypatch, tmp_path)
    agent = Agent(slug="acme", name="Acme", agent_key="claude-code")
    agent.agent_dir.mkdir(parents=True, exist_ok=True)
    strategy = Strategy(agent_slug="acme", name="Scalper")
    return TickEngine(agent=agent, strategy=strategy, config=config, chat_id=1, user_id=1)


def test_run_once_becomes_max_ticks_1_session(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch, {"execution_mode": "run_once"})
    # run_once is an ordinary tick session capped at one tick — on the record.
    assert engine.is_experiment is False
    assert engine.config["execution_mode"] == "loop"
    assert engine.config["max_ticks"] == 1
    assert engine.agent_id == "acme_1"
    assert engine.session_dir == tmp_path / "acme" / "sessions" / "session_1"
    assert engine.journal is not None

    meta = read_session_meta(engine.session_dir)
    assert meta["kind"] == "tick_loop"
    assert meta["strategy"] == "scalper"
    assert meta["status"] == "running"
    # Frozen launch config saved in the session dir
    assert (engine.session_dir / "config.yml").exists()


def test_experiment_mode_is_the_only_experiment(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch, {"execution_mode": "experiment"})
    assert engine.is_experiment is True
    assert engine.agent_id == "acme_e1"
    assert engine.session_dir is None
    assert engine.journal is None


def test_engine_falls_back_to_agent_risk_baseline(tmp_path, monkeypatch):
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    _patch_roots(monkeypatch, tmp_path)
    agent = Agent(
        slug="acme", name="Acme", agent_key="claude-code",
        risk_limits={"max_position_size_quote": 250.0, "max_open_executors": 2},
    )
    agent.agent_dir.mkdir(parents=True, exist_ok=True)
    strategy = Strategy(agent_slug="acme", name="Scalper")
    engine = TickEngine(agent=agent, strategy=strategy, config={}, chat_id=1, user_id=1)
    assert engine.risk.limits.max_position_size_quote == 250.0
    assert engine.risk.limits.max_open_executors == 2

    # An explicit config risk_limits wins over the baseline.
    engine2 = TickEngine(
        agent=agent,
        strategy=strategy,
        config={"risk_limits": {"max_position_size_quote": 900.0}},
        chat_id=1,
        user_id=1,
    )
    assert engine2.risk.limits.max_position_size_quote == 900.0


def test_engine_stop_finalizes_meta(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch, {})

    async def scenario():
        await engine.start()
        # Cancel promptly; the first tick will fail fast (no API client in tests)
        await engine.stop()

    asyncio.run(scenario())
    meta = read_session_meta(engine.session_dir)
    assert meta["status"] == "stopped"
    assert meta["ended_at"]


def test_consult_session_persisted_with_retention(tmp_path, monkeypatch):
    """run_consult leaves a kind:consult session behind and prunes old ones."""
    from condor.agents import consult as consult_module
    from condor.agents import run as run_module
    from condor.agents.run import RunResult

    _patch_roots(monkeypatch, tmp_path)
    agent_dir = tmp_path / "oracle"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\nname: oracle\nwhen_to_consult: always\nserver_required: false\n---\n\nBody.\n"
    )

    async def fake_run(agent, prompt, **kw):
        return RunResult(text="the answer", events=[{"type": "text", "text": "the answer"}])

    monkeypatch.setattr(run_module, "run_agent", fake_run)
    monkeypatch.setattr(consult_module, "CONSULT_SESSIONS_KEEP", 2)

    for i in range(3):
        answer = asyncio.run(
            consult_module.run_consult(
                slug="oracle", user_id=1, chat_id=2, server_name=None, task=f"q{i}"
            )
        )
        assert answer == "the answer"

    sessions = sorted(d.name for d in (agent_dir / "sessions").iterdir())
    assert sessions == ["session_2", "session_3"]  # retention cap = 2
    meta = yaml.safe_load((agent_dir / "sessions" / "session_3" / "meta.yml").read_text())
    assert meta["kind"] == "consult"
    assert meta["status"] == "done"
    assert meta["task"] == "q2"
    transcript = (agent_dir / "sessions" / "session_3" / "transcript.md").read_text()
    assert "the answer" in transcript


def test_web_start_seeds_agent_risk_baseline(tmp_path, monkeypatch):
    """The /agents/{slug}/start route must resolve risk_limits as
    request config > strategy default_config > AGENT.md baseline > schema
    defaults — the baseline must not be masked by load_full_config's 500/5
    schema defaults (regression: a live experiment showed 500/5 for a 0/0 agent)."""
    from types import SimpleNamespace

    import condor.web.routes.agents as agents_routes

    _patch_roots(monkeypatch, tmp_path)
    agent_dir = tmp_path / "watcher"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\nname: watcher\nserver_required: true\n"
        "risk_limits:\n  max_position_size_quote: 0\n  max_open_executors: 0\n"
        "---\n\nBody.\n"
    )
    sdir = agent_dir / "strategies" / "snap"
    sdir.mkdir(parents=True)
    (sdir / "strategy.md").write_text(
        "---\nname: snap\ndefault_config:\n  frequency_sec: 120\n---\n\nTick.\n"
    )

    captured = {}

    class _FakeEngine:
        def __init__(self, *, agent, strategy, config, chat_id, user_id):
            captured["config"] = config
            self.agent_id = "watcher_1"
            self.session_num = 1

        async def start(self, bot=None):
            pass

    import condor.agents.engine as engine_module

    monkeypatch.setattr(engine_module, "TickEngine", _FakeEngine)

    req = agents_routes.StartAgentRequest(config={"execution_mode": "experiment"})
    user = SimpleNamespace(id=1)
    result = asyncio.run(agents_routes.start_agent("watcher", req, user=user))
    assert result["started"] is True
    assert captured["config"]["risk_limits"] == {
        "max_position_size_quote": 0,
        "max_open_executors": 0,
    }

    # A strategy-level risk_limits wins over the baseline.
    (sdir / "strategy.md").write_text(
        "---\nname: snap\ndefault_config:\n  risk_limits:\n"
        "    max_position_size_quote: 50\n---\n\nTick.\n"
    )
    asyncio.run(agents_routes.start_agent("watcher", req, user=user))
    assert captured["config"]["risk_limits"] == {"max_position_size_quote": 50}

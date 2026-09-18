"""PERF-057: JournalManager caches journal.md so metric queries do one read.

get_summary_dict() and RiskEngine.get_state() must not re-read/re-parse the
journal once per metric. The cache is keyed by (mtime_ns, size) so writes from
other processes (e.g. the MCP journal_write tool) invalidate it, and the
manager's own writes invalidate it explicitly.

READ-658: the journal no longer keeps an ``## Executors`` ledger. Nothing in
production wrote it, so the tracker only feeds the risk gate its drawdown;
exposure and the open executor count come from the live executors provider.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from condor.agents import engine as engine_module
from condor.agents.journal import JOURNAL_TEMPLATE, JournalManager
from condor.agents.risk import RiskEngine, RiskLimits

_HAND_WRITTEN_EXECUTOR = (
    "- executor=ex1 | type=position_executor | binance BTC-USDT BUY "
    "| amount=$100.00 | created=2026-07-02 00:00 | status=open | pnl=0 | volume=0"
)


def _make_journal(tmp_path, snapshots: int = 1) -> JournalManager:
    jm = JournalManager("test-agent", session_dir=tmp_path)
    for i in range(snapshots):
        jm.record_snapshot(
            total_pnl=5.0 + i,
            total_volume=1000.0 * (i + 1),
            open_count=1,
            position_size=100.0,
        )
    return jm


def _spy_reads(monkeypatch, path: Path) -> dict:
    """Count Path.read_text calls targeting the journal file."""
    calls = {"n": 0}
    original = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        if self == path:
            calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    return calls


def test_get_summary_dict_performs_exactly_one_read(tmp_path, monkeypatch):
    jm = _make_journal(tmp_path)
    calls = _spy_reads(monkeypatch, jm._path)

    summary = jm.get_summary_dict()

    assert calls["n"] == 1
    assert summary == {"total_ticks": 0, "total_volume": 1000.0, "drawdown_pct": 0.0}

    # Warm cache: no further reads while the file is unchanged.
    jm.get_summary_dict()
    assert calls["n"] == 1


def test_risk_get_state_triggers_at_most_one_read(tmp_path, monkeypatch):
    jm = _make_journal(tmp_path, snapshots=5)
    calls = _spy_reads(monkeypatch, jm._path)

    state = RiskEngine(RiskLimits()).get_state(jm)

    assert calls["n"] <= 1
    assert not state.is_blocked


def test_own_writes_invalidate_cache(tmp_path):
    jm = _make_journal(tmp_path)
    assert jm.get_total_volume() == 1000.0

    jm.record_snapshot(
        total_pnl=6.0, total_volume=2500.0, open_count=1, position_size=100.0
    )
    assert jm.get_total_volume() == 2500.0


def test_external_writes_invalidate_cache(tmp_path):
    jm = _make_journal(tmp_path)
    assert jm.get_total_volume() == 1000.0

    # Simulate another process (MCP journal_write) editing the file directly.
    text = jm._path.read_text().replace("volume=$1,000", "volume=$4,000", 1)
    jm._path.write_text(text)

    assert jm.get_total_volume() == 4000.0


# ---------------------------------------------------------------------------
# READ-658: the Executors ledger is gone
# ---------------------------------------------------------------------------


def test_a_fresh_journal_has_no_executors_section(tmp_path):
    assert "## Executors" not in JOURNAL_TEMPLATE
    jm = JournalManager("test-agent", session_dir=tmp_path)
    assert "## Executors" not in jm.read_full()


def test_the_journal_exposes_no_executor_ledger_api():
    for name in (
        "track_executor",
        "update_executor",
        "_parse_executors",
        "get_daily_pnl",
        "get_total_exposure",
        "get_open_executor_count",
        "_append_to_section",
    ):
        assert not hasattr(JournalManager, name), name


def test_get_state_reads_only_drawdown_from_the_tracker():
    class _DrawdownOnly:
        def get_drawdown_pct(self) -> float:
            return 0.0

    state = RiskEngine(RiskLimits()).get_state(_DrawdownOnly())

    assert not state.is_blocked
    assert state.total_exposure == 0.0
    assert state.executor_count == 0


def test_the_engine_ignores_a_hand_written_executor_line(tmp_path):
    """Exposure and count come from the live provider, never from journal.md."""
    jm = JournalManager("test-agent", session_dir=tmp_path)
    jm._path.write_text(
        jm.read_full().replace(
            "## Snapshots\n",
            f"## Executors\n{_HAND_WRITTEN_EXECUTOR}\n\n## Snapshots\n",
        )
    )

    engine = engine_module.TickEngine.__new__(engine_module.TickEngine)
    engine.agent_id = "test-agent"
    engine.config = {}
    engine.journal = jm
    engine.ledger = None
    engine.risk = RiskEngine(RiskLimits())
    engine.is_experiment = False
    engine._last_skill_data = {}
    engine._last_block_reason = ""

    async def _no_adopt(client):
        return None

    class _NoProviders:
        async def run_core_providers(self, *args, **kwargs):
            return {}

    captured = {}

    def _capture_prompt(core_data_summaries, risk_state, live_open_count):
        captured["state"] = risk_state
        return "prompt"

    engine._adopt_running_bots = _no_adopt
    engine.provider_registry = _NoProviders()
    engine._build_prompt = _capture_prompt

    ctx = asyncio.run(engine._gather_tick_context(client=None))

    assert ctx is not None
    assert captured["state"].total_exposure == 0.0
    assert captured["state"].executor_count == 0


def test_get_info_sources_pnl_exposure_and_count_from_the_provider(tmp_path):
    engine = engine_module.TickEngine.__new__(engine_module.TickEngine)
    engine._last_skill_data = {
        "total_pnl": 5.0,
        "total_exposure": 100.0,
        "executors": [{}],
    }
    engine.journal = _make_journal(tmp_path)
    engine.agent_id = "test-agent"
    engine.config = {}
    engine.strategy = SimpleNamespace(name="Test", slug="test")
    engine._agent_key = lambda: "test-agent"
    for attr, value in (
        ("session_num", 1),
        ("status", "running"),
        ("_last_tick_at", None),
        ("_last_error", None),
        ("session_dir", None),
        ("is_experiment", False),
    ):
        try:
            setattr(engine, attr, value)
        except AttributeError:  # a read-only property derives it
            pass

    info = engine.get_info()

    assert info["daily_pnl"] == 5.0
    assert info["total_exposure"] == 100.0
    assert info["open_executors"] == 1

"""PERF-057: JournalManager caches journal.md so metric queries do one read.

get_summary_dict() and RiskEngine.get_state() must not re-read/re-parse the
journal once per metric. The cache is keyed by (mtime_ns, size) so writes from
other processes (e.g. the MCP journal_write tool) invalidate it, and the
manager's own writes invalidate it explicitly.
"""

from pathlib import Path

from condor.agents.journal import JournalManager
from condor.agents.risk import RiskEngine, RiskLimits


def _make_journal(tmp_path) -> JournalManager:
    jm = JournalManager("test-agent", session_dir=tmp_path)
    jm.track_executor(
        "ex1",
        "position_executor",
        {
            "connector_name": "binance",
            "trading_pair": "BTC-USDT",
            "side": "BUY",
            "total_amount_quote": 100,
        },
    )
    jm.record_snapshot(
        total_pnl=5.0, total_volume=1000.0, open_count=1, position_size=100.0
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
    assert summary["daily_pnl"] == 0.0
    assert summary["total_volume"] == 1000.0
    assert summary["total_exposure"] == 100.0
    assert summary["open_executors"] == 1
    assert summary["drawdown_pct"] == 0.0

    # Warm cache: no further reads while the file is unchanged.
    jm.get_summary_dict()
    assert calls["n"] == 1


def test_risk_get_state_triggers_at_most_one_read(tmp_path, monkeypatch):
    jm = _make_journal(tmp_path)
    calls = _spy_reads(monkeypatch, jm._path)

    state = RiskEngine(RiskLimits()).get_state(jm)

    assert calls["n"] <= 1
    assert state.total_exposure == 100.0
    assert state.executor_count == 1
    assert not state.is_blocked


def test_own_writes_invalidate_cache(tmp_path):
    jm = _make_journal(tmp_path)
    assert jm.get_open_executor_count() == 1

    jm.track_executor("ex2", "position_executor", {"total_amount_quote": 50})
    assert jm.get_open_executor_count() == 2
    assert jm.get_total_exposure() == 150.0

    jm.update_executor("ex2", pnl=1.5, volume=10.0, stopped=True)
    assert jm.get_open_executor_count() == 1


def test_external_writes_invalidate_cache(tmp_path):
    jm = _make_journal(tmp_path)
    assert jm.get_open_executor_count() == 1

    # Simulate another process (MCP journal_write) editing the file directly.
    extra = (
        "- executor=ex0 | type=position_executor | binance ETH-USDT BUY "
        "| amount=$25.00 | created=2026-07-02 00:00 | status=open | pnl=0 | volume=0"
    )
    text = jm._path.read_text().replace("- executor=ex1", f"{extra}\n- executor=ex1", 1)
    jm._path.write_text(text)

    assert jm.get_open_executor_count() == 2
    assert jm.get_total_exposure() == 125.0

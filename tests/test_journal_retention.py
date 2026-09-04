"""PERF-136: journal.md is bounded, and a tick rewrites it once.

Two independent claims are pinned here.

*Write amplification.* A tick used to rewrite the entire journal three times
(record_tick, record_snapshot, write_summary) and a blocked tick twice. The
counts below are the point of the change, so they are asserted as counts --
asserting the resulting text would pass just as well with the old code.

*Retention.* Ticks and Snapshots gained one line per tick forever, so per-tick
IO grew with session age. They are now capped, and -- because the journal is
the agent's memory and an audit trail -- the overflow is *moved* to
``journal_archive.md`` rather than deleted, with a marker line saying so. What
a reader sees at that boundary is asserted, not just the cap.
"""

import asyncio

import pytest

import condor.agents.journal as journal_mod
import condor.reports as rep
from condor.agents.journal import JournalManager, count_journal_ticks


@pytest.fixture
def journal(tmp_path) -> JournalManager:
    return JournalManager("test-agent", session_dir=tmp_path)


@pytest.fixture
def writes(monkeypatch) -> list[str]:
    """Record every full rewrite of a journal.md, in order."""
    recorded: list[str] = []
    original = journal_mod.atomic_write_text

    def spy(path, text, **kwargs):
        if str(path).endswith("journal.md"):
            recorded.append(text)
        return original(path, text, **kwargs)

    monkeypatch.setattr(journal_mod, "atomic_write_text", spy)
    return recorded


def _tick_writes(journal: JournalManager, tick: int) -> None:
    """The journal calls one engine tick makes, in engine.py's order."""
    journal.record_tick(response_summary=f"tick {tick}")
    journal.record_snapshot(
        total_pnl=1.0, total_volume=10.0, open_count=1, position_size=100.0
    )
    journal.write_summary(
        tick=tick, status="Running", pnl=1.0, open_count=1, last_action="held"
    )


# ── Write amplification ──


def test_the_unbatched_calls_of_a_tick_write_the_file_three_times(journal, writes):
    """Baseline: this is what every tick used to cost, unconditionally."""
    _tick_writes(journal, 1)

    assert len(writes) == 3


def test_a_batched_tick_writes_the_file_once(journal, writes):
    with journal.batch():
        _tick_writes(journal, 1)

    assert len(writes) == 1
    # ...and the single write carries everything the three used to.
    assert "- tick#1 " in writes[0]
    assert "pnl=$+1.00" in writes[0]
    assert "Last tick: #1" in writes[0]


def test_a_reader_inside_the_batch_sees_the_pending_writes(journal):
    """Nothing inside the block may read a stale journal off the disk."""
    with journal.batch():
        journal.record_tick(response_summary="one")
        journal.record_snapshot(
            total_pnl=7.0, total_volume=1.0, open_count=0, position_size=50.0
        )

        assert journal.tick_count == 1
        assert journal.get_total_volume() == 1.0
        assert journal.get_drawdown_pct() == 0.0
        assert "- tick#1 " in journal.read_full()

    assert "- tick#1 " in journal._path.read_text()


def test_a_batch_that_raises_still_journals_what_it_recorded(journal, writes):
    """A tick blowing up mid-way must not lose the tick it already recorded."""
    with pytest.raises(RuntimeError):
        with journal.batch():
            journal.record_tick(response_summary="recorded before the crash")
            raise RuntimeError("tick exploded")

    assert len(writes) == 1
    assert "recorded before the crash" in journal._path.read_text()


def test_nested_batches_flush_once_at_the_outermost_exit(journal, writes):
    with journal.batch():
        journal.record_tick()
        with journal.batch():
            journal.record_tick()
        assert not writes, "the inner block must not flush"

    assert len(writes) == 1


# ── Write amplification, through the real engine ──


def _engine(tmp_path, monkeypatch):
    from condor.agents import strategy as strategy_module
    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    strategy = Strategy(agent_slug="brigado", name="Grid")
    strategy.home.mkdir(parents=True, exist_ok=True)
    agent = Agent(slug="brigado", name="Brigado")
    return TickEngine(
        agent=agent,
        strategy=strategy,
        config={"execution_mode": "loop"},
        chat_id=1,
        user_id=1,
    )


def _async(result):
    async def go(*args, **kwargs):
        return result

    return go


async def _empty_stream():
    return
    yield  # pragma: no cover -- makes this an async generator


class _FakeACP:
    async def start(self):
        return None

    async def stop(self):
        return None


def test_one_real_engine_tick_rewrites_the_journal_once(tmp_path, monkeypatch, writes):
    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports" / "reports_index.json")
    engine = _engine(tmp_path / "agents", monkeypatch)
    monkeypatch.setattr(engine, "_get_client", _async(object()))
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine, "_create_client", _async(_FakeACP()))
    monkeypatch.setattr(engine, "_notify", _async(None))
    monkeypatch.setattr(engine, "_collect_stream", lambda *a, **k: _empty_stream())
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    writes.clear()  # session setup is not part of the per-tick cost

    asyncio.run(engine._tick())

    assert len(writes) == 1, "a tick must rewrite journal.md exactly once"
    assert engine.journal.tick_count == 1
    text = engine.journal._path.read_text()
    assert "- tick#1 " in text and "Last tick: #1" in text


def test_a_risk_blocked_tick_rewrites_the_journal_once(tmp_path, monkeypatch, writes):
    """The blocked path wrote twice (append_action + record_tick)."""
    from condor.agents.risk import RiskState

    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports" / "reports_index.json")
    engine = _engine(tmp_path / "agents", monkeypatch)
    monkeypatch.setattr(engine, "_get_client", _async(object()))
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine, "_create_client", _async(_FakeACP()))
    monkeypatch.setattr(engine, "_notify", _async(None))
    monkeypatch.setattr(engine, "_collect_stream", lambda *a, **k: _empty_stream())
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    monkeypatch.setattr(
        engine.risk,
        "get_state",
        lambda tracker: RiskState(is_blocked=True, block_reason="max drawdown"),
    )
    writes.clear()

    asyncio.run(engine._tick())

    assert len(writes) == 1
    text = engine.journal._path.read_text()
    assert "tick_blocked" in text and "- tick#1 " in text


# ── Retention ──


@pytest.fixture
def small_caps(monkeypatch):
    monkeypatch.setattr(journal_mod, "MAX_TICK_LINES", 10)
    monkeypatch.setattr(journal_mod, "MAX_SNAPSHOT_LINES", 10)
    return 10


def _tick_lines(journal: JournalManager) -> list[str]:
    return [
        l
        for l in journal._get_section("Ticks").splitlines()
        if l.startswith(journal_mod.TICK_LINE_PREFIX)
    ]


def test_the_ticks_section_never_exceeds_the_cap(journal, small_caps):
    for _ in range(small_caps * 5):
        journal.record_tick()

    assert len(_tick_lines(journal)) == small_caps


def test_the_snapshots_section_never_exceeds_the_cap(journal, small_caps):
    for i in range(small_caps * 5):
        journal.record_snapshot(
            total_pnl=float(i), total_volume=1.0, open_count=0, position_size=10.0
        )

    assert len(journal._parse_snapshots()) == small_caps


def test_what_the_reader_sees_at_the_boundary(journal, small_caps):
    """The visible marker must say what was dropped and where it went."""
    for _ in range(small_caps + 3):
        journal.record_tick()

    lines = journal._get_section("Ticks").splitlines()
    assert lines[0] == "- archived 3 earlier ticks to journal_archive.md"
    assert lines[1].startswith("- tick#4 ")
    assert lines[-1].startswith(f"- tick#{small_caps + 3} ")


def test_the_trimmed_entries_are_archived_not_destroyed(journal, small_caps):
    for i in range(small_caps + 3):
        journal.record_tick(response_summary=f"tick {i + 1}")
    for i in range(small_caps + 2):
        journal.record_snapshot(
            total_pnl=float(i), total_volume=1.0, open_count=0, position_size=10.0
        )

    archive = (journal._session_dir / journal_mod.ARCHIVE_NAME).read_text()
    assert "## Ticks" in archive and "## Snapshots" in archive
    for n in (1, 2, 3):
        assert f"- tick#{n} " in archive
    assert "pnl=$+0.00" in archive, "the oldest snapshot must survive in the sidecar"
    # Nothing that is still live got copied out.
    assert f"- tick#{small_caps + 3} " not in archive


def test_the_marker_is_not_read_back_as_data(journal, small_caps):
    for i in range(small_caps + 5):
        journal.record_snapshot(
            total_pnl=1.0, total_volume=float(i), open_count=0, position_size=10.0
        )

    assert journal._get_section("Snapshots").startswith("- archived ")
    # A marker parsed as a snapshot would show up as a zero-volume point.
    assert all("volume" in s for s in journal._parse_snapshots())
    assert journal.get_total_volume() == float(small_caps + 4)
    assert all(p["timestamp"] for p in journal.get_pnl_series())


def test_the_drawdown_high_water_mark_survives_the_trim(journal, small_caps):
    """A forgotten peak would quietly make the risk gate less protective."""
    journal.record_snapshot(
        total_pnl=100.0, total_volume=1.0, open_count=1, position_size=100.0
    )
    for _ in range(small_caps * 2):
        journal.record_snapshot(
            total_pnl=50.0, total_volume=1.0, open_count=1, position_size=100.0
        )

    assert 100.0 not in [s["pnl"] for s in journal._parse_snapshots()]
    assert journal._archived_peak_pnl() == 100.0
    assert journal.get_drawdown_pct() == pytest.approx(50.0)


def test_tick_numbering_survives_the_trim_across_a_restart(journal, small_caps, writes):
    """Acceptance criterion: numbering resumes from the number, not the count."""
    for _ in range(150):
        journal.record_tick()

    assert journal.tick_count == 150
    assert len(_tick_lines(journal)) == small_caps

    reopened = JournalManager("test-agent", session_dir=journal._session_dir)

    assert reopened.tick_count == 150
    assert reopened.record_tick() == 151
    assert count_journal_ticks(reopened._path) == 151


def test_count_journal_ticks_falls_back_to_counting_unnumbered_lines(tmp_path):
    """A malformed tick line is no reason to restart history at zero."""
    path = tmp_path / "journal.md"
    path.write_text("# Journal\n\n## Ticks\n- tick#x | bad\n- tick#y | bad\n")

    assert count_journal_ticks(path) == 2


def test_an_unwritable_archive_keeps_the_entries_in_the_journal(
    journal, small_caps, caplog
):
    """Losing the sidecar must never mean losing the entries."""
    # Something else already holds the name -- the sidecar cannot be appended to.
    (journal._session_dir / journal_mod.ARCHIVE_NAME).mkdir()

    for _ in range(small_caps + 1):
        journal.record_tick()

    assert len(_tick_lines(journal)) == small_caps + 1
    assert "archiving Ticks failed" in caplog.text

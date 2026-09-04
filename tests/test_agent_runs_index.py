"""The runs index: every run an agent ever had, read off disk (FEAT-099).

The rail behind ``/agents/:slug/runs`` polls this at five seconds, so the two
properties that carry the feature are both asserted here: it says what a run
*is* — kind, ticks, when it started, when it ended — and it reaches nothing but
the filesystem to say it. The third is honesty about absence: a run written
before the action log existed has to be distinguishable from one that recorded
doing nothing, or the spine will colour twenty ticks as "did nothing".
"""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.routing import Match

from condor.agents import agent as agent_module
from condor.agents import sessions_index
from condor.agents import strategy as strategy_module
from condor.agents.sessions_index import infer_latest_session_status, list_runs
from condor.runtime.registry_file import BOOT_ID
from condor.web.routes.agents import router

USER = SimpleNamespace(id=1, is_admin=True)


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Both memoised reads start cold, and leave nothing behind."""
    sessions_index._journal_ticks_cache.clear()
    sessions_index._experiment_info_cache.clear()
    yield
    sessions_index._journal_ticks_cache.clear()
    sessions_index._experiment_info_cache.clear()


def _roots(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)


def _write_agent(root: Path, slug: str, name: str) -> Path:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(f"---\nname: {name}\n---\n\nBody.\n")
    return d


def _write_strategy(root: Path, agent_slug: str, sslug: str, name: str) -> Path:
    d = root / agent_slug / "strategies" / sslug
    d.mkdir(parents=True, exist_ok=True)
    (d / "strategy.md").write_text(f"---\nname: {name}\n---\n\nPlaybook.\n")
    return d


def _journal(ticks: int) -> str:
    lines = ["# Journal", "", "## Ticks", ""]
    for t in range(1, ticks + 1):
        lines.append(f"- tick#{t} | 2026-08-06 22:{t:02d} | actions=0 | did a thing")
    return "\n".join(lines) + "\n"


def _write_session(
    strategy_dir: Path,
    num: int,
    *,
    ticks: int = 0,
    snapshots: int = 0,
    status: dict | None = None,
    actions_log: bool = False,
    dirname: str = "sessions",
    mtime: float | None = None,
) -> Path:
    d = strategy_dir / dirname / f"session_{num}"
    d.mkdir(parents=True, exist_ok=True)
    journal = d / "journal.md"
    journal.write_text(_journal(ticks))
    if mtime is not None:
        os.utime(journal, (mtime, mtime))
    if snapshots:
        snap_dir = d / "snapshots"
        snap_dir.mkdir(exist_ok=True)
        for t in range(1, snapshots + 1):
            (snap_dir / f"snapshot_{t}.md").write_text(
                f"# Snapshot #{t} — 2026-08-06 22:{t:02d}:00\n"
            )
    if status is not None:
        (d / "status.json").write_text(json.dumps(status))
    if actions_log:
        (d / "actions.jsonl").write_text(
            json.dumps(
                {
                    "tick": 1,
                    "at": 1.0,
                    "tool": "stop_executor",
                    "verb": "stop_executor",
                    "summary": "Stop executor e1",
                    "ok": True,
                    "error": "",
                }
            )
            + "\n"
        )
    return d


def _write_experiment(
    strategy_dir: Path, num: int, *, mode: str = "dry_run", error: bool = False
) -> Path:
    d = strategy_dir / "dry_runs"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"experiment_{num}.md"
    response = "(error: status_code: 404)" if error else "Looks flat, holding."
    path.write_text(
        f"# Experiment #{num} — 2026-08-06 22:00:00\n"
        f"Mode: {mode}\n"
        f"Model: claude-code\n\n"
        f"## Agent Response\n{response}\n"
    )
    return path


# ── What a run row says ──


def test_a_session_reports_its_ticks_snapshots_and_end(tmp_path):
    strategy_dir = tmp_path / "brl_mm"
    _write_session(
        strategy_dir,
        3,
        ticks=20,
        snapshots=20,
        status={
            "state": "stopped",
            "agent_id": "brigado.brl_mm_3",
            "boot_id": BOOT_ID,
            "updated_at": 1786088056.7,
        },
    )

    (row,) = list_runs(strategy_dir, "brigado.brl_mm")
    assert row["run_id"] == "s3"
    assert row["kind"] == "session"
    assert row["number"] == 3
    assert row["agent_id"] == "brigado.brl_mm_3"
    assert row["status"] == "stopped"
    assert row["tick_count"] == 20
    assert row["snapshot_count"] == 20
    assert row["started_at"] > 0
    assert row["ended_at"] == 1786088056.7
    assert row["error"] is False


def test_a_live_session_has_no_end(tmp_path):
    """A run still going has no end — the last heartbeat is not one."""
    strategy_dir = tmp_path / "brl_mm"
    _write_session(
        strategy_dir,
        1,
        ticks=4,
        status={"state": "running", "boot_id": BOOT_ID, "updated_at": 1786088056.7},
    )

    (row,) = list_runs(strategy_dir, "brigado.brl_mm")
    assert row["status"] == "running"
    assert row["ended_at"] is None


def test_a_run_a_dead_process_left_running_reads_as_interrupted(tmp_path):
    """FEAT-012's distinction: a foreign boot id means nobody recorded an end."""
    strategy_dir = tmp_path / "brl_mm"
    _write_session(
        strategy_dir,
        1,
        ticks=9,
        status={
            "state": "running",
            "boot_id": "a-process-that-is-gone",
            "updated_at": 1786088056.7,
        },
    )

    (row,) = list_runs(strategy_dir, "brigado.brl_mm")
    assert row["status"] == "interrupted"
    # It is over, whatever it recorded: the heartbeat is the closest thing to
    # an end, and withholding it would render an interrupted run as still live.
    assert row["ended_at"] == 1786088056.7


def test_a_session_with_no_status_file_is_idle_not_a_guess(tmp_path):
    strategy_dir = tmp_path / "brl_mm"
    _write_session(strategy_dir, 1, ticks=2)

    (row,) = list_runs(strategy_dir, "brigado.brl_mm")
    assert row["status"] == "idle"
    assert row["ended_at"] is None
    assert row["agent_id"] == "brigado.brl_mm_1"


def test_a_dry_run_is_a_peer_row_not_a_superscript(tmp_path):
    strategy_dir = tmp_path / "brl_mm"
    _write_experiment(strategy_dir, 1, mode="dry_run")

    (row,) = list_runs(strategy_dir, "brigado.brl_mm")
    assert row["run_id"] == "e1"
    assert row["kind"] == "experiment"
    assert row["execution_mode"] == "dry_run"
    assert row["tick_count"] == 1
    assert row["agent_id"] == "brigado.brl_mm_e1"
    assert row["error"] is False


def test_a_failed_single_tick_says_so(tmp_path):
    strategy_dir = tmp_path / "brl_mm"
    _write_experiment(strategy_dir, 2, mode="run_once", error=True)

    (row,) = list_runs(strategy_dir, "brigado.brl_mm")
    assert row["error"] is True
    assert row["status"] == "error"
    assert row["execution_mode"] == "run_once"


# ── The honest degradation ──


def test_a_run_written_before_the_action_log_is_marked_as_having_none(tmp_path):
    """The spine must not colour twenty un-logged ticks as "did nothing"."""
    strategy_dir = tmp_path / "brl_mm"
    _write_session(strategy_dir, 1, ticks=20, actions_log=False)
    _write_session(strategy_dir, 2, ticks=3, actions_log=True)

    rows = {r["run_id"]: r for r in list_runs(strategy_dir, "brigado.brl_mm")}
    assert rows["s1"]["has_actions_log"] is False
    assert rows["s2"]["has_actions_log"] is True


# ── The layout ──


def test_a_legacy_trading_sessions_layout_is_listed_once(tmp_path):
    strategy_dir = tmp_path / "brl_mm"
    _write_session(strategy_dir, 1, ticks=5, dirname="trading_sessions")
    _write_session(strategy_dir, 1, ticks=7, dirname="sessions")
    _write_session(strategy_dir, 2, ticks=2, dirname="trading_sessions")

    rows = list_runs(strategy_dir, "brigado.brl_mm")
    assert sorted(r["run_id"] for r in rows) == ["s1", "s2"]
    # The current directory name wins the collision.
    assert next(r for r in rows if r["run_id"] == "s1")["tick_count"] == 7


def test_runs_come_back_newest_first(tmp_path):
    strategy_dir = tmp_path / "brl_mm"
    _write_session(strategy_dir, 1, ticks=1)
    _write_session(strategy_dir, 2, ticks=1)
    _write_session(strategy_dir, 3, ticks=1)

    rows = list_runs(strategy_dir, "brigado.brl_mm")
    assert [r["number"] for r in rows] == [3, 2, 1]


def test_a_strategy_with_no_runs_is_empty_not_an_error(tmp_path):
    strategy_dir = tmp_path / "brl_mm"
    strategy_dir.mkdir()
    assert list_runs(strategy_dir, "brigado.brl_mm") == []


# ── The cache ──


def test_an_unchanged_journal_is_read_once(tmp_path, monkeypatch):
    """The rail polls at 5s; a whole journal.md per session per poll is the cost
    this cache exists to remove."""
    strategy_dir = tmp_path / "brl_mm"
    _write_session(strategy_dir, 1, ticks=30)

    reads: list[Path] = []
    real = Path.read_text

    def spy(self, *args, **kwargs):
        if self.name == "journal.md":
            reads.append(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)

    for _ in range(5):
        assert list_runs(strategy_dir, "brigado.brl_mm")[0]["tick_count"] == 30
    assert len(reads) == 1


def test_the_status_summary_reads_an_unchanged_journal_once(tmp_path, monkeypatch):
    """``infer_latest_session_status`` goes through the same memo (PERF-323).

    It is the hotter caller of the two: ``/api/v1/agents`` runs it once per
    strategy on every chat-rail poll, so an uncached read here costs a whole
    ``journal.md`` per strategy, several times a minute.
    """
    strategy_dir = tmp_path / "brl_mm"
    _write_session(strategy_dir, 1, ticks=30)

    reads: list[Path] = []
    real = Path.read_text

    def spy(self, *args, **kwargs):
        if self.name == "journal.md":
            reads.append(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)

    for _ in range(2):
        status = infer_latest_session_status(strategy_dir, "brigado.brl_mm")
        assert status["tick_count"] == 30
    assert len(reads) == 1


def test_a_journal_that_grew_is_re_read(tmp_path):
    strategy_dir = tmp_path / "brl_mm"
    session_dir = _write_session(strategy_dir, 1, ticks=2, mtime=1000)
    assert list_runs(strategy_dir, "brigado.brl_mm")[0]["tick_count"] == 2

    journal = session_dir / "journal.md"
    journal.write_text(_journal(9))
    os.utime(journal, (2000, 2000))
    assert list_runs(strategy_dir, "brigado.brl_mm")[0]["tick_count"] == 9


# ── The route ──


def _call_route(slug="brigado"):
    from condor.web.routes.agents import list_agent_runs

    return asyncio.run(list_agent_runs(slug, user=USER))


def test_the_route_folds_every_strategy_into_one_rail(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    brl = _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    pmm = _write_strategy(tmp_path, "brigado", "pmm_king", "PMM King")
    _write_session(brl, 1, ticks=3)
    _write_session(pmm, 1, ticks=1)
    _write_experiment(pmm, 1)

    runs = _call_route().runs
    assert len(runs) == 3
    assert {r.strategy_slug for r in runs} == {"brl_mm", "pmm_king"}
    assert {r.strategy_name for r in runs} == {"BRL MM", "PMM King"}
    # Newest first across strategies, not grouped by one.
    stamps = [r.started_at or 0.0 for r in runs]
    assert stamps == sorted(stamps, reverse=True)


def test_the_route_carries_no_money_at_all(monkeypatch, tmp_path):
    """Removing the fake zeros is half the point: a rail row has no PnL field
    to render as ``+$0.00``."""
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    brl = _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    _write_session(brl, 1, ticks=3)

    (row,) = _call_route().runs
    fields = set(row.model_dump())
    assert not {f for f in fields if "pnl" in f or "volume" in f or "fees" in f}


def test_the_route_makes_no_hummingbot_call(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    brl = _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    _write_session(brl, 1, ticks=3, snapshots=3)
    _write_experiment(brl, 1)

    import config_manager

    async def _boom(*args, **kwargs):  # pragma: no cover - it must never run
        raise AssertionError("the runs route reached for a Hummingbot client")

    monkeypatch.setattr(config_manager.ConfigManager, "get_client", _boom)
    assert len(_call_route().runs) == 2


def test_an_unreadable_strategy_costs_only_its_own_rows(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    brl = _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    broken = _write_strategy(tmp_path, "brigado", "broken", "Broken")
    _write_session(brl, 1, ticks=3)

    import condor.agents.sessions_index as index_module

    real = index_module.list_runs

    def boom(strategy_dir, run_key):
        if strategy_dir == broken:
            raise OSError("unreadable")
        return real(strategy_dir, run_key)

    monkeypatch.setattr(index_module, "list_runs", boom)

    runs = _call_route().runs
    assert [r.strategy_slug for r in runs] == ["brl_mm"]


def test_an_unknown_agent_is_a_404(monkeypatch, tmp_path):
    from fastapi import HTTPException

    _roots(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        _call_route("nobody")
    assert excinfo.value.status_code == 404


def test_the_runs_route_is_not_shadowed_by_the_slug_catch_all():
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/agents/brigado/runs",
        "path_params": {},
    }
    for route in router.routes:
        if route.matches(scope)[0] == Match.FULL:
            assert route.endpoint.__name__ == "list_agent_runs"
            return
    raise AssertionError("no route matched /agents/brigado/runs")

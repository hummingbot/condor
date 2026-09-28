"""The boot that renames an agent's ``strategies/`` to ``loops/`` (FEAT-128, v5).

The playbook is called a loop everywhere a person or a model reads it, so the
folder follows: ``<local>/<slug>/strategies/<s>/strategy.md`` becomes
``<local>/<slug>/loops/<s>/loop.md``. The stock half arrives renamed through git;
v5 renames the local half, which is where every session, state file, learning
and ownership record lives. Pinned here: it is a pure rename (bytes identical),
it merges rather than overwrites, it is idempotent, and every reader that used
to find a run under ``strategies/`` still finds it -- including the two that
fail *silently* (runtime state and boot recovery).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from condor import paths
from condor.migrations import (
    MARKER_FILENAME,
    MARKER_V2_FILENAME,
    MARKER_V3_FILENAME,
    MARKER_V4_FILENAME,
    MARKER_V5_FILENAME,
    ensure_migrated,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Stock + local + runtime roots, with v1-v4 already done (a post-v4 box)."""
    stock = tmp_path / "stock"
    local = tmp_path / "local"
    runtime = tmp_path / "runtime"
    stock.mkdir()
    monkeypatch.setenv(paths.STOCK_AGENTS_ROOT_ENV, str(stock))
    monkeypatch.setenv(paths.AGENTS_ROOT_ENV, str(local))
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(runtime))
    for marker in (
        MARKER_FILENAME,
        MARKER_V2_FILENAME,
        MARKER_V3_FILENAME,
        MARKER_V4_FILENAME,
    ):
        _write(runtime / marker, "done\n")
    return stock, local, runtime


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _old_loop(local: Path, agent="brigado", sslug="mm") -> Path:
    home = local / agent / "strategies" / sslug
    _write(home / "strategy.md", "---\nname: MM\n---\n\ntick\n")
    _write(home / "learnings.md", "learned")
    _write(home / "state.json", json.dumps({"entries": {"cursor": {"v": "e-42"}}}))
    _write(home / "owned_bots.json", '["bot-1"]')
    _write(home / "config.yml", "frequency_sec: 60\n")
    _write(home / "sessions" / "session_1" / "journal.md", "ticked")
    _write(home / "sessions" / "session_1" / "status.json", '{"state": "stopped"}')
    return home


def test_a_local_strategies_dir_becomes_loops_byte_for_byte(roots):
    _, local, runtime = roots
    home = _old_loop(local)
    before = _snapshot(home)

    report = ensure_migrated()

    moved = local / "brigado" / "loops" / "mm"
    assert not (local / "brigado" / "strategies").exists()
    after = _snapshot(moved)
    # strategy.md is the one name that changes; every byte stays.
    before["loop.md"] = before.pop("strategy.md")
    assert after == before
    assert report.loops_renamed == 1 and report.loop_md_renamed == 1
    assert (runtime / MARKER_V5_FILENAME).is_file()


def test_a_second_boot_changes_nothing(roots):
    _, local, runtime = roots
    _old_loop(local)
    ensure_migrated()
    snapshot = _snapshot(local)

    # The marker is a fast path; the steps are idempotent without it too.
    (runtime / MARKER_V5_FILENAME).unlink()
    assert ensure_migrated().total == 0
    assert ensure_migrated().total == 0
    assert _snapshot(local) == snapshot


def test_a_stock_loop_with_local_runtime_still_resolves_both_halves(roots):
    """The split ``ema_trend_loop`` case: loop.md in stock, sessions in local."""
    from condor.agents.agent import AgentStore
    from condor.agents.strategy import StrategyStore

    stock, local, _ = roots
    _write(stock / "trend" / "AGENT.md", "---\nname: Trend\n---\n\nTrend.\n")
    _write(stock / "trend" / "loops" / "ema" / "loop.md", "---\nname: EMA\n---\n\nx\n")
    _write(local / "trend" / "strategies" / "ema" / "sessions" / "session_3" / "j.md")
    assert AgentStore().get("trend") is not None

    ensure_migrated()

    loop = StrategyStore().get("trend", "ema")
    assert loop is not None and loop.name == "EMA"
    assert loop.source == stock / "trend" / "loops" / "ema" / "loop.md"
    assert loop.home == local / "trend" / "loops" / "ema"
    assert (loop.home / "sessions" / "session_3" / "j.md").exists()


def test_an_existing_loops_dir_is_merged_the_local_copy_winning(roots):
    _, local, runtime = roots
    _write(local / "brigado" / "strategies" / "mm" / "learnings.md", "old")
    _write(local / "brigado" / "strategies" / "mm" / "sessions" / "session_1" / "j")
    _write(local / "brigado" / "loops" / "mm" / "learnings.md", "new")

    report = ensure_migrated()

    loops = local / "brigado" / "loops" / "mm"
    assert not (local / "brigado" / "strategies").exists()
    assert (loops / "learnings.md").read_text() == "new"
    assert (loops / "sessions" / "session_1" / "j").exists()
    backup = runtime / "migration-backups" / "loops" / "brigado" / "mm"
    assert (backup / "learnings.md").read_text() == "old"
    assert report.agent_backups == 1 and report.loops_renamed == 1


def test_strategy_md_beside_loop_md_keeps_loop_md(roots):
    _, local, runtime = roots
    _write(local / "a" / "loops" / "same" / "strategy.md", "same")
    _write(local / "a" / "loops" / "same" / "loop.md", "same")
    _write(local / "a" / "loops" / "diff" / "strategy.md", "old")
    _write(local / "a" / "loops" / "diff" / "loop.md", "new")

    ensure_migrated()

    assert not (local / "a" / "loops" / "same" / "strategy.md").exists()
    assert (local / "a" / "loops" / "same" / "loop.md").read_text() == "same"
    assert not (local / "a" / "loops" / "diff" / "strategy.md").exists()
    assert (local / "a" / "loops" / "diff" / "loop.md").read_text() == "new"
    backup = runtime / "migration-backups" / "loops" / "a" / "diff" / "strategy.md"
    assert backup.read_text() == "old"


def test_an_old_install_runs_v2_then_v5_in_one_boot(tmp_path, monkeypatch):
    """v2 still writes ``strategies/`` (it is history); v5 renames what it made."""
    stock = tmp_path / "stock"
    monkeypatch.setenv(paths.STOCK_AGENTS_ROOT_ENV, str(stock))
    monkeypatch.setenv(paths.AGENTS_ROOT_ENV, str(tmp_path / "local"))
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path / "runtime"))
    # Pre-FEAT-115 shape: runtime output under the stock tree's strategies/.
    _write(stock / "scout" / "AGENT.md", "---\nname: Scout\n---\n\nS.\n")
    _write(stock / "scout" / "strategies" / "grid" / "learnings.md", "learned")
    _write(stock / "scout" / "strategies" / "grid" / "sessions" / "session_1" / "j")

    report = ensure_migrated()

    grid = tmp_path / "local" / "scout" / "loops" / "grid"
    assert (grid / "learnings.md").read_text() == "learned"
    assert (grid / "sessions" / "session_1" / "j").exists()
    assert not (tmp_path / "local" / "scout" / "strategies").exists()
    assert report.agent_artefacts == 2 and report.loops_renamed == 1


def test_runtime_state_is_still_found_after_the_rename(roots, monkeypatch):
    """The silent trap: a missed literal falls back to a namespace dir."""
    from condor.runtime import state as state_module

    _, local, _ = roots
    _old_loop(local)
    ensure_migrated()
    monkeypatch.setattr(state_module, "_cache", {})
    monkeypatch.setattr(state_module, "_last_write", {})
    monkeypatch.setattr(state_module, "_dirty", set())

    assert state_module.load_namespace("brigado.mm") == 1
    assert "cursor" in state_module._entries("brigado.mm")
    assert state_module._state_dir("brigado.mm") == local / "brigado" / "loops" / "mm"
    # ...and the cache is not evicted as an orphan.
    assert state_module.cleanup_orphans() == 0


def test_boot_recovery_finds_a_crashed_session_under_loops(roots):
    from condor.runtime.loops import LoopSupervisor
    from condor.runtime.registry_file import LoopState

    _, local, _ = roots
    session = local / "brigado" / "strategies" / "mm" / "sessions" / "session_2"
    _write(
        session / "status.json",
        json.dumps({"state": LoopState.RUNNING, "boot_id": "a-dead-process"}),
    )

    ensure_migrated()

    found = list(LoopSupervisor()._sessions_to_settle(None))
    moved = local / "brigado" / "loops" / "mm" / "sessions" / "session_2"
    assert [(d, crashed) for d, _, crashed in found] == [(moved, True)]


def test_a_local_skill_fork_and_the_mutes_follow_the_new_names(roots):
    _, local, _ = roots
    fork = local / "_shared" / "skills" / "strategy_builder" / "SKILL.md"
    _write(fork, "---\nname: strategy_builder\ndescription: mine\n---\n\nBody\n")
    _write(
        local / "brigado" / "mutes.yml",
        "skills: [strategy_builder, lp]\ntools: [manage_strategies]\n",
    )
    _write(local / "scout" / "mutes.yml", "skills: [lp]\n")
    untouched = (local / "scout" / "mutes.yml").read_text()

    report = ensure_migrated()

    moved = local / "_shared" / "skills" / "loop_builder" / "SKILL.md"
    assert not fork.exists()
    assert "\nname: loop_builder\n" in moved.read_text()
    mutes = yaml.safe_load((local / "brigado" / "mutes.yml").read_text())
    assert mutes == {"skills": ["loop_builder", "lp"], "tools": ["manage_loops"]}
    assert (local / "scout" / "mutes.yml").read_text() == untouched
    assert report.mutes_rewritten == 1


# ── nothing in condor/ still names the old layout ──

_ALLOWED = {
    # v2 is history: it runs before v5 and writes what v5 then renames.
    Path("condor/migrations.py"),
    # A legacy *root-level* agents/strategies folder, not the per-agent one.
    Path("condor/memory/paths.py"),
}


def test_no_stray_old_layout_literal_under_condor():
    """A literal that bypasses LOOPS_DIRNAME / LOOP_MD reads the wrong folder."""
    pattern = re.compile(r"""["'](strategies|strategy\.md)["']""")
    offenders = []
    for path in sorted((REPO_ROOT / "condor").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        if rel in _ALLOWED:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert offenders == []

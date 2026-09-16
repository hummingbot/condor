"""agent_id → disk path: one parse, one strategy-dir composition (ARCH-676).

``enumerate_agent_ids`` writes the ids; ``parse_agent_id`` is their inverse,
and ``resolve_agent_dirs`` / the MCP ``_resolve_experiment_file`` build paths
from it with the same composition as ``Strategy.home``, honouring the legacy
``trading_sessions/`` and ``experiments/`` layouts.
"""

import pytest

from condor.agents import sessions_index
from condor.agents.journal import JournalManager, resolve_agent_dirs
from condor.agents.sessions_index import parse_agent_id, strategy_dir_for_run_key
from mcp_servers.condor.tools.trading_agent import _resolve_experiment_file


@pytest.fixture
def agents_root(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(root))
    return root


def _touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_parse_agent_id_inverts_every_id_enumerate_agent_ids_emits(tmp_path):
    strategy_dir = tmp_path / "s"
    _touch(strategy_dir / "sessions" / "session_1" / "journal.md")
    _touch(strategy_dir / "trading_sessions" / "session_2" / "journal.md")
    _touch(strategy_dir / "dry_runs" / "experiment_3.md")
    _touch(strategy_dir / "experiments" / "experiment_12.md")

    ids = sessions_index.enumerate_agent_ids("a.s", strategy_dir)
    assert {kind for _, _, kind in ids} == {"session", "experiment"}
    assert len(ids) == 4
    for agent_id, n, kind in ids:
        assert parse_agent_id(agent_id) == ("a.s", n, kind)


@pytest.mark.parametrize("bad", ["a.s", "a.s_x", "a.s_e", "a.s_", "_1", "a.s_1x"])
def test_parse_agent_id_rejects_malformed_ids(bad):
    assert parse_agent_id(bad) is None


def test_strategy_dir_is_the_strategy_home_composition(agents_root):
    assert strategy_dir_for_run_key("a.s") == agents_root / "a" / "strategies" / "s"
    # The dot-less legacy flat prefix has no producer since FEAT-004.
    assert strategy_dir_for_run_key("flat") is None


def test_a_legacy_trading_sessions_dir_is_resolved_not_shadowed(agents_root):
    strategy_dir = agents_root / "a" / "strategies" / "s"
    legacy = strategy_dir / "trading_sessions" / "session_1"
    _touch(legacy / "journal.md", "# Journal - a.s_1\n")

    assert resolve_agent_dirs("a.s_1") == (legacy, strategy_dir)
    # The MCP fallback call shape must not create an empty current-layout twin.
    JournalManager("a.s_1", *resolve_agent_dirs("a.s_1"))
    assert not (strategy_dir / "sessions" / "session_1").exists()


def test_a_session_not_yet_on_disk_resolves_to_the_current_layout(agents_root):
    strategy_dir = agents_root / "a" / "strategies" / "s"
    strategy_dir.mkdir(parents=True)
    assert resolve_agent_dirs("a.s_4") == (
        strategy_dir / "sessions" / "session_4",
        strategy_dir,
    )
    assert resolve_agent_dirs("a.s_e4") == (None, strategy_dir)


def test_unknown_or_malformed_ids_resolve_to_nothing(agents_root):
    (agents_root / "a" / "strategies" / "s").mkdir(parents=True)
    (agents_root / "flat").mkdir(parents=True)
    assert resolve_agent_dirs("a.missing_1") == (None, None)
    assert resolve_agent_dirs("a.s_x") == (None, None)
    assert resolve_agent_dirs("flat_1") == (None, None)


def test_resolve_experiment_file_finds_the_legacy_dir(agents_root):
    strategy_dir = agents_root / "a" / "strategies" / "s"
    _touch(strategy_dir / "experiments" / "experiment_2.md")

    assert _resolve_experiment_file("a.s_e2") == (
        strategy_dir / "experiments" / "experiment_2.md",
        2,
    )
    assert _resolve_experiment_file("a.s_e3") == (None, 3)
    assert _resolve_experiment_file("a.missing_e3") == (None, 3)
    assert _resolve_experiment_file("a.s_2") == (None, None)

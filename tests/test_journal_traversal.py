"""A journal agent_id is resolved inside the agents root, or not at all (SEC-678).

The MCP journal tools hand a model-supplied ``agent_id`` to
``resolve_agent_dirs`` whenever no engine is registered under it. The strategy
half of the id now goes through the same one-segment rule as every agent slug
(``condor.memory.paths.safe_slug``, SEC-648) in
``sessions_index.strategy_dir_for_run_key``, so a ``..`` or ``/`` in it names
no strategy -- while a unicode slug ``slugify`` produces still resolves.
"""

import os

import pytest

from condor.agents.journal import JournalManager, resolve_agent_dirs
from mcp_servers.condor.tools import trading_agent


@pytest.fixture
def agents(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(root))
    (root / "brigado" / "strategies" / "grid").mkdir(parents=True)
    import condor.agents.engine as engine_module

    monkeypatch.setattr(engine_module, "get_engine", lambda _aid: None)
    return root


@pytest.fixture
def victim(tmp_path):
    d = tmp_path / "victim"
    d.mkdir()
    return d


def _rel(agents, victim):
    return os.path.relpath(victim, agents / "brigado" / "strategies")


def test_a_traversal_id_resolves_nothing_and_writes_nothing(agents, victim):
    aid = f"brigado.{_rel(agents, victim)}_1"

    assert resolve_agent_dirs(aid) == (None, None)
    assert trading_agent.journal_write(
        agent_id=aid, entry_type="learning", text="x"
    ) == {"error": "no journal available for this agent"}
    assert trading_agent.journal_read(agent_id=aid, section="learnings") == {
        "content": "(no journal available for this agent)"
    }
    assert list(victim.rglob("*")) == []


def test_a_slashed_id_without_dots_is_refused(agents):
    # The nested dir exists, so only the one-segment rule refuses it.
    (agents / "condor" / "strategies" / "other" / "deep").mkdir(parents=True)
    assert resolve_agent_dirs("condor.other/deep_1") == (None, None)


def test_an_experiment_traversal_id_reads_nothing_outside(agents, victim):
    (victim / "dry_runs").mkdir()
    (victim / "dry_runs" / "experiment_1.md").write_text("secret")
    aid = f"brigado.{_rel(agents, victim)}_e1"

    assert trading_agent._resolve_experiment_file(aid) == (None, 1)


def test_the_journal_manager_fallback_refuses_a_traversal_id(agents, victim):
    with pytest.raises(ValueError):
        JournalManager("../victim/flat_1")
    assert list(victim.rglob("*")) == []


def test_well_formed_and_unicode_ids_still_resolve(agents):
    session, base = resolve_agent_dirs("brigado.grid_1")
    assert base == agents / "brigado" / "strategies" / "grid"
    assert session == base / "sessions" / "session_1"

    (agents / "señor_trader" / "strategies" / "grid").mkdir(parents=True)
    session, base = resolve_agent_dirs("señor_trader.grid_1")
    assert base == agents / "señor_trader" / "strategies" / "grid"
    assert session == base / "sessions" / "session_1"

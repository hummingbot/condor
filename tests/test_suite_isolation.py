"""The suite must not write into the developer's live install (FEAT-051).

This is the wound that motivated the one-root refactor: every store derived its
own root, so isolating one meant remembering to monkeypatch a private function
in every module that touched it — and the modules that forgot left 862 stub
conversations and a stream of test delegations inside a running install.

Asserting on "nothing appeared on disk after the run" would be flaky (the real
bot may be running while the suite is), so what is pinned here is the mechanism
instead: with the autouse fixture in ``conftest.py`` active, every path a
writer can build resolves outside the repository. If someone reintroduces a
root that ignores ``CONDOR_RUNTIME_ROOT``, ``CONDOR_DATA_DIR`` or
``CONDOR_AGENTS_ROOT``, one of these fails.

All three roots are covered, because all three are durable and all three were
reachable from a test: ``.condor/`` (conversations, delegations, state,
telemetry), ``data/`` (the bell, routine hooks, backtests, code runs) -- whose
three cwd-relative constants READ-215 replaced with resolvers -- and
``agents/`` (every agent's per-user memory and skill library), the one READ-215
did not reach and CORR-220 did.
"""

from pathlib import Path

from condor import backtest_store, code_runs, paths
from condor.agents.delegate import DelegateTask, _record_dir
from condor.memory import paths as memory_paths

REPO = Path(__file__).resolve().parent.parent


def _outside_the_repo(path: Path) -> bool:
    return not path.resolve().is_relative_to(REPO)


def test_the_runtime_root_is_not_the_live_one():
    assert paths.runtime_root() != REPO / ".condor"
    assert _outside_the_repo(paths.runtime_root())


def test_no_store_resolves_inside_the_repository():
    assert _outside_the_repo(paths.conversation_dir(42, "abc"))
    assert _outside_the_repo(paths.delegation_dir(42, "t"))
    assert _outside_the_repo(paths.state_dir("ns"))
    assert _outside_the_repo(paths.telemetry_dir())


def test_the_agent_stores_are_isolated_too():
    """``agents/`` is the third root, and the one a test *writes* to.

    ``MemoryStore`` and ``SkillStore`` both ``mkdir(parents=True)`` their root
    on the first write, so a module that forgot the old per-module monkeypatch
    left stub memories and skills in the developer's live library. Every path
    the two stores can build is asserted here, the default slug and a named
    agent alike.
    """
    assert paths.agents_root() != REPO / "agents"
    assert _outside_the_repo(paths.agents_root())
    assert _outside_the_repo(memory_paths.assistant_home())
    assert _outside_the_repo(memory_paths.assistant_home("grid_scalper"))
    assert _outside_the_repo(memory_paths.store_root(42))
    assert _outside_the_repo(memory_paths.store_root(42, "grid_scalper"))
    assert _outside_the_repo(memory_paths.builtin_skills_root("grid_scalper"))
    assert _outside_the_repo(memory_paths.shared_skills_root())
    assert _outside_the_repo(memory_paths.shared_routines_root())


def test_a_memory_would_be_written_outside_the_install():
    """The writer's own root, not a reconstruction of it."""
    from condor.memory.store import MemoryStore

    assert _outside_the_repo(MemoryStore(424242, "grid_scalper").root)


def test_a_delegation_would_be_written_outside_the_install():
    """The writer's own path, not a reconstruction of it."""
    dt = DelegateTask(
        task_id="scout-delegate-abc",
        agent_slug="scout",
        user_id=7,
        chat_id=1,
        server_name=None,
        task="scan",
    )

    assert _outside_the_repo(_record_dir(dt))
    # And nowhere near the agent's own directory any more.
    assert "agents" not in _record_dir(dt).parts


def test_the_operational_store_is_isolated_too():
    """``data/`` is the second durable root, and a test must not land in it."""
    assert paths.data_dir() != REPO / "data"
    assert _outside_the_repo(paths.data_dir())
    assert _outside_the_repo(paths.notifications_path())
    assert _outside_the_repo(paths.routine_hooks_path())
    assert _outside_the_repo(paths.backtests_dir())
    assert _outside_the_repo(paths.legacy_backtests_file())
    assert _outside_the_repo(paths.code_runs_dir())


def test_the_default_stores_land_outside_the_install():
    """The writers' own defaults, not a reconstruction of them.

    ``BacktestStore()`` and ``CodeRunStore()`` both create their directory in
    ``__init__``, so a default that had stayed bound at import would make this
    test itself write into the live ``data/``.
    """
    assert _outside_the_repo(backtest_store.BacktestStore()._dir)
    assert _outside_the_repo(code_runs.CodeRunStore()._dir)

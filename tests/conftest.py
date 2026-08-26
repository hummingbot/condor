"""Test helpers shared across the suite."""

import importlib.util
import sys

import pytest

from condor.memory.paths import shared_routines_root


def load_shared_routine(name: str):
    """Import a routine from ``agents/_shared/routines`` as a module.

    A shared routine (FEAT-038) lives outside any package, so it has no dotted
    import path — production loads it exactly this way, from its file. Tests that
    need the module's internals (not just the ``RoutineInfo``) go through here so
    they exercise the same loading the routine actually gets.
    """
    module_name = f"shared_routine_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    path = shared_routines_root() / f"{name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:  # pragma: no cover - a missing file is the bug
        raise ImportError(f"No shared routine '{name}' at {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _reset_gecko_throttle():
    """Give every test the full GeckoTerminal budget.

    The limiter is process-wide and window-based on real time, so without this a
    fast suite spends the whole minute's budget inside the first few tests and the
    rest fail on a throttle that has nothing to do with what they assert.
    """
    from condor.pool_data import reset_gecko_throttle

    reset_gecko_throttle()
    yield


@pytest.fixture(autouse=True)
def _isolated_runtime_root(tmp_path, monkeypatch):
    """Keep all three durable roots out of the developer's live install.

    For as long as every store derived its own root there was nothing to
    repoint, so four test modules each had to remember to monkeypatch a private
    ``_root`` and four others forgot -- which is how 812 stub conversations
    ended up in a real install (FEAT-051). One env var per root, one fixture.

    The second line covers ``data/``: the bell, the routine hooks, the backtest
    store and the code runs used to need a per-module monkeypatch of a private
    name each (and a test that forgot appended to the running install's
    notification bell, or dropped a record among the live ones in
    ``data/code_runs/``). They all resolve through ``condor.paths`` now, so
    ``$CONDOR_DATA_DIR`` moves the lot.

    The third line covers ``agents/``: every ``MemoryStore`` and ``SkillStore``
    hangs off it and ``mkdir(parents=True)``s on write, so a module that did not
    monkeypatch ``condor.memory.paths._PROJECT_ROOT`` by hand wrote into the
    developer's own memory and skill library -- it left an ``audit.log`` for a
    ``user_424242`` that appears nowhere in the repo. Ten modules remembered;
    the knob means none of them has to (CORR-220).
    """
    from condor import paths

    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path / "condor-runtime"))
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "condor-data"))
    # ``tmp_path / "agents"`` and not ``condor-agents``: ``tmp_path`` stands in
    # for the repo root in the agent tests, and several roots still out of scope
    # here (``agent.py``/``strategy.py``'s ``_DATA_ROOT``) are monkeypatched to
    # that same directory, so the registry and the stores stay one tree.
    monkeypatch.setenv(paths.AGENTS_ROOT_ENV, str(tmp_path / "agents"))

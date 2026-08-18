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

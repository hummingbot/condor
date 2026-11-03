"""Shared isolation for the runtime tests.

Creating a session now mints a durable conversation (FEAT-015), so without
this every test that starts one would leave a transcript in the developer's
real runtime store.
"""

import pytest

from condor import paths
from condor.runtime import conversations


@pytest.fixture(autouse=True)
def isolated_conversation_root(_isolated_runtime_root, monkeypatch):
    """The throwaway users root, plus a clean set of live recorders.

    The root itself is isolated suite-wide by ``_isolated_runtime_root`` (one
    env var, ``tests/conftest.py``); this only depends on it explicitly so the
    ordering is stated rather than inherited, and returns the root the tests
    assert against.
    """
    monkeypatch.setattr(conversations, "_live_recorders", set())
    return paths.users_root()

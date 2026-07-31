"""Shared isolation for the runtime tests.

Creating a session now mints a durable conversation (FEAT-015), so without
this every test that starts one would leave a transcript in the developer's
real ``condor/.runtime/conversations/``.
"""

import pytest

from condor.runtime import conversations


@pytest.fixture(autouse=True)
def isolated_conversation_root(tmp_path, monkeypatch):
    """Point the conversation store at a throwaway directory.

    Stubs ``_root()`` rather than ``_DATA_ROOT``: the tests that need the real
    agents root (agent binding, strategy state) must keep it.
    """
    root = tmp_path / "conversations"
    monkeypatch.setattr(conversations, "_root", lambda: root)
    monkeypatch.setattr(conversations, "_live_recorders", set())
    return root

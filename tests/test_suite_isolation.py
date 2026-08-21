"""The suite must not write into the developer's live install (FEAT-051).

This is the wound that motivated the one-root refactor: every store derived its
own root, so isolating one meant remembering to monkeypatch a private function
in every module that touched it — and the modules that forgot left 862 stub
conversations and a stream of test delegations inside a running install.

Asserting on "nothing appeared on disk after the run" would be flaky (the real
bot may be running while the suite is), so what is pinned here is the mechanism
instead: with the autouse fixtures in ``conftest.py`` active, every path a
writer can build resolves outside the repository. If someone reintroduces a
root that ignores ``CONDOR_RUNTIME_ROOT``, one of these fails.
"""

from pathlib import Path

from condor import notifications, paths
from condor.agents.delegate import DelegateTask, _record_dir

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


def test_the_notification_bell_is_isolated_too():
    assert _outside_the_repo(Path(notifications._FILE))

"""The advisory lock names who holds it (C16).

``start`` refuses a second run on the same checkout and says which process is
already updating. It could only ever say that if the pid survives being read
by the process that lost the race.
"""

from __future__ import annotations

import fcntl
import os

import pytest

from condor.paths import runtime_root
from condor.updates.run import _acquire_run_lock, _release_run_lock


def test_the_loser_of_the_race_can_still_read_the_holders_pid():
    """Opening ``"w"`` truncated the file before ``flock`` was even attempted.

    So the contender erased the pid on its way to being refused, ``start``
    read an empty file, and the "(pid N)" clause was dropped from every
    conflict message — and anyone inspecting ``update.lock`` afterwards found
    it empty too.
    """
    handle = _acquire_run_lock()
    assert handle is not None, "nothing else should hold the lock in a test"
    path = runtime_root() / "update.lock"
    try:
        assert path.read_text().strip() == str(os.getpid())

        # Exactly what a second process does: open the same path, then fail to
        # take the lock. The open must not destroy what the holder wrote.
        contender = path.open("a+")
        try:
            raised = False
            try:
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raised = True
            assert raised, "the contender should not have got the lock"
        finally:
            contender.close()

        assert path.read_text().strip() == str(
            os.getpid()
        ), "the contender's open truncated the holder's pid"
    finally:
        _release_run_lock(handle)


def test_the_lock_is_rewritten_rather_than_appended():
    """``"a+"`` must not leave a run's pid stacked under the previous one."""
    first = _acquire_run_lock()
    assert first is not None
    _release_run_lock(first)
    second = _acquire_run_lock()
    assert second is not None
    try:
        assert (runtime_root() / "update.lock").read_text().strip() == str(os.getpid())
    finally:
        _release_run_lock(second)


def test_a_failure_before_the_task_exists_releases_the_lock():
    """`_execute`'s finally is the only release, and there is no `_execute` yet.

    `check()` shells out to git and docker, so it can raise for reasons that
    have nothing to do with this checkout — and the lock was then held by a
    process that was not updating, locking out every other process on the
    checkout until it exited.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from condor.updates import run as run_module

    with patch.object(
        run_module.components, "check", AsyncMock(side_effect=OSError("no git"))
    ):
        with pytest.raises(OSError):
            asyncio.run(run_module.start(["condor"]))

    assert run_module._lock_handle is None, "the lock outlived the failed setup"
    # And the next attempt is not refused by our own stale handle.
    assert _acquire_run_lock() is not None
    _release_run_lock(_acquire_run_lock())

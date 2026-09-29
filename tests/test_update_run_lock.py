"""The advisory lock names who holds it (C16).

``start`` refuses a second run on the same checkout and says which process is
already updating. It could only ever say that if the pid survives being read
by the process that lost the race.
"""

from __future__ import annotations

import fcntl
import os

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

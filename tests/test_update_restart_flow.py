"""The /update pipeline: rebuild what the pull invalidated, restart cleanly.

Two things used to be missing from an in-place update. The dashboard bundle was
never rebuilt — ``make run`` builds it before starting, but /update pulls and
re-execs, so pulled frontend commits kept serving the previous ``frontend/dist``.
And the restart was a bare ``os.execv`` from inside the handler, which dropped
the process image before ``teardown()`` could flush persistence or reap ACP/MCP
subprocesses. These tests pin both: the build runs exactly when it is needed,
and a restart request goes through the normal shutdown path.
"""

import asyncio
import os
import signal
from unittest.mock import AsyncMock, patch

import pytest

import handlers.admin.update as update_handler
from utils import updater


@pytest.fixture(autouse=True)
def _reset_restart_flag():
    """Keep the module-level restart flag from leaking between tests."""
    updater._restart_pending = False
    yield
    updater._restart_pending = False


class FakeQuery:
    """Collects what the admin would have seen, in order."""

    def __init__(self):
        self.messages = []

    async def edit_message_text(self, text, parse_mode=None, reply_markup=None):
        self.messages.append(text)


def _stub_pipeline(**overrides):
    """Patch every step of the Condor update; each override replaces one step."""
    steps = dict(
        get_local_commit_full=AsyncMock(side_effect=["old_sha", "new_sha"]),
        pull_updates=AsyncMock(return_value=(True, "3 files changed")),
        install_dependencies=AsyncMock(return_value=(True, "Resolved 120 packages")),
        frontend_needs_build=AsyncMock(return_value=True),
        build_frontend=AsyncMock(return_value=(True, "built in 297ms")),
    )
    steps.update(overrides)
    return patch.multiple("utils.updater", **steps), steps


# ---------------------------------------------------------------------------
# What needs rebuilding
# ---------------------------------------------------------------------------


def _fake_fs(has_frontend=True, has_bundle=True):
    """Pretend frontend/ and frontend/dist/index.html do (not) exist."""
    real_isdir, real_isfile = os.path.isdir, os.path.isfile

    def isdir(path):
        if path == updater.FRONTEND_DIR:
            return has_frontend
        return real_isdir(path)

    def isfile(path):
        if path.endswith(os.path.join("dist", "index.html")):
            return has_bundle
        return real_isfile(path)

    return patch.multiple("os.path", isdir=isdir, isfile=isfile)


def test_frontend_rebuilds_when_the_pull_touched_it():
    with (
        _fake_fs(),
        patch.object(
            updater, "_run_git", AsyncMock(return_value=(0, "frontend/src/App.tsx"))
        ),
    ):
        assert asyncio.run(updater.frontend_needs_build("old_sha", "new_sha")) is True


def test_frontend_skipped_when_the_pull_left_it_alone():
    with _fake_fs(), patch.object(updater, "_run_git", AsyncMock(return_value=(0, ""))):
        assert asyncio.run(updater.frontend_needs_build("old_sha", "new_sha")) is False


def test_frontend_builds_when_the_bundle_is_missing_entirely():
    """A fresh clone has no dist/, so build it even though nothing changed."""
    with (
        _fake_fs(has_bundle=False),
        patch.object(updater, "_run_git", AsyncMock(return_value=(0, ""))) as git,
    ):
        assert asyncio.run(updater.frontend_needs_build("same", "same")) is True
        git.assert_not_awaited()  # short-circuits before diffing


def test_no_frontend_directory_means_nothing_to_build():
    with _fake_fs(has_frontend=False):
        assert asyncio.run(updater.frontend_needs_build("old_sha", "new_sha")) is False


def test_unresolvable_diff_assumes_changed():
    """Shallow clone or rewritten history: redo the work rather than skip it."""
    with patch.object(updater, "_run_git", AsyncMock(return_value=(128, "bad object"))):
        assert asyncio.run(updater.paths_changed("old", "new", "frontend")) is True


def test_identical_commits_change_nothing():
    with patch.object(updater, "_run_git", AsyncMock()) as git:
        assert asyncio.run(updater.paths_changed("same", "same", "frontend")) is False
        git.assert_not_awaited()


# ---------------------------------------------------------------------------
# The update pipeline
# ---------------------------------------------------------------------------


def test_update_pulls_syncs_builds_then_reports_ready():
    ctx, steps = _stub_pipeline()
    query = FakeQuery()
    with ctx:
        assert asyncio.run(update_handler._update_condor(query)) is True

    steps["pull_updates"].assert_awaited_once()
    steps["install_dependencies"].assert_awaited_once()
    steps["build_frontend"].assert_awaited_once()
    # The diff is taken across the pull, not against an arbitrary commit.
    steps["frontend_needs_build"].assert_awaited_once_with("old_sha", "new_sha")


def test_update_does_not_build_when_the_frontend_is_untouched():
    ctx, steps = _stub_pipeline(frontend_needs_build=AsyncMock(return_value=False))
    with ctx:
        assert asyncio.run(update_handler._update_condor(FakeQuery())) is True
    steps["build_frontend"].assert_not_awaited()


def test_failed_pull_stops_before_touching_dependencies():
    ctx, steps = _stub_pipeline(
        pull_updates=AsyncMock(return_value=(False, "uncommitted changes"))
    )
    query = FakeQuery()
    with ctx:
        assert asyncio.run(update_handler._update_condor(query)) is False

    steps["install_dependencies"].assert_not_awaited()
    steps["build_frontend"].assert_not_awaited()
    assert "uncommitted changes" in query.messages[-1]


def test_failed_build_blocks_the_restart():
    """Restarting here would bring the dashboard back on the previous bundle."""
    ctx, steps = _stub_pipeline(
        build_frontend=AsyncMock(return_value=(False, "TS2304: Cannot find name"))
    )
    query = FakeQuery()
    with ctx:
        assert asyncio.run(update_handler._update_condor(query)) is False

    assert "TS2304" in query.messages[-1]


def test_command_output_is_trimmed_for_telegram():
    """Build logs run to thousands of lines; only the tail is worth sending."""
    trimmed = update_handler._tail("\n".join(str(i) for i in range(500)))
    assert trimmed.startswith("...")
    assert trimmed.endswith("499")
    assert len(trimmed.split("\n")) <= 16


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------


def test_restart_request_signals_shutdown_instead_of_exec():
    """Exec'ing from the handler would skip teardown: flush, reap, final state."""
    with patch.object(os, "kill") as kill, patch.object(os, "execv") as execv:
        updater.request_restart()

    assert updater.restart_pending() is True
    kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
    execv.assert_not_called()


def test_nothing_restarts_unless_asked():
    assert updater.restart_pending() is False


def test_exec_restart_replaces_the_process_in_place():
    """In-place exec is what keeps Condor in the tmux pane it was started in."""
    with patch.object(os, "execv") as execv, patch.object(os, "chdir") as chdir:
        updater.exec_restart()

    chdir.assert_called_once_with(updater.CONDOR_DIR)
    python, argv = execv.call_args[0]
    assert argv[0] == python  # same interpreter, same argv — not a new command


def test_hung_step_is_killed_rather_than_waited_on():
    rc, output = asyncio.run(updater._run_cmd("bash", "-c", "sleep 30", timeout=0.5))
    assert rc == 124
    assert "Timed out" in output

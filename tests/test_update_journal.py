"""The durable record of an update, and how a restart is judged.

The process that starts a Condor update is the process that dies, so no live
transport can report the outcome — the interesting part happens in the gap. The
journal is the contract instead: every step transition is one atomic write, and
the run left at ``restarting`` is resolved at boot by asking HEAD whether it is
the commit the update aimed at.

``$CONDOR_DATA_DIR`` is already pointed at ``tmp_path`` by the suite's autouse
fixture, so the journal these tests write is never the developer's own.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from condor import paths
from condor.updates import components
from condor.updates import run as run_module


@pytest.fixture(autouse=True)
def _no_live_run():
    """The engine's single-run state is process-wide; do not leak it."""
    run_module._current = None
    run_module._task = None
    run_module._relaunch = None
    run_module._observers.clear()
    components.invalidate()
    yield
    run_module._current = None
    run_module._task = None
    run_module._relaunch = None
    run_module._observers.clear()
    components.invalidate()


def _journal() -> dict:
    return json.loads(paths.update_run_path().read_text())


def _a_run(**kw) -> run_module.Run:
    defaults = dict(
        id="u-1756300000",
        started=1756300000.0,
        actor={"user_id": 1, "chat_id": 1},
        components=[components.CONDOR],
        steps=[run_module.Step("condor.fast-forward", "Fast-forwarding Condor")],
    )
    defaults.update(kw)
    return run_module.Run(**defaults)


# ---------------------------------------------------------------------------
# Every transition is on disk
# ---------------------------------------------------------------------------


def test_a_step_transition_is_written_before_anybody_is_told():
    """The journal is the record; an observer that blew up must not cost it."""
    run = _a_run()
    seen = []

    async def observer(r):
        seen.append(r.step("condor.fast-forward").state)
        raise RuntimeError("this surface is gone")

    run_module.register_observer(observer)
    asyncio.run(run_module._begin(run, "condor.fast-forward"))

    assert seen == ["running"]
    assert _journal()["steps"][0]["state"] == "running"


def test_a_finished_step_records_its_output_tail():
    run = _a_run()

    async def go():
        step = await run_module._begin(run, "condor.fast-forward")
        await run_module._finish(run, step, run_module.OK, "Updating 73e5400..0ed2a0f")

    asyncio.run(go())
    step = _journal()["steps"][0]
    assert step["state"] == "ok"
    assert "0ed2a0f" in step["output_tail"]
    assert step["ended"] >= step["started"]


def test_output_is_trimmed_before_it_is_journaled():
    """Build logs run to thousands of lines; only the tail is worth keeping."""
    trimmed = run_module.tail("\n".join(str(i) for i in range(500)))
    assert trimmed.startswith("...")
    assert trimmed.endswith("499")
    assert len(trimmed) <= run_module.OUTPUT_TAIL_CHARS + 3


def test_a_failed_run_records_why():
    run = _a_run()
    asyncio.run(run_module._fail(run, "Condor could not be fast-forwarded."))

    journaled = _journal()
    assert journaled["state"] == "failed"
    assert journaled["error"] == "Condor could not be fast-forwarded."
    assert run.done.is_set()


def test_a_junk_journal_reads_as_no_run():
    paths.update_run_path().parent.mkdir(parents=True, exist_ok=True)
    paths.update_run_path().write_text("{not json")
    assert run_module.read_journal() is None


def test_a_journaled_run_round_trips():
    run = _a_run(target_commit="0ed2a0f" * 5, state=run_module.RESTARTING)
    run_module._write_journal(run)

    restored = run_module.read_journal()
    assert restored is not None
    assert restored.id == run.id
    assert restored.state == run_module.RESTARTING
    assert restored.target_commit == run.target_commit
    assert [s.key for s in restored.steps] == ["condor.fast-forward"]
    assert restored.live is True


# ---------------------------------------------------------------------------
# Boot: did it work?
# ---------------------------------------------------------------------------

TARGET = "0ed2a0f1234567890abcdef1234567890abcdef1"
OTHER = "73e5400aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _finalize_after(head: str, **run_kw):
    run_module._write_journal(_a_run(**run_kw))
    with patch.object(
        run_module.updater, "get_local_commit_full", AsyncMock(return_value=head)
    ):
        return asyncio.run(run_module.finalize_pending_run())


def test_coming_back_on_the_target_commit_is_a_success():
    run = _finalize_after(
        TARGET,
        state=run_module.RESTARTING,
        target_commit=TARGET,
        steps=[run_module.Step("condor.restart", "Restarting Condor", state="running")],
    )
    assert run is not None
    assert run.state == "succeeded"
    assert run.steps[0].state == "ok"
    assert _journal()["state"] == "succeeded"


def test_coming_back_on_a_different_commit_is_a_failure():
    """The restart happened; the new code did not. Say which sha came up."""
    run = _finalize_after(
        OTHER,
        state=run_module.RESTARTING,
        target_commit=TARGET,
        steps=[run_module.Step("condor.restart", "Restarting Condor", state="running")],
    )
    assert run is not None
    assert run.state == "failed"
    assert "73e5400" in run.error and "0ed2a0f" in run.error
    assert run.steps[0].state == "failed"


def test_a_run_that_was_not_restarting_is_left_alone():
    assert _finalize_after(TARGET, state=run_module.SUCCEEDED) is None
    assert _finalize_after(TARGET, state=run_module.FAILED) is None


def test_no_journal_at_all_finalizes_nothing():
    with patch.object(
        run_module.updater, "get_local_commit_full", AsyncMock(return_value=TARGET)
    ):
        assert asyncio.run(run_module.finalize_pending_run()) is None


# ---------------------------------------------------------------------------
# One run at a time
# ---------------------------------------------------------------------------


def test_a_second_start_joins_the_run_already_in_flight():
    """Two surfaces mean two watchers, not two updates."""

    async def go():
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_execute(run, resolutions):
            started.set()
            await release.wait()
            run.state = run_module.SUCCEEDED
            run.done.set()

        with (
            patch.object(run_module, "_execute", slow_execute),
            patch.object(components, "check", AsyncMock(return_value=[])),
            patch.object(components, "keys", lambda: [components.CONDOR]),
        ):
            first = await run_module.start([components.CONDOR], actor_user_id=1)
            await started.wait()
            second = await run_module.start([components.CONDOR], actor_user_id=2)
            assert second is first
            assert second.actor["user_id"] == 1  # the run kept its own actor
            release.set()
            await first.done.wait()

            # Once it is finished, a new start is a new run.
            third = await run_module.start([components.CONDOR], actor_user_id=3)
            assert third is not first

    asyncio.run(go())


def test_the_plan_is_laid_out_before_anything_runs():
    """The confirm screen and the progress screen are the same list."""
    status = components.ComponentStatus(
        key=components.HUMMINGBOT_API,
        name="Hummingbot API",
        facets={},
        mode="image",
    )
    steps = run_module._plan(
        [components.HUMMINGBOT_API, components.CONDOR],
        {components.HUMMINGBOT_API: status},
    )
    keys = [s.key for s in steps]
    # Condor last, and it stops with the code on disk: there is no restart step
    # because the engine never restarts the process (it asks instead).
    assert keys == [
        "hummingbot-api.fast-forward",
        "hummingbot-api.image",
        "hummingbot-api.up",
        "hummingbot-api.health",
        "condor.fast-forward",
        "condor.deps",
        "condor.frontend",
    ]
    assert all(s.state == "pending" for s in steps)
    assert "Pulling" in steps[1].label


def test_source_mode_builds_instead_of_pulling():
    status = components.ComponentStatus(
        key=components.HUMMINGBOT_API, name="x", facets={}, mode="source"
    )
    steps = run_module._plan(
        [components.HUMMINGBOT_API], {components.HUMMINGBOT_API: status}
    )
    assert "Rebuilding" in steps[1].label


# ---------------------------------------------------------------------------
# Resolutions act on exactly what blocked
# ---------------------------------------------------------------------------


def test_a_resolution_recomputes_the_paths_it_is_about_to_destroy():
    """The screen the admin pressed may be minutes old."""
    block = components.Block(
        component=components.CONDOR,
        code="dirty-conflict",
        message="",
        paths=["environment.yml"],
        resolutions=["discard", "stash", "cancel"],
    )
    discard = AsyncMock(return_value=(True, "Restored 1 tracked file."))
    with (
        patch.object(components, "repo_blocks", AsyncMock(return_value=[block])),
        patch.object(run_module.updater, "discard_paths", discard),
    ):
        ok, message = asyncio.run(run_module.resolve(components.CONDOR, "discard"))

    assert ok and "Restored" in message
    assert discard.await_args[0][1] == ["environment.yml"]


def test_a_resolution_with_nothing_left_to_do_is_not_an_error():
    with patch.object(components, "repo_blocks", AsyncMock(return_value=[])):
        ok, message = asyncio.run(run_module.resolve(components.CONDOR, "discard"))
    assert ok and "Nothing conflicts" in message


def test_a_blocker_that_offers_no_such_resolution_is_not_acted_on():
    """``diverged`` offers only cancel; discard must not touch the tree."""
    block = components.Block(
        component=components.CONDOR,
        code="diverged",
        message="",
        paths=[],
        resolutions=["cancel"],
    )
    discard = AsyncMock()
    with (
        patch.object(components, "repo_blocks", AsyncMock(return_value=[block])),
        patch.object(run_module.updater, "discard_paths", discard),
    ):
        ok, _ = asyncio.run(run_module.resolve(components.CONDOR, "discard"))
    assert ok
    discard.assert_not_awaited()


# ---------------------------------------------------------------------------
# The update stops one step short, on purpose
# ---------------------------------------------------------------------------

BEFORE = "73e5400aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AFTER = "0ed2a0f1234567890abcdef1234567890abcdef1"


def _run_condor_update(*, before: str, after: str) -> run_module.Run:
    """Drive ``_update_condor`` with every shell-out stubbed out."""
    run = _a_run(
        steps=run_module._plan([components.CONDOR], {}),
        components=[components.CONDOR],
    )
    commits = iter([before, after])
    with (
        patch.object(components, "repo_blocks", AsyncMock(return_value=[])),
        patch.object(
            run_module.updater,
            "get_local_commit_full",
            AsyncMock(side_effect=lambda *a, **k: next(commits)),
        ),
        patch.object(
            run_module.updater, "fast_forward", AsyncMock(return_value=(True, "ok"))
        ),
        patch.object(
            run_module.updater,
            "install_dependencies",
            AsyncMock(return_value=(True, "ok")),
        ),
        patch.object(
            run_module.updater,
            "frontend_needs_build",
            AsyncMock(return_value=False),
        ),
        patch.object(
            run_module.updater, "get_current_branch", AsyncMock(return_value="main")
        ),
        patch.object(run_module.updater, "request_restart") as restart,
    ):
        assert asyncio.run(run_module._update_condor(run)) is True
        restart.assert_not_called()
    return run


def test_a_condor_update_never_restarts_the_process():
    """Condor is rarely the top of its own process tree; exec'ing races it.

    ``request_restart`` not being called is the whole feature — the assertion
    lives in the helper because every test here depends on it.
    """
    run = _run_condor_update(before=BEFORE, after=AFTER)
    assert [s.key for s in run.steps][-1] == "condor.frontend"
    assert run.target_commit == AFTER


def test_a_landed_update_records_the_relaunch_it_owes():
    _run_condor_update(before=BEFORE, after=AFTER)

    pending = run_module.relaunch_pending()
    assert pending is not None
    assert pending["from_commit"] == BEFORE
    assert pending["target_commit"] == AFTER
    assert pending["branch"] == "main"


def test_an_update_that_moved_nothing_owes_no_relaunch():
    """Re-running an update at HEAD must not ask for a pointless restart."""
    _run_condor_update(before=BEFORE, after=BEFORE)
    assert run_module.relaunch_pending() is None


def test_the_relaunch_notice_is_never_journaled():
    """It asks whether this *process* is stale, and a journal outlives it.

    Written to disk, the banner would survive the very relaunch it asked for.
    """
    run = _run_condor_update(before=BEFORE, after=AFTER)
    run_module._write_journal(run)
    assert "relaunch" not in json.dumps(_journal())

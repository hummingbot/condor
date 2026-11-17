"""One Condor per runtime root (OP-01).

A second ``python main.py`` used to run the ACP reaper, boot reconciliation and
polling against the live instance's state before it failed on the port bind —
and then skipped every shutdown step, because uvicorn reports a failed bind
with ``sys.exit(1)`` and ``SystemExit`` is not an ``Exception``.
"""

import asyncio
import subprocess
import sys
import textwrap

import pytest

import main
from condor import paths


@pytest.fixture(autouse=True)
def _no_held_lock(monkeypatch):
    """Each test starts as a process that has not taken the lock yet."""
    monkeypatch.setattr(main, "_instance_lock_handle", None)
    yield
    handle = main._instance_lock_handle
    if handle is not None:
        handle.close()


def _hold_lock_elsewhere(lock_path):
    """A second process holding the lock, ready once it has printed a line."""
    code = textwrap.dedent("""
        import fcntl, os, sys
        handle = open(sys.argv[1], "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(f"{os.getpid()}\\n")
        handle.flush()
        print("held", flush=True)
        sys.stdin.read()
        """)
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_the_lock_is_taken_and_records_the_pid():
    assert main._acquire_instance_lock() is True
    lock_path = paths.runtime_root() / main._INSTANCE_LOCK_FILENAME
    assert lock_path.read_text().strip().isdigit()
    # Asking again in the same process is not a second instance.
    assert main._acquire_instance_lock() is True


def test_main_exits_before_the_reaper_when_another_instance_holds_the_lock(
    monkeypatch,
):
    lock_path = paths.runtime_root() / main._INSTANCE_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    reached = []
    import condor.acp.client as acp_client

    monkeypatch.setattr(
        acp_client, "reap_stale_acp_trees", lambda *a, **k: reached.append("reaper")
    )
    monkeypatch.setattr(
        main, "check_startup_config", lambda: reached.append("startup-config")
    )

    holder = _hold_lock_elsewhere(lock_path)
    try:
        with pytest.raises(SystemExit) as exc:
            main.main()
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)

    assert exc.value.code == 1
    assert reached == []
    # The holder's pid survives the refused attempt: it is what the error names.
    assert lock_path.read_text().strip() == str(holder.pid)


def test_the_lock_frees_when_its_holder_exits():
    lock_path = paths.runtime_root() / main._INSTANCE_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = _hold_lock_elsewhere(lock_path)
    assert main._acquire_instance_lock() is False
    holder.stdin.close()
    holder.wait(timeout=10)
    assert main._acquire_instance_lock() is True


@pytest.mark.parametrize("raised", [SystemExit(1), RuntimeError("boom")])
def test_a_web_task_that_died_does_not_skip_the_shutdown_steps(raised):
    """uvicorn's failed bind is ``sys.exit(1)``; the steps after it must still run."""

    async def scenario():
        web_task = asyncio.get_running_loop().create_future()
        web_task.set_exception(raised)
        await main._drain_web_task(web_task)
        return "steps-run"

    assert asyncio.run(scenario()) == "steps-run"

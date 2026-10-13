"""Agent-written code cannot freeze or stop the main process (code_guard).

Each case here is a way a snippet used to take all of Condor down with it — the
2026-10-07 freeze was a recursive glob from ``/`` that walked ``/sys`` on the
main thread for hours — and the property pinned is the same every time: the
snippet fails on its own line, the run is recorded, and the event loop is still
there afterwards.

Sync tests driving coroutines with ``asyncio.run`` (``pytest-asyncio`` is not
installed in this venv). A ``SystemExit`` escaping a snippet would end
``asyncio.run`` and fail the test with it, which is the point of those cases.
"""

import asyncio
import builtins
import logging
import os
import time

import pytest

from condor import code_runner
from condor.code_runs import CodeRunStore
from condor.runtime import code_guard, loop_watchdog


@pytest.fixture
def store(tmp_path, monkeypatch):
    written = CodeRunStore(tmp_path / "code_runs")
    monkeypatch.setattr(code_runner, "get_code_run_store", lambda: written)

    import config_manager

    async def _client(*a, **kw):
        return None

    monkeypatch.setattr(config_manager, "get_client", _client)
    return written


def run_alongside(code: str, timeout: float = 1.0) -> tuple[dict, int]:
    """Run a snippet next to a ticking task; return the run and the ticks seen.

    Ticks after the run prove the loop survived it — a frozen or stopped loop
    never comes back to them.
    """

    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        out = await code_runner.execute_code(code, timeout=timeout)
        before = ticks
        await asyncio.sleep(0.1)
        task.cancel()
        return out, ticks - before

    return asyncio.run(main())


# ── the deadline interrupts synchronous code ──


@pytest.mark.parametrize(
    "code",
    [
        "while True:\n    pass\n",
        "def spin():\n    n = 0\n    while True:\n        n += 1\n\nspin()\n",
        "[x for x in iter(int, 1)]\n",
    ],
    ids=["bare-loop", "loop-in-function", "endless-comprehension"],
)
def test_a_pure_cpu_loop_is_cut_at_its_budget(store, code):
    started = time.monotonic()
    out, ticks_after = run_alongside(code, timeout=1)

    assert out["status"] == "timeout"
    assert "Timed out after 1s" in out["error"]
    assert 'File "<code_run>"' in out["traceback"]
    assert time.monotonic() - started < 3
    assert ticks_after > 0


def test_a_syscall_loop_is_cut_even_without_line_monitoring(store, monkeypatch):
    """The audit hook alone stops it — the guard for library code the snippet calls."""
    monkeypatch.setattr(code_guard, "_tool_id", None)

    out, ticks_after = run_alongside("import os\nwhile True:\n    os.listdir('.')\n")

    assert out["status"] == "timeout"
    assert "interrupted at os.listdir" in out["error"]
    assert ticks_after > 0


def test_output_printed_before_the_cut_is_kept(store):
    out, _ = run_alongside("print('started')\nwhile True:\n    pass\n")

    assert out["status"] == "timeout"
    assert out["stdout"] == "started\n"


def test_a_task_the_snippet_left_running_is_stopped_with_it(store):
    """A spawned task carries the run's scope and dies with the run."""
    code = (
        "import builtins\n"
        "async def forever():\n"
        "    while True:\n"
        "        await asyncio.sleep(0.01)\n"
        "builtins._guard_orphan = asyncio.create_task(forever())\n"
    )

    async def main():
        out = await code_runner.execute_code("import asyncio\n" + code, timeout=5)
        await asyncio.sleep(0.1)
        return out, builtins._guard_orphan

    try:
        out, orphan = asyncio.run(main())
    finally:
        vars(builtins).pop("_guard_orphan", None)

    assert out["status"] == "ok"
    assert orphan.done()
    assert isinstance(orphan.exception(), code_guard.CodeRunDeadline)


_CLEANUP_SHAPES = {
    "finally": (
        "await lock.acquire()\n"
        "try:\n"
        "    while True:\n"
        "        pass\n"
        "finally:\n"
        "    lock.release()\n"
        "    print('cleanup ran')\n"
    ),
    "one-line-loop": (
        "await lock.acquire()\n"
        "try:\n"
        "    while True: pass\n"
        "finally:\n"
        "    lock.release()\n"
        "    print('cleanup ran')\n"
    ),
    "loop-in-function": (
        "def spin():\n"
        "    n = 0\n"
        "    while True:\n"
        "        n += 1\n"
        "await lock.acquire()\n"
        "try:\n"
        "    spin()\n"
        "finally:\n"
        "    lock.release()\n"
        "    print('cleanup ran')\n"
    ),
    "except": (
        "await lock.acquire()\n"
        "try:\n"
        "    while True:\n"
        "        pass\n"
        "except BaseException:\n"
        "    lock.release()\n"
        "    print('cleanup ran')\n"
        "    raise\n"
    ),
    "with": (
        "import contextlib\n"
        "@contextlib.contextmanager\n"
        "def held():\n"
        "    try:\n"
        "        yield\n"
        "    finally:\n"
        "        lock.release()\n"
        "        print('cleanup ran')\n"
        "await lock.acquire()\n"
        "with held():\n"
        "    while True:\n"
        "        pass\n"
    ),
}


@pytest.mark.parametrize("shape", list(_CLEANUP_SHAPES))
def test_cleanup_still_runs_after_a_timeout(store, shape):
    """Code handling the interruption gets to run: a lock it holds is released."""
    code = "import builtins\nlock = builtins._guard_lock\n" + _CLEANUP_SHAPES[shape]

    async def main():
        builtins._guard_lock = asyncio.Lock()
        out = await code_runner.execute_code(code, timeout=1)
        return out, builtins._guard_lock.locked()

    try:
        out, still_locked = asyncio.run(main())
    finally:
        vars(builtins).pop("_guard_lock", None)

    assert out["status"] == "timeout"
    assert out["stdout"] == "cleanup ran\n"
    assert not still_locked


def test_a_cleanup_that_hangs_is_cut_after_its_grace(store):
    code = (
        "try:\n    while True:\n        pass\nfinally:\n    while True:\n        pass\n"
    )
    started = time.monotonic()
    out, ticks_after = run_alongside(code, timeout=1)

    assert out["status"] == "timeout"
    assert time.monotonic() - started < 1 + code_guard.INTERRUPT_GRACE + 2
    assert ticks_after > 0


def test_a_snippet_that_swallows_the_interruption_is_still_cut(store):
    """The cleanup window is granted once, not per interruption."""
    code = (
        "while True:\n"
        "    try:\n"
        "        while True:\n"
        "            pass\n"
        "    except BaseException:\n"
        "        pass\n"
    )
    started = time.monotonic()
    out, ticks_after = run_alongside(code, timeout=1)

    assert out["status"] == "timeout"
    assert time.monotonic() - started < 1 + code_guard.INTERRUPT_GRACE + 2
    assert ticks_after > 0


def test_a_background_routine_outlives_the_snippet_that_started_it(store, monkeypatch):
    """start_routine is a real background run: no snippet deadline, no orphan cut."""
    from pydantic import BaseModel

    from condor import routine_store
    from condor.routine_store import RoutineStore
    from routines.base import RoutineInfo

    class Config(BaseModel):
        """A test routine."""

    async def slow(config, context):
        await asyncio.sleep(1.5)  # past the 1s snippet budget, and its expiry
        os.listdir(".")  # an audited call: the guard would cut it here
        return "finished"

    routines = RoutineStore()
    monkeypatch.setattr(routine_store, "_store", routines)
    monkeypatch.setattr(
        routine_store,
        "get_routine",
        {"slow": RoutineInfo(name="slow", config_class=Config, run_fn=slow)}.get,
    )

    async def _quiet(self, *a, **kw):
        return None

    monkeypatch.setattr(RoutineStore, "_fire_hooks", _quiet)
    monkeypatch.setattr(RoutineStore, "_report_run", _quiet)

    async def main():
        out = await code_runner.execute_code(
            "from condor import primitives\n"
            "result = await primitives.start_routine('slow')\n",
            timeout=1,
        )
        for _ in range(100):
            meta = routines.get_instance(out["result"])
            if meta and meta["status"] != "running":
                return out, meta
            await asyncio.sleep(0.05)
        return out, routines.get_instance(out["result"])

    out, meta = asyncio.run(main())

    assert out["status"] == "ok"
    assert meta["status"] == "completed"
    assert "finished" in str(meta.get("result_summary") or meta)


# ── the freeze that started this ──


@pytest.mark.parametrize(
    "code",
    [
        "import glob\nglob.glob('/**/_theta_derive.py', recursive=True)\n",
        "import pathlib\nlist(pathlib.Path('/').rglob('_theta_derive.py'))\n",
        "import os\nfor _ in os.walk('/'):\n    pass\n",
        "import os\nos.listdir('/proc')\n",
        "import os\nos.listdir('/sys/class')\n",
    ],
    ids=["glob-from-root", "rglob-from-root", "walk-from-root", "proc", "sys"],
)
def test_a_filesystem_search_from_the_root_is_refused_at_once(store, code):
    started = time.monotonic()
    out, ticks_after = run_alongside(code, timeout=60)

    assert out["status"] == "error"
    assert "GuardViolation" in out["error"]
    assert "exact path" in out["error"]
    assert time.monotonic() - started < 2
    assert ticks_after > 0


def test_listing_an_ordinary_directory_still_works(store, tmp_path):
    (tmp_path / "a.txt").write_text("x")

    out, _ = run_alongside(f"import os\nresult = os.listdir({str(tmp_path)!r})\n")

    assert out["status"] == "ok"
    assert "a.txt" in out["result"]


# ── ways to stop the process ──


@pytest.mark.parametrize(
    "code",
    [
        "exit()\n",
        "import sys\nsys.exit(0)\n",
        "raise SystemExit(3)\n",
        "raise KeyboardInterrupt\n",
    ],
)
def test_exiting_is_an_error_not_a_shutdown(store, code):
    out, ticks_after = run_alongside(code)

    assert out["status"] == "error"
    assert "would stop Condor" in out["error"]
    assert ticks_after > 0


@pytest.mark.parametrize(
    "code",
    [
        "import os\nos._exit(0)\n",
        "from os import _exit\n_exit(0)\n",
        "import os\nif False:\n    os.abort()\n",
        "import signal\nsignal.raise_signal(signal.SIGTERM)\n",
    ],
)
def test_a_hard_exit_is_refused_before_anything_runs(store, code):
    out, _ = run_alongside("print('ran')\n" + code)

    assert out["status"] == "error"
    assert out["error"].startswith("Refused:")
    assert out["stdout"] == ""


def test_signalling_condor_itself_is_refused(store):
    out, _ = run_alongside("import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n")

    assert out["status"] == "error"
    assert "own process" in out["error"]


# ── ways to stall the loop ──


def test_a_long_blocking_sleep_is_refused(store):
    started = time.monotonic()
    out, _ = run_alongside("import time\ntime.sleep(30)\n", timeout=60)

    assert out["status"] == "error"
    assert "asyncio.sleep" in out["error"]
    assert time.monotonic() - started < 2


def test_a_short_blocking_sleep_is_allowed(store):
    out, _ = run_alongside("import time\ntime.sleep(0.01)\nresult = 'slept'\n")

    assert out["status"] == "ok"
    assert out["result"] == "slept"


def test_input_is_refused(store):
    out, _ = run_alongside("input('continue? ')\n")

    assert out["status"] == "error"
    assert "input()" in out["error"]


def test_help_prints_instead_of_starting_a_pager(store):
    out, _ = run_alongside("import sys\nresult = sys.stdout.isatty()\nhelp(len)\n")

    assert out["status"] == "ok"
    assert out["result"] == "False"
    assert "len" in out["stdout"]


# ── nothing outside a guarded run is touched ──


def test_code_outside_a_run_is_unaffected(store):
    run_alongside("result = 1\n")  # the hooks are installed by now

    assert code_guard.current_scope() is None
    assert os.listdir("/")
    time.sleep(code_guard.MAX_BLOCKING_SLEEP + 0.05)  # would raise inside a run


def test_a_routine_inherits_the_deadline_of_the_snippet_calling_it():
    parent = code_guard.GuardScope("snippet", deadline=time.monotonic() + 5, budget=5)

    assert code_guard.routine_scope("free").deadline is None
    with code_guard.guarded(parent):
        nested = code_guard.routine_scope("called")
    assert nested.deadline == parent.deadline


# ── the watchdog names the stuck line ──


def test_the_watchdog_logs_where_the_loop_is_blocked(caplog):
    def block_the_loop_here():
        time.sleep(0.8)

    async def main():
        watchdog = loop_watchdog.start(stall_after=0.3)
        await asyncio.sleep(0.05)
        block_the_loop_here()
        await asyncio.sleep(1.5)
        watchdog.stop()

    with caplog.at_level(logging.WARNING, logger=loop_watchdog.__name__):
        asyncio.run(main())

    blocked = [r for r in caplog.records if "Event loop blocked" in r.getMessage()]
    assert len(blocked) == 1
    assert "block_the_loop_here" in blocked[0].getMessage()
    assert any("unblocked" in r.getMessage() for r in caplog.records)
